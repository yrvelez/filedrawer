"""End-to-end run of the demo with the mock provider, plus the privacy canary and release flow."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from filedrawer.cli import main as cli_main
from filedrawer import index as fdindex
from filedrawer.release import attest, release, reproduce
from demo.make_demo import CANARY

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def demo_study(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("studies")
    rc = cli_main(["demo", "--provider", "mock", "--out", str(out), "--review"])
    assert rc == 0
    return out / "demo-immigration-framing"


def test_package_files(demo_study):
    for rel in ["study.json", "report.md", "codebook.json", "codebook.md", "pap.json", "pap.md", "survey.qsf", "RUN.md", "review.md",
                "data/raw_tidy.csv", "data/clean.csv", "results/registered_summary.csv", "results/analysis_tags.csv",
                "results/H1.csv", "results/H2.csv", "results/S1_by_level.csv", "figures/registered_effects.png",
                "provenance/provenance.json", "provenance/llm_log.jsonl", "provenance/literature.json"] + \
               [f"scripts/0{i}_{n}.py" for i, n in enumerate(["tidy", "clean", "registered", "exploratory"], start=1)]:
        assert (demo_study / rel).exists(), rel
    for rel in ["silicon", "scripts/05_silicon.py", "figures/silicon_comparison.png"]:     # silicon stage removed
        assert not (demo_study / rel).exists(), rel
    assert "Silicon" not in (demo_study / "report.md").read_text()


def test_tags_and_report_labels(demo_study):
    tags = (demo_study / "results" / "analysis_tags.csv").read_text()
    assert "H1,hypothesis,registered" in tags and "H2,hypothesis,deviation" in tags and "E1,exploratory,exploratory" in tags
    rep = (demo_study / "report.md").read_text()
    assert "Everything in this section is exploratory" in rep
    assert "Deviation from pre-registration" in rep
    assert "FULLY AGENTIC" in rep and "SIMULATED DEMO DATA" in rep
    assert "[citation removed: not in retrieved set]" in rep      # fabricated citation stripped
    assert "Reviewer pass" in rep


def test_study_json(demo_study):
    sj = json.loads((demo_study / "study.json").read_text())
    assert sj["slug"] == "demo-immigration-framing" and sj["synthetic"] and sj["release_status"] == "draft"
    assert sj["provenance"]["mode"] == "fully_agentic" and sj["provenance"]["reviewer_pass"] is True
    assert "IPAddress" in sj["provenance"]["pii"]["dropped"]
    h = {x["id"]: x for x in sj["hypotheses"]}
    assert h["H1"]["tag"] == "registered" and h["H1"]["supported"] is True and h["H2"]["tag"] == "deviation"
    assert sj["constructs"] == ["immigration_attitudes", "framing_effects", "partisanship"]
    prov = json.loads((demo_study / "provenance" / "provenance.json").read_text())
    assert "silicon" not in prov["usage_by_agent"] and prov["inputs"]["raw_csv_sha256"]


def test_canary_never_reaches_model_or_package(demo_study):
    log = (demo_study / "provenance" / "llm_log.jsonl").read_text()
    assert CANARY not in log and "canary.person@example.com" not in log and "203.0.113" not in log
    # nothing in the package carries the canary or any identifier value
    for p in demo_study.rglob("*"):
        if p.is_file():
            txt = p.read_bytes()
            assert CANARY.encode() not in txt, p
            assert b"203.0.113." not in txt, p
    # every request is schema/aggregates: no request message contains a 12+ token run of raw row values
    import re
    rows = (ROOT / "runs" / "demo_inputs" / "demo_raw.csv").read_text().splitlines()[3:6]
    for line in log.splitlines():
        rec = json.loads(line)
        blob = json.dumps(rec["request"])
        for r in rows:
            assert ",".join(r.split(",")[17:29]) not in blob


def test_reproduce_and_release_flow(demo_study):
    out = reproduce(demo_study)
    assert out["ok"], out
    prov = attest(demo_study, step="Checked tags and read the report", by="Test Author")
    assert prov["mode"] == "human_reviewed" and len(prov["human_steps"]) == 1
    assert "HUMAN REVIEWED" in (demo_study / "report.md").read_text()
    sj = release(demo_study)
    assert sj["release_status"] == "released"
    idx = fdindex.build_index(demo_study.parent, include_drafts=False)
    assert [s["slug"] for s in idx["studies"]] == ["demo-immigration-framing"]
    assert idx["studies"][0]["provenance_mode"] == "human_reviewed"


def test_release_blocked_when_pii_kept(tmp_path):
    out = tmp_path / "studies"
    rc = cli_main(["demo", "--provider", "mock", "--out", str(out), "--allow-pii", "--no-silicon", "--no-lit"])   # --no-silicon: removed stage, still accepted
    assert rc == 0
    study = out / "demo-immigration-framing"
    assert "IPAddress" in Path(study / "data" / "raw_tidy.csv").read_text().splitlines()[0]
    with pytest.raises(SystemExit):
        release(study)
    prov = json.loads((study / "provenance" / "provenance.json").read_text())
    assert prov["pii"]["allow_pii"] and "IPAddress" in prov["pii"]["kept_with_override"]


def test_csv_only_fallback_runs(tmp_path):
    """No QSF: codebook inferred from the export; arms must come from the data column."""
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    from demo.make_demo import generate
    csv = generate(tmp_path / "raw.csv")
    cfg = load_config(overrides={"provider": "mock"})
    study = run_pipeline({"csv": str(csv), "qsf": None, "pap": str(ROOT / "demo" / "pap.md"), "slug": "noqsf",
                          "title": "No QSF", "authors": [], "out_dir": str(tmp_path / "s")}, cfg,
                         {"arm_column": "condition", "no_lit": True, "no_exploratory": True, "synthetic": True})
    sj = json.loads((study / "study.json").read_text())
    assert any("no QSF" in d for d in sj["provenance"]["degraded"])
    assert (study / "results" / "registered_summary.csv").exists()


def test_package_dir_and_init_study(tmp_path):
    """Standalone study repo: scaffold, then the package lands at the repo root with root-level links."""
    from filedrawer.scaffold import init_study
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    from demo.make_demo import generate
    repo = tmp_path / "ai-discernment"
    msg = init_study(repo, "ai-discernment", "AI discernment", "A. Author", "https://github.com/yrvelez/ai-discernment")
    assert "scaffolded" in msg and (repo / "run.sh").exists() and "inputs/**/*.csv" in (repo / ".gitignore").read_text()
    generate(repo / "inputs" / "export.csv")
    (repo / "inputs" / "survey.qsf").write_bytes((ROOT / "demo" / "demo.qsf").read_bytes())
    (repo / "inputs" / "pap.md").write_text((ROOT / "demo" / "pap.md").read_text())
    (repo / "README.md").write_text("# keep me\n")
    cfg = load_config(overrides={"provider": "mock"})
    study = run_pipeline({"csv": str(repo / "inputs" / "export.csv"), "qsf": str(repo / "inputs" / "survey.qsf"),
                          "pap": str(repo / "inputs" / "pap.md"), "slug": "ai-discernment", "title": "AI discernment",
                          "authors": ["A. Author"], "out_dir": str(tmp_path), "package_dir": str(repo),
                          "repo_url": "https://github.com/yrvelez/ai-discernment"}, cfg,
                         {"no_lit": True, "no_exploratory": True})
    assert study == repo and (repo / "README.md").read_text() == "# keep me\n"      # inputs and README survive
    assert (repo / "inputs" / "export.csv").exists() and (repo / "report.md").exists()
    sj = json.loads((repo / "study.json").read_text())
    assert sj["links"]["folder"] == "https://github.com/yrvelez/ai-discernment/tree/main"
    assert sj["links"]["report"] == "https://github.com/yrvelez/ai-discernment/blob/main/report.md"
    gi = (repo / ".gitignore").read_text()
    assert "inputs/**/*.csv" in gi                                                    # scaffold gitignore kept
    # a second run clears outputs but keeps inputs
    run_pipeline({"csv": str(repo / "inputs" / "export.csv"), "qsf": str(repo / "inputs" / "survey.qsf"),
                  "pap": str(repo / "inputs" / "pap.md"), "slug": "ai-discernment", "title": "AI discernment",
                  "authors": [], "out_dir": str(tmp_path), "package_dir": str(repo)}, cfg,
                 {"no_lit": True, "no_exploratory": True})
    assert (repo / "inputs" / "export.csv").exists() and (repo / "results" / "registered_summary.csv").exists()


# ---- report layout: key findings table, model details in code blocks, no shouting labels -------------
def test_report_layout(demo_study):
    rep = (demo_study / "report.md").read_text()
    assert "## Key findings" in rep
    kf = rep[rep.index("## Key findings"):rep.index("## Design and data")]
    assert kf.count("\n- ") >= 3 and "| Estimate |" not in kf          # prose bullets, not the fallback table
    assert rep.count("```") >= 4                                      # model block(s) + reproduction block
    assert "PLANNED, NOT PRE-REGISTERED" not in rep and "DEVIATION from" not in rep
    assert rep.index("## Abstract") < rep.index("## Key findings") < rep.index("## Results")
    assert rep.index("## Limitations") < rep.index("## Technical appendix") < rep.index("### Files")
    assert "#### Details:" in rep                                      # dense material sits behind click-to-expand headings



def test_citation_files(demo_study):
    rep = (demo_study / "report.md").read_text()
    assert "### How to cite" in rep and "@unpublished{" in rep and "Cite as:" in rep
    cff = (demo_study / "CITATION.cff").read_text()
    assert cff.startswith("cff-version: 1.2.0") and "repository-code" in cff


