"""Research potential: the memo drives typed extensions (design advance, generalizability, theoretical debate, plus a
non-survey plan), renders in the report and study.json, and degrades to the legacy kinds without a memo."""
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/yrvelez/filedrawer"


def _run(tmp_path_factory, demo_csv, name, fixtures=None):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp(name)
    repo = out / "repo"
    repo.mkdir()
    cfg = load_config(overrides={"provider": "mock"})
    flags = {"review": "light", "no_lit": True, "no_exploratory": True, "synthetic": True, "extensions": True}
    if fixtures:
        flags["fixtures"] = str(fixtures)
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-pot", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": REPO}, cfg, flags)


@pytest.fixture(scope="module")
def pot_study(tmp_path_factory, demo_csv):
    return _run(tmp_path_factory, demo_csv, "pot")


def test_memo_is_saved_and_rendered(pot_study):
    memo = json.loads((pot_study / "provenance" / "potential.json").read_text())
    assert [b["id"] for b in memo["briefs"]] == ["advance_design", "generalizability_conditional", "theoretical_debate", "data_to_collect"]
    assert memo["briefs"][3]["needs_qsf"] is False and memo["debates"][0]["id"] == "D1"
    rep = (pot_study / "report.md").read_text()
    assert "## Research potential" in rep and "| Statistical power |" in rep and "**D1. Economic versus cultural" in rep
    assert rep.index("## Review\n") < rep.index("## Research potential") < rep.index("## Proposed extensions") < rep.index("## Technical appendix")
    sj = json.loads((pot_study / "study.json").read_text())
    assert sj["potential"]["causes"] == ["power", "framing"] and len(sj["potential"]["briefs"]) == 4


def test_extensions_follow_the_briefs(pot_study):
    rows = json.loads((pot_study / "extensions" / "index.json").read_text())
    assert [r["id"] for r in rows] == ["advance_design", "generalizability_conditional", "theoretical_debate", "data_to_collect"]
    assert [r["kind"] for r in rows] == ["mechanism", "boundary", "alternative", "observational"]
    assert [r["label"] for r in rows] == ["Design advance", "Generalizability", "Theoretical debate", "Data to collect"]
    assert rows[0]["debate"] == "D1" and rows[0]["why"] and rows[0]["power_target"]["n_per_arm"] == 500
    assert rows[0]["qsf"] and rows[3]["qsf"] is None and rows[3]["plan"]["identification"]
    assert not (pot_study / "extensions" / "data_to_collect.qsf").exists()
    rep = (pot_study / "report.md").read_text()
    assert "### Design advance:" in rep and "### Data to collect:" in rep and "#### Details: background and open items" in rep
    assert "**Identification.**" in rep and "**Power.** About 500 per arm" in rep
    sj = json.loads((pot_study / "study.json").read_text())
    assert sj["extensions"][0]["brief_id"] == "advance_design" and sj["extensions"][3]["mode"] == "observational"


def test_without_a_memo_the_legacy_kinds_run(tmp_path_factory, demo_csv, tmp_path):
    src = ROOT / "tests" / "fixtures" / "mock"
    fx = tmp_path / "mock"
    shutil.copytree(src, fx, ignore=shutil.ignore_patterns("potential", "extensions_obs"))
    study = _run(tmp_path_factory, demo_csv, "legacy", fixtures=fx)
    assert not (study / "provenance" / "potential.json").exists()
    rows = json.loads((study / "extensions" / "index.json").read_text())
    assert [r["kind"] for r in rows] == ["mechanism", "boundary", "alternative"] and all(r["brief_id"] is None for r in rows)
    rep = (study / "report.md").read_text()
    assert "## Research potential" not in rep and "### Mechanism:" in rep
    assert "research potential" in " ".join(json.loads((study / "provenance" / "provenance.json").read_text())["degraded"]).lower()


def test_validate_drops_unknown_works_and_requires_the_three_briefs():
    from filedrawer.agents import potential as POT
    memo = json.loads(json.loads((ROOT / "tests" / "fixtures" / "mock" / "potential" / "0.json").read_text())["content"])
    errs = POT.validate(memo, {"10.1000/demo1"})
    assert errs == [] and [w["id"] for w in memo["debates"][0]["works"]] == ["10.1000/demo1"]
    memo["briefs"] = [b for b in memo["briefs"] if b["id"] != "theoretical_debate"]
    assert any("theoretical_debate" in e for e in POT.validate(memo, set()))


def test_no_debate_means_no_debate_brief():
    """With no debate in the literature, a theoretical_debate brief is a placeholder: it is dropped, not required."""
    from filedrawer.agents import potential as POT
    memo = json.loads(json.loads((ROOT / "tests" / "fixtures" / "mock" / "potential" / "0.json").read_text())["content"])
    memo["debates"] = []
    for b in memo["briefs"]:
        if b["id"] == "theoretical_debate":
            b["title"] = "No debate visible in the retrieved works"
    assert POT.validate(memo, set()) == []
    assert "theoretical_debate" not in [b["id"] for b in memo["briefs"]]
    memo["briefs"] = [b for b in memo["briefs"] if b["id"] != "advance_design"]
    assert any("advance_design" in e for e in POT.validate(memo, set()))


def test_power_table_from_clean_csv(pot_study):
    from filedrawer.analysis import power
    pap = json.loads((pot_study / "pap.json").read_text())
    table = power.mde_table(pot_study, pap)
    assert table and all(r["mde_80"] > 0 and r["n_level"] > 1 for r in table)
    assert {r["hypothesis"] for r in table} >= {"H1"}
    assert "| Analysis |" in power.mde_markdown(table)


def test_diagrams_are_written_and_embedded(pot_study):
    assert (pot_study / "figures" / "design.svg").exists() and (pot_study / "figures" / "design.txt").exists()
    rep = (pot_study / "report.md").read_text()
    assert "![Design at a glance](figures/design.svg)" in rep and "#### Details: the design in words" in rep
    assert rep.index("## Design and data") < rep.index("![Design at a glance]") < rep.index("## Results")
    rows = json.loads((pot_study / "extensions" / "index.json").read_text())
    assert rows[0]["svg"] == "extensions/advance_design.svg" and rows[3]["svg"] == "extensions/data_to_collect.svg"
    assert "[diagram](extensions/advance_design.svg)" in rep and "[plain-text description](extensions/advance_design.txt)" in rep
    sj = json.loads((pot_study / "study.json").read_text())
    assert sj["design"]["diagram"] == "figures/design.svg" and sj["design"]["causal"] is True and sj["design"]["sample_kind"] == "human"
    assert sj["extensions"][0]["svg"] == "extensions/advance_design.svg"
    man = json.loads((pot_study / ".filedrawer-manifest.json").read_text())["files"]
    assert "figures/design.svg" in man and "extensions/advance_design.svg" in man
