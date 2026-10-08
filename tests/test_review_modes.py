"""Review modes: light, advanced (methodology + statistics), coarse (CLI) and refine (manual import)."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_parse_modes():
    from filedrawer.review import parse_modes
    assert parse_modes(True) == ["light"]
    assert parse_modes("refine,light") == ["light", "refine"]
    assert parse_modes("both") == ["light", "advanced"]
    assert parse_modes("native") == ["light"]                 # review.json files written before the rename
    with pytest.raises(ValueError):
        parse_modes("light,deep")


def test_packaged_prompts_load_and_override_falls_back(tmp_path):
    from filedrawer.review.advanced import load_prompts, AGENTS
    prompts, notes = load_prompts(None)
    assert set(prompts) == set(AGENTS) and not notes
    assert "{section_text}" in prompts["statistics"]["user"] and "PDF" not in prompts["statistics"]["system"]
    (tmp_path / "statistics.yaml").write_text("name: statistics\nsystem: custom\nuser: '{section_text}'\n")
    prompts, notes = load_prompts(tmp_path)
    assert prompts["statistics"]["system"] == "custom"
    assert prompts["methodology"]["source"].endswith("review/prompts/methodology.yaml")
    assert any("methodology.yaml not usable" in n for n in notes)


def test_no_prompts_means_no_agents(tmp_path, monkeypatch):
    from filedrawer.review import advanced
    monkeypatch.setattr(advanced, "VENDORED", tmp_path / "nowhere")
    prompts, notes = advanced.load_prompts(None)
    assert prompts == {} and len(notes) == 2


def test_comment_mapping():
    from filedrawer.review.common import to_issue, parse_comments
    hyps = ["H1", "H2"]
    i = to_issue({"section_ref": "Results, H2", "issue": "Confounding", "detail": "educ", "severity": "major",
                  "suggestion": "add educ", "confidence": 0.8}, "advanced:statistics", hyps)
    assert i["severity"] == "high" and i["kind"] == "analytical" and i["change"] == {"target": "H2", "type": "robustness"}
    assert i["issue"] == "Confounding: educ" and i["fix"] == "add educ"
    mt = to_issue({"issue": "No Bonferroni correction", "severity": "critical", "kind": "analytical", "target": "H1"}, "x", hyps)
    assert mt["kind"] == "presentational" and mt["change"] is None
    assert to_issue({"issue": "x", "confidence": 0.1}, "x", hyps) is None
    assert to_issue({"issue": "x", "severity": "suggestion", "target": "H9"}, "x", hyps)["change"] is None
    assert parse_comments('[{"issue": "a"}, {"issue": "b"}]') == [{"issue": "a"}, {"issue": "b"}]
    assert parse_comments('{"comments": [{"issue": "a"}]}') == [{"issue": "a"}]


def test_split_sections_drops_appendix():
    from filedrawer.review.common import split_sections
    md = "# T\npreamble\n## Abstract\nabs\n## Results\nres\n## Technical appendix\nsecret"
    assert split_sections(md) == [("Abstract", "abs"), ("Results", "res")]


def test_read_review_file(tmp_path):
    from filedrawer.review.external import read_review_file
    (tmp_path / "r.md").write_text("hello")
    assert read_review_file(tmp_path / "r.md") == "hello"
    with pytest.raises(SystemExit):
        read_review_file(tmp_path / "r.docx")


@pytest.fixture(scope="module")
def reviewed(tmp_path_factory, demo_csv):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("modes")
    repo = out / "demo-repo"; repo.mkdir()
    cfg = load_config(overrides={"provider": "mock", "review": {"agent_fix": False}})
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-immigration-framing", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": "https://github.com/x/demo"}, cfg,
                        {"review": "light,advanced,coarse,refine", "no_lit": True, "no_exploratory": True, "synthetic": True})


def test_all_modes_merge_and_refine_is_pending(reviewed):
    rv = json.loads((reviewed / "review.json").read_text())
    assert rv["modes"] == ["light", "advanced", "coarse", "refine"] and rv["pending"] == ["refine"]
    assert set(rv["models"]) == {"light", "advanced:methodology", "advanced:statistics", "coarse", "orchestrator"}
    assert [i["id"] for i in rv["issues"]] == ["R1", "R2", "A1", "A2", "A3", "C1", "C2", "K1"]
    by = {i["id"]: i for i in rv["issues"]}
    assert by["A1"]["source"] == "advanced:methodology"                      # fenced JSON parsed
    assert by["A2"]["kind"] == "presentational" and by["A2"]["severity"] == "high"   # the Holm request
    assert by["A3"]["change"]["target"] == "H2" and by["C1"]["change"]["target"] == "H2"
    assert (reviewed / "provenance" / "coarse_review.md").exists()
    md = (reviewed / "review.md").read_text()
    assert "A3 (analytical, advanced:statistics)" in md and "Pending: Refine" in md and "review-import" in md
    assert "Light Pass + Advanced Pass + Coarse + Refine review" in (reviewed / "report.md").read_text()
    assert json.loads((reviewed / "provenance" / "provenance.json").read_text())["review_mode"] == "light,advanced,coarse,refine"


def test_refine_import_replaces_pending(reviewed, tmp_path):
    from filedrawer.cli import main
    f = tmp_path / "refine.md"
    f.write_text("Referee report. 1. Education omitted from H2 (major). 2. Abstract overstates generality (minor).")
    assert main(["review-import", str(reviewed), "refine", str(f), "--provider", "mock"]) == 0
    rv = json.loads((reviewed / "review.json").read_text())
    assert rv["pending"] == [] and [i["id"] for i in rv["issues"] if i["source"] == "refine"] == ["RF1", "RF2"]
    assert (reviewed / "provenance" / "refine_review.txt").read_text().startswith("Referee report")
    assert "Pending: Refine" not in (reviewed / "review.md").read_text()
    # importing again replaces, does not duplicate
    assert main(["review-import", str(reviewed), "refine", str(f), "--provider", "mock"]) == 0
    rv = json.loads((reviewed / "review.json").read_text())
    assert len([i for i in rv["issues"] if i["source"] == "refine"]) == 2


def test_address_answers_external_issue_and_carries_external_reviews(reviewed):
    from filedrawer.config import load_config
    from filedrawer.address import run_address, select_issues
    cfg = load_config(overrides={"provider": "mock", "review": {"agent_fix": False}})
    rv = json.loads((reviewed / "review.json").read_text())
    assert [i["id"] for i in select_issues(rv, None)] == ["A3", "C1", "RF1"]      # the Light Pass raises no analytical issues
    out = run_address(reviewed, cfg, issues=["RF1"], yes=True)
    assert out["ran"] is True and out["addenda"][0]["responds_to"] == "RF1" and out["addenda"][0]["base"] == "H2"
    rv2 = json.loads((reviewed / "review.json").read_text())
    assert rv2["modes"] == ["light", "advanced", "coarse", "refine"] and rv2["pending"] == []
    ids = [i["id"] for i in rv2["issues"]]
    assert {"C1", "C2", "RF1", "RF2"} <= set(ids) and any(i.startswith("A") for i in ids)
    assert "RF1" in (reviewed / "responses.md").read_text()
