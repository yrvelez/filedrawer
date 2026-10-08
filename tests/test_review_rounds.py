"""Journal-style review: draft -> referees -> editor's decision letter -> author revision -> editor sign-off on the
revised text; `address` archives the round and runs round two."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(tmp_path_factory, demo_csv, extra_flags):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("rounds")
    repo = out / "repo"
    repo.mkdir()
    cfg = load_config(overrides={"provider": "mock", "review": {"agent_fix": False}})
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-immigration-framing", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": "https://github.com/x/demo"}, cfg,
                        {"review": True, "no_lit": True, "no_exploratory": True, "synthetic": True, **extra_flags})


@pytest.fixture(scope="module")
def revised(tmp_path_factory, demo_csv):
    return _run(tmp_path_factory, demo_csv, {})


def test_revision_consumes_the_decision_letter(revised):
    rv = json.loads((revised / "review.json").read_text())
    assert rv["round"] == 1 and rv["rounds"] == []
    assert rv["revision_notes"] and rv["revision_notes"][0].startswith("K1:")
    snap = json.loads((revised / "provenance" / "ctx_snapshot.json").read_text())
    assert snap["sections_draft"]["abstract"] != snap["sections"]["abstract"]
    assert "positive but imprecise" in snap["sections"]["abstract"]
    rep = (revised / "report.md").read_text()
    assert "positive but imprecise" in rep[rep.index("## Abstract"):rep.index("## Key findings")]
    box = rep[rep.index("## Review\n"):rep.index("## Technical appendix")]
    assert "**Outcome.**" in box and "Decision" not in box and "author" not in box.lower()
    assert "- G1." in box and "290 respondents" in box and "Done:" in box
    assert "After corrections" in box and "**Reworded** · Abstract" in box


def test_signoff_rejudges_claims_and_decides(revised):
    rv = json.loads((revised / "review.json").read_text())
    so = rv["signoff"]
    assert so["claims"][0]["previous"] == "C1" and so["claims"][0]["verdict"] == "supported"
    # the Light Pass raises no analytical issues, so once the re-check supports every claim nothing is left open
    assert rv["decision"] == "accept" == so["decision"]
    sj = json.loads((revised / "study.json").read_text())
    assert "review_decision" not in sj                      # no editor, so no disposition is published
    assert sj["review_claims"]["total"] == len(so["claims"]) and sj["review_claims"]["on_revised_text"] is True
    assert sj["review_claims"]["open_analytical"] == 0 and sj["review_rounds"] == 1 and sj["review_round"] == 1
    assert next(b for b in sj["badges"] if b["name"] == "review")["value"].endswith("claims supported")
    prov = json.loads((revised / "provenance" / "provenance.json").read_text())
    assert prov["review_rounds"] == [{"round": 1, "decision": "accept", "k_issues": 1, "revised": True, "agent_addenda": []}]
    md = (revised / "review.md").read_text()
    assert "Review outcome (round 1):" in md and "after the agent's corrections" in md
    assert "## Claim re-check on the corrected text" in md and "## Corrections made by the writing agent" in md


def test_no_revise_keeps_the_draft(tmp_path_factory, demo_csv):
    study = _run(tmp_path_factory, demo_csv, {"no_revise": True})
    rv = json.loads((study / "review.json").read_text())
    assert "revision_notes" not in rv and "signoff" not in rv
    snap = json.loads((study / "provenance" / "ctx_snapshot.json").read_text())
    assert snap["sections_draft"]["abstract"] == snap["sections"]["abstract"]
    assert "checked claims supported before corrections" in (study / "report.md").read_text()


def test_address_archives_round_one_and_runs_round_two(revised):
    from filedrawer.config import load_config
    from filedrawer.address import run_address
    cfg = load_config(overrides={"provider": "mock", "review": {"agent_fix": False}})
    out = run_address(revised, cfg, issues=["R2"], yes=True, by="A. Tester")     # named explicitly: a person may ask
    assert out["ran"] is True
    rv = json.loads((revised / "review.json").read_text())
    assert rv["round"] == 2 and len(rv["rounds"]) == 1
    r1 = rv["rounds"][0]
    assert r1["round"] == 1 and r1["decision"] == "accept" and r1["revision_notes"][0].startswith("K1:")
    assert {i["id"] for i in r1["issues"]} == {"R1", "R2", "K1"}
    rep = (revised / "report.md").read_text()
    assert "*Round 1: 3 issue(s); " in rep and "claim" in rep[rep.index("*Round 1: 3 issue(s); "):][:160]
    sj = json.loads((revised / "study.json").read_text())
    assert sj["review_round"] == 2 and sj["review_rounds"] == 2


def test_followup_brief_sends_back_what_is_still_wrong():
    """After a correction pass, claims the re-check still flags and serious text issues without a note go back to the
    writing agent; supported claims, noted issues, low-severity and declined items do not."""
    from filedrawer.review import followup_brief
    rv = {"round": 1, "revision_notes": ["R1: stated the subsample size"],
          "signoff": {"claims": [{"claim": "a", "verdict": "supported"}, {"claim": "b", "location": "E1", "verdict": "unsupported", "evidence": "empty table"}]},
          "issues": [{"id": "R1", "severity": "high", "disposition": "editorial"}, {"id": "R5", "severity": "medium", "disposition": "editorial", "issue": "weights"},
                     {"id": "R7", "severity": "low", "disposition": "editorial"}, {"id": "R3", "severity": "high", "disposition": "declined", "disposition_reason": "plan"}]}
    b = followup_brief(rv)
    assert [c["id"] for c in b["claims"]] == ["F1"] and "Still unsupported" in b["claims"][0]["issue"]
    assert [i["id"] for i in b["presentational"]] == ["R5"] and b["declined"] == [{"id": "R3", "reason": "plan"}]
    rv["signoff"]["claims"][1]["verdict"] = "supported"
    rv["revision_notes"].append("R5: documented the weights")
    assert followup_brief(rv) is None


def test_light_pass_report_says_what_it_checks(revised):
    rep = (revised / "report.md").read_text()
    box = rep[rep.index("## Review\n"):rep.index("## Technical appendix")]
    assert "The Light Pass does two things" in box and "registered analyses run as planned" in box
    assert "| Registered analysis | Against the plan |" in box
    rv = json.loads((revised / "review.json").read_text())
    assert all(i["kind"] == "presentational" for i in rv["issues"]) and not rv.get("agent_addenda")


def test_plan_match_flags_missing_estimates_and_unexplained_deviations():
    from filedrawer.review.plan_match import plan_match, sentence
    pap = {"registered": {"hypotheses": [{"id": "H1"}, {"id": "H2"}, {"id": "H3"}, {"id": "H4"}]},
           "implemented": {"hypotheses": [{"id": "H1"}, {"id": "H3"}, {"id": "H4"}]}, "registration": {"status": "registered"}}
    tags = [{"analysis_id": "H1", "tag": "registered", "differences": []},
            {"analysis_id": "H3", "tag": "deviation", "differences": [{"field": "estimator.covariates"}], "justification": ""},
            {"analysis_id": "H4", "tag": "deviation", "differences": [{"field": "estimator.weights"}], "justification": "weights were missing"}]
    rows = [{"analysis_id": "H1", "estimate": "0.1"}, {"analysis_id": "H3", "estimate": "0.2"}, {"analysis_id": "H4", "estimate": "0.3"}]
    pm = plan_match(pap, tags, rows)
    st = {r["id"]: r["status"] for r in pm["rows"]}
    assert st == {"H1": "as planned", "H2": "not run", "H3": "deviation", "H4": "deviation"}
    probs = {p["id"]: p["problem"] for p in pm["problems"]}
    assert set(probs) == {"H2", "H3"} and "no stated reason" in probs["H3"] and "covariates" in probs["H3"]
    assert sentence(pm).startswith("1 of 4 registered analyses run as planned")
