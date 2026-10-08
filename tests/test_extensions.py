"""Phase C: follow-up designs in autoexperiment's schema, grounded in the source survey, buildable as Qualtrics drafts."""
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/yrvelez/filedrawer"
MOCK = ROOT / "tests" / "fixtures" / "mock" / "extensions"


def _bridge_available():
    from filedrawer.extensions import autoexperiment_dir
    return autoexperiment_dir() is not None and shutil.which("node") is not None


def _proposal(turn=0) -> dict:
    return json.loads((MOCK / f"{turn}.json").read_text())["tool_calls"][0]["args"]["value"]


@pytest.fixture(scope="module")
def ext_study(tmp_path_factory, demo_csv):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("ext")
    repo = out / "repo"; repo.mkdir()
    cfg = load_config(overrides={"provider": "mock"})
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-ext", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": REPO}, cfg,
                        {"review": "light", "no_lit": True, "no_exploratory": True, "synthetic": True, "extensions": True})


def test_extensions_stage_writes_three_validated_designs(ext_study):
    rows = json.loads((ext_study / "extensions" / "index.json").read_text())
    # the research-potential memo (fixture potential/0.json) drives the designs: three survey briefs plus one plan
    assert [r["kind"] for r in rows] == ["mechanism", "boundary", "alternative", "observational"]
    assert [r["id"] for r in rows] == ["advance_design", "generalizability_conditional", "theoretical_debate", "data_to_collect"]
    assert all(r["status"] == "proposed" and r["hypothesis"] for r in rows)
    assert rows[0]["validated_by"] == ("autoexperiment" if _bridge_available() else "structural")
    for r in rows:
        p = json.loads((ext_study / "extensions" / f"{r['id']}.json").read_text())
        assert p["kind"] == r["kind"]
        if r["kind"] != "observational":
            assert len(p["design"]["conditions"]) >= 2
    rep = (ext_study / "report.md").read_text()
    assert "## Proposed extensions" in rep and "### Design advance:" in rep and "Import a QSF file" in rep
    assert rep.index("## Limitations") < rep.index("## Proposed extensions") < rep.index("## Technical appendix")
    sj = json.loads((ext_study / "study.json").read_text())
    assert [e["kind"] for e in sj["extensions"]] == ["mechanism", "boundary", "alternative", "observational"]
    man = json.loads((ext_study / ".filedrawer-manifest.json").read_text())["files"]
    assert "extensions/index.json" in man and "extensions/advance_design.json" in man


def test_source_and_structure_guards():
    from filedrawer import extensions as X
    p = _proposal(0)
    qsf_ids, urls = {"QID1", "QID12"}, {REPO}
    assert X.structural_errors(p) == [] and X.source_errors(p, qsf_ids, urls) == []
    bad = json.loads(json.dumps(p))
    bad["design"]["blocks"][-2]["questions"][0]["sourceQuestionId"] = "QID999"
    bad["design"]["evidence"][0]["url"] = "https://example.org/made-up"
    errs = X.source_errors(bad, qsf_ids, urls)
    assert any("QID999" in e for e in errs) and any("made-up" in e for e in errs)
    assert X.structural_errors({"id": "x"})                       # missing required keys
    assert any("kind" in e for e in X.structural_errors(dict(p, kind="sideways")))


@pytest.mark.skipif(not _bridge_available(), reason="autoexperiment + node not available")
def test_full_validator_catches_flow_errors(tmp_path):
    from filedrawer import extensions as X
    p = _proposal(1)
    p["design"]["conditions"][0]["nodeId"] = "rand"               # must point at an arm, not the randomizer
    errs, validator = X.validate(p, {"QID1", "QID12"}, {REPO}, tmp_dir=tmp_path)
    assert validator == "autoexperiment" and any("randomizer" in e for e in errs)


def test_extensions_command_on_finished_package(ext_study, capsys):
    from filedrawer.cli import main
    (ext_study / "extensions" / "index.json").unlink()
    rc = main(["extensions", str(ext_study), "--provider", "mock", "--fixtures", str(ROOT / "tests" / "fixtures" / "mock")])
    assert rc == 0
    rows = json.loads((ext_study / "extensions" / "index.json").read_text())
    assert len(rows) == 4
    prov = json.loads((ext_study / "provenance" / "provenance.json").read_text())
    assert prov["usage_by_agent"]["extensions"]["calls"] >= 6       # three from the run, three from the command


@pytest.mark.skipif(not _bridge_available(), reason="autoexperiment + node not available")
def test_build_dry_run_validates_without_touching_qualtrics(ext_study):
    from filedrawer import extensions as X
    out = X.build(ext_study, "generalizability_conditional", dry_run=True)
    assert out == {"ok": True, "errors": [], "built": False}
    with pytest.raises(SystemExit):
        X.build(ext_study, "nonexistent", dry_run=True)


def test_revise_replaces_one_proposal_and_resets_its_build(ext_study):
    from filedrawer import extensions as X
    rows = X.load_index(ext_study)
    for r in rows:
        if r["id"] == "generalizability_conditional":
            r.update(status="built", survey_id="SV_x", built_at="2026-01-01")
    (ext_study / "extensions" / "index.json").write_text(json.dumps(rows))
    p = _proposal(1)
    p["id"], p["title"] = "generalizability_conditional", "Revised boundary test"
    out = X.replace(ext_study, p, "structural", "narrow to refugees only")
    by = {r["id"]: r for r in out}
    g = by["generalizability_conditional"]
    assert g["title"] == "Revised boundary test" and g["status"] == "proposed"
    assert g["survey_id"] is None and g["revisions"][0]["request"] == "narrow to refugees only"
    assert g["brief_id"] == "generalizability_conditional" and g["label"] == "Generalizability"     # carried from the old index row
    assert set(by) == {"advance_design", "generalizability_conditional", "theoretical_debate", "data_to_collect"}


def test_normalize_repairs_common_model_slips():
    from filedrawer import extensions as X
    p = json.loads((MOCK / "0.json").read_text())["tool_calls"][0]["args"]["value"]
    q = p["design"]["blocks"][-1]["questions"][0]
    q["type"], q["rows"] = "Matrix", ["Useful", "Clear"]
    q["choices"] = [{"id": "1", "text": "Low", "recode": 1}, {"id": "2", "text": "High", "recode": 2}]
    p["design"]["flow"].append({"id": "br", "type": "branch", "label": "", "children": [], "blockId": "", "field": "", "value": "",
                                "questionId": q["id"], "choiceId": "1", "operator": "selected"})
    for e in p["design"].get("evidence") or []:
        e.pop("origin", None)
    X.normalize(p)
    assert q["rows"] == [{"id": "r1", "text": "Useful"}, {"id": "r2", "text": "Clear"}]
    assert [c["id"] for c in q["choices"]] == ["c1", "c2"] and q["choices"][0]["recode"] == "1"
    assert p["design"]["flow"][-1]["choiceId"] == "c1"
    assert all(e["origin"] == "web" for e in p["design"].get("evidence") or [])
