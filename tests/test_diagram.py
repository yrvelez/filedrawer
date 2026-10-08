"""Design diagrams: a template SVG for every standard design, a plain-text twin, the sanitizer for model SVG, and the
files written for a study and its extensions."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from filedrawer import diagram as D
from tests.test_conjoint import make_conjoint_pap
from tests.test_multiarm import make_pap

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://www.w3.org/2000/svg}"


def _parse(svg: str) -> ET.Element:
    root = ET.fromstring(svg)
    assert root.tag == NS + "svg" and root.get("viewBox", "").startswith("0 0 960 ")
    assert root.find(NS + "title") is not None and root.find(NS + "desc") is not None
    assert "--fd-ink" in svg and "prefers-color-scheme" in svg and "<script" not in svg.lower()
    return root


def _ids(root) -> set[str]:
    return {g.get("id") for g in root.iter(NS + "g") if g.get("id")}


def test_two_arm_demo_design():
    pap = json.loads((ROOT / "demo" / "pap.json").read_text()) if (ROOT / "demo" / "pap.json").exists() else None
    if pap is None:
        pap = make_pap()
    root = _parse(D.render(pap, {"n_analysis": 387}))
    ids = _ids(root)
    assert any(i.startswith("arm-") for i in ids)
    assert "randomized" in D.render(pap, {}) and "N = 387" in D.describe(pap, {"n_analysis": 387})


def test_multiarm_and_conjoint_and_methods_and_observational():
    multi = make_pap()
    root = _parse(D.render(multi, {}))
    assert len([i for i in _ids(root) if i.startswith("arm-")]) == 12            # control + 11 arms
    cj = make_conjoint_pap()
    svg = D.render(cj, {})
    root = _parse(svg)
    assert "arm-info-Misinfo" in _ids(root) and "AMCE" in svg and "measured" in svg      # tie/trust are measured in that fixture
    assert "Each profile is built from 3 features" in D.describe(cj, {})
    methods = {"design": {"type": "methods_comparison", "methods": ["LLM silicon sample", "Human YouGov sample"], "benchmark": "ANES 2020",
                          "metrics": ["mean error", "sign agreement"]}, "registered": {"outcomes": [], "hypotheses": [], "subgroups": []},
               "implemented": {"outcomes": [], "hypotheses": []}}
    svg = D.render(methods, {})
    assert "Benchmark" in svg and "ANES 2020" in svg and "discrepancy" in D.describe(methods, {}) or "benchmark" in D.describe(methods, {}).lower()
    obs = make_pap()
    obs["design"]["type"] = "observational_survey"
    svg = D.render(obs, {})
    assert 'class="edge assoc"' in svg and "observed" in svg and "association" in svg
    assert "Estimates are associations" in D.describe(obs, {})
    obs2 = {"design": {"type": "observational_survey", "arms": {}}, "registered": {"outcomes": [{"name": "y", "label": "Trust"}], "hypotheses": [], "subgroups": []},
            "implemented": {"outcomes": [{"name": "y", "label": "Trust"}], "hypotheses": [{"id": "H1", "outcome": "y", "treatment": {"column": "income", "continuous": True},
                                                                                           "estimator": {"covariates": ["age", "educ"]}}]}}
    svg = D.render(obs2, {})
    assert "arm-exposure" in _ids(_parse(svg)) and "Adjusted for" in svg and "age, educ" in svg


def test_nonstandard_and_synthetic():
    pap = make_pap()
    pap["design"]["nonstandard"] = True
    assert D.render(pap, {}) is None
    other = {"design": {"type": "other", "arms": {}}, "registered": {"outcomes": [], "hypotheses": [], "subgroups": []}, "implemented": {"outcomes": [], "hypotheses": []}}
    assert D.render(other, {}) is not None            # type other is associational: exposure -> outcome
    syn = make_pap()
    syn["design"].update(sample_kind="synthetic", synthetic={"model": "x/y"})
    assert "LLM-generated (x/y)" in D.describe(syn, {})


def test_sanitize_strips_dangerous_content():
    bad = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 100" onload="alert(1)"><script>alert(1)</script>'
           '<foreignObject><div>x</div></foreignObject><a href="https://evil.example"><rect x="0" y="0" width="10" height="10"/></a>'
           '<g id="arm-a" data-text="t"><text x="1" y="2">ok</text></g></svg>')
    out = D.sanitize(bad)
    assert out and "script" not in out.lower() and "foreignObject" not in out and "onload" not in out and "evil.example" not in out
    assert "arm-a" in out and ">ok<" in out
    assert D.sanitize("not svg at all") is None
    assert D.sanitize("<svg>" + "x" * 70000 + "</svg>") is None
    assert D.sanitize('<html><body><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg></body></html>') is not None


def test_write_study_and_extension_files(tmp_path):
    pap = make_pap()
    out = D.write_study_diagram(tmp_path, pap, {"n_analysis": 1800})
    assert out == {"svg": "figures/design.svg", "text": "figures/design.txt", "source": "template"}
    assert (tmp_path / "figures" / "design.svg").exists() and "N = 1,800" in (tmp_path / "figures" / "design.txt").read_text()
    pap["design"]["nonstandard"] = True
    out = D.write_study_diagram(tmp_path, pap, {}, fallback=lambda desc: f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 50"><title>t</title><desc>{desc[:20]}</desc><rect/></svg>')
    assert out["source"] == "model"
    out = D.write_study_diagram(tmp_path, pap, {}, fallback=lambda desc: "<script>x</script>")
    assert out["source"] is None and out["svg"] is None
    # extensions: a survey proposal and an observational plan
    (tmp_path / "extensions").mkdir()
    prop = json.loads((ROOT / "tests" / "fixtures" / "mock" / "extensions" / "0.json").read_text())["tool_calls"][0]["args"]["value"]
    prop["id"] = "advance_design"
    plan = json.loads((ROOT / "tests" / "fixtures" / "mock" / "extensions_obs" / "0.json").read_text())["tool_calls"][0]["args"]["value"]
    for p in (prop, plan):
        (tmp_path / "extensions" / f"{p['id']}.json").write_text(json.dumps(p))
    rows = D.write_extension_diagrams(tmp_path, [{"id": "advance_design"}, {"id": "data_to_collect"}])
    assert rows[0]["svg"] == "extensions/advance_design.svg" and rows[1]["svg"] == "extensions/data_to_collect.svg"
    svg = (tmp_path / "extensions" / "advance_design.svg").read_text()
    assert "Primary outcome" in svg and any(i.startswith("arm-") for i in _ids(_parse(svg)))
    assert 'class="edge assoc"' in (tmp_path / "extensions" / "data_to_collect.svg").read_text()
    assert "Identification:" in (tmp_path / "extensions" / "data_to_collect.txt").read_text()
    assert json.loads((tmp_path / "extensions" / "index.json").read_text())[0]["text"] == "extensions/advance_design.txt"
