"""Phase A: structured review -> approved robustness addenda -> re-estimate, re-write, re-review, responses memo."""
import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _rows(path: Path) -> dict:
    with open(path, newline="") as fh:
        return {r["analysis_id"] + "|" + r.get("term", ""): r for r in csv.DictReader(fh)}


@pytest.fixture(scope="module")
def reviewed_study(tmp_path_factory, demo_csv):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("addr")
    repo = out / "demo-repo"; repo.mkdir()
    cfg = load_config(overrides={"provider": "mock", "review": {"agent_fix": False}})
    study = run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                          "slug": "demo-immigration-framing", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                          "package_dir": str(repo), "repo_url": "https://github.com/x/demo"}, cfg,
                         {"review": "light,advanced", "no_lit": True, "no_exploratory": True, "synthetic": True})
    return study


def test_review_json_is_structured(reviewed_study):
    rv = json.loads((reviewed_study / "review.json").read_text())
    ids = [i["id"] for i in rv["issues"]]
    assert ids[:2] == ["R1", "R2"] and "A3" in ids and ids[-1] == "K1"   # K1: the checking agent's overstated-claim issue
    kinds = {i["id"]: i["kind"] for i in rv["issues"]}
    light = [i for i in rv["issues"] if i.get("source") == "light"]
    assert light and all(i["kind"] == "presentational" and i["check"] in ("estimate", "plan") for i in light)   # Light Pass: text only
    assert kinds["A3"] == "analytical" and kinds["K1"] == "presentational"
    pm = rv["plan_match"]
    assert pm["summary"]["registered"] >= 1 and pm["rows"][0]["status"] in ("as planned", "deviation")
    assert next(i for i in rv["issues"] if i["id"] == "A3")["change"]["target"] == "H2"
    assert "A3 (analytical, advanced:statistics)" in (reviewed_study / "review.md").read_text()
    assert (reviewed_study / "provenance" / "ctx_snapshot.json").exists()


def test_address_adds_robustness_addendum_without_touching_registered(reviewed_study):
    from filedrawer.config import load_config
    from filedrawer.address import run_address, select_issues
    cfg = load_config(overrides={"provider": "mock"})
    before = _rows(reviewed_study / "results" / "registered_summary.csv")
    rv = json.loads((reviewed_study / "review.json").read_text())
    assert [i["id"] for i in select_issues(rv, None)] == ["A3"]
    # dry run proposes but changes nothing
    out = run_address(reviewed_study, cfg, dry_run=True)
    assert out["ran"] is False and out["addenda"][0]["base"] == "H2" and out["addenda"][0]["id"] == "H2a"
    assert not (reviewed_study / "responses.md").exists()
    # real run, unattended approval
    out = run_address(reviewed_study, cfg, yes=True, by="A. Tester")
    assert out["ran"] is True and [a["id"] for a in out["addenda"]] == ["H2a"]
    after = _rows(reviewed_study / "results" / "registered_summary.csv")
    for k, v in before.items():                                   # registered rows byte-identical
        assert after[k] == v, k
    assert "H2a|" in after and after["H2a|"]["estimate"]
    tags = {r["analysis_id"]: r for r in csv.DictReader(open(reviewed_study / "results" / "analysis_tags.csv"))}
    assert tags["H2a"]["tag"] == "robustness" and tags["H2"]["tag"] in ("registered", "deviation")
    pap = json.loads((reviewed_study / "pap.json").read_text())
    assert pap["addenda"][0]["status"] == "run" and pap["addenda"][0]["responds_to"] == "A3"
    assert all(h["id"] != "H2a" for h in pap["implemented"]["hypotheses"])      # the saved plan keeps implemented clean
    memo = (reviewed_study / "responses.md").read_text()
    assert "A3" in memo and "H2a" in memo and "Second reviewer pass" in memo
    rep = (reviewed_study / "report.md").read_text()
    assert "## Review\n" in rep and "### H2a." in rep and "**Robustness check H2a**" in rep
    kf = rep[rep.index("## Key findings"):rep.index("## Design and data")]
    assert "H2a" not in kf
    sj = json.loads((reviewed_study / "study.json").read_text())
    assert sj["addenda"][0]["id"] == "H2a" and sj["responses"] == "responses.md"
    prov = json.loads((reviewed_study / "provenance" / "provenance.json").read_text())
    assert prov["mode"] == "human_reviewed" and any("H2a" in s["step"] for s in prov["human_steps"])
    assert prov["address_rounds"][0]["addenda"] == ["H2a"]
    man = json.loads((reviewed_study / ".filedrawer-manifest.json").read_text())["files"]
    assert "responses.md" in man and "results/H2a.csv" in man


def test_addendum_guards():
    from filedrawer.pap import addendum_errors, expand_addenda, next_addendum_id
    pap = json.loads((ROOT / "tests" / "fixtures" / "mock" / "papreader" / "pap.json").read_text())
    cols = ["imm_att_1", "imm_att_2", "imm_att_3", "policy_support", "pid3", "agecat", "educ", "condition", "Finished"]
    assert addendum_errors(pap, {"base": "H9", "delta": {"estimator": {"covariates": []}}}, cols)
    assert addendum_errors(pap, {"base": "H2", "delta": {"outcome": "imm_index"}}, cols)
    assert addendum_errors(pap, {"base": "H2", "delta": {"estimator": {"covariates": ["nope"]}}}, cols)
    assert addendum_errors(pap, {"base": "H2", "delta": {"estimator": {"covariates": ["pid3", "educ"]}}}, cols) == []
    pap["addenda"] = [{"id": "H2a", "base": "H2", "status": "approved", "label": "x", "delta": {"exclusions": ["attn == 1"]}}]
    ex = expand_addenda(pap)
    h = {x["id"]: x for x in ex["implemented"]["hypotheses"]}["H2a"]
    assert h["exclusions"] == ["Finished == 1", "attn == 1"] and h["base"] == "H2"
    assert next_addendum_id(pap, "H2") == "H2b"


def test_collapse_arms_addendum_runs(tmp_path):
    """A multi-arm hypothesis can be answered with one 'any treatment vs control' model."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tests.test_multiarm import make_data, make_pap, run_scripts
    import pandas as pd
    from filedrawer.pap import addendum_errors, expand_addenda
    df = make_data(n=900, seed=5)
    pap = make_pap()
    for h in pap["implemented"]["hypotheses"] + pap["registered"]["hypotheses"]:
        h["estimator"]["cluster"] = None
    cols = list(df.columns)
    add = {"id": "H1a", "base": "H1", "status": "approved", "label": "any treatment vs control", "delta": {"collapse_arms": True}}
    assert addendum_errors(pap, add, cols) == []
    pap["addenda"] = [add]
    ex = expand_addenda(pap)
    h = {x["id"]: x for x in ex["implemented"]["hypotheses"]}["H1a"]
    assert h["treatment"]["contrast"][0] == "__treated__" and h["pooled"] is False
    study = tmp_path / "collapse"
    run_scripts(study, ex, df)
    s = pd.read_csv(study / "results" / "registered_summary.csv")
    row = s[s["analysis_id"] == "H1a"].iloc[0]
    assert row["n"] == len(df) and abs(float(row["estimate"])) < 2
    from filedrawer.pap import next_addendum_id
    assert next_addendum_id(pap, "H1", taken={"H1b"}) == "H1c"



def test_rerender_after_address_keeps_addenda_and_responses(reviewed_study):
    """A later command that re-renders from the saved package (extensions, build) must not drop the address round."""
    from filedrawer.config import load_config
    from filedrawer.address import load_context
    from filedrawer.orchestrator import finalize_package
    if not (reviewed_study / "responses.md").exists():
        pytest.skip("address test did not run first")
    ctx = load_context(reviewed_study, load_config(overrides={"provider": "mock"}))
    assert any(h["id"] == "H2a" for h in ctx["pap"]["implemented"]["hypotheses"])
    finalize_package(ctx, ctx["sections"], package_dir=True, accumulate=True)
    rep = (reviewed_study / "report.md").read_text()
    assert "### H2a." in rep and "## Review\n" in rep and "**Robustness check H2a**" in rep
    sj = json.loads((reviewed_study / "study.json").read_text())
    assert any(h.get("tag") == "robustness" for h in sj["hypotheses"])
    saved = json.loads((reviewed_study / "pap.json").read_text())
    assert all(h["id"] != "H2a" for h in saved["implemented"]["hypotheses"])      # the plan on disk stays unexpanded


def test_orchestrator_triages_and_feeds_extensions(reviewed_study):
    """The review orchestrator (mock fixture review_orchestrator/0.json) sets dispositions, turns overstated claims into
    K issues, keeps unknown ids out, and its unresolved questions reach the extensions agent's task."""
    rv = json.loads((reviewed_study / "review.json").read_text())
    syn = rv["synthesis"]
    by = {i["id"]: i for i in rv["issues"]}
    assert by["R1"]["disposition"] == "editorial" and by["R2"]["disposition"] == "address"
    assert by["K1"]["source"] == "claims" and "Overstated claim" in by["K1"]["issue"]
    assert syn["unresolved"] == [{"question": "Does the framing effect persist a week later?", "why": "The study measured attitudes once.",
                                  "from": ["R2"], "id": "U1"}]
    assert "orchestrator" in rv["models"] and rv["overall"].startswith("The registered framing effects")
    md = (reviewed_study / "review.md").read_text()
    assert "## Claim checks" in md and "U1: Does the framing effect persist" in md
    from filedrawer.config import load_config
    from filedrawer.address import load_context
    from filedrawer.agents.extensions import _task
    task = _task(load_context(reviewed_study, load_config(overrides={"provider": "mock"})), "mechanism", [])
    assert "U1: Does the framing effect persist a week later?" in task and "State the subsample size" in task


def test_select_issues_respects_dispositions():
    from filedrawer.address import select_issues
    rv = {"issues": [{"id": "R1", "kind": "analytical", "severity": "high", "disposition": "declined"},
                     {"id": "R2", "kind": "analytical", "severity": "high", "disposition": "address"},
                     {"id": "R3", "kind": "analytical", "severity": "medium", "disposition": "unresolved"},
                     {"id": "R4", "kind": "analytical", "severity": "medium"}]}
    assert [i["id"] for i in select_issues(rv, None)] == ["R2", "R4"]
    assert [i["id"] for i in select_issues(rv, ["R1"])] == ["R1"]          # an explicit request overrides the editor


def test_review_section_is_a_correction_list(reviewed_study):
    from filedrawer.config import load_config
    from filedrawer.address import run_address
    if not (reviewed_study / "responses.md").exists():
        run_address(reviewed_study, load_config(overrides={"provider": "mock"}), yes=True, by="A. Tester")
    rep = (reviewed_study / "report.md").read_text()
    assert "## Review\n" in rep and "## Peer review" not in rep and "## Reviewer responses" not in rep
    box = rep[rep.index("## Review\n"):rep.index("## Technical appendix")]
    assert "Light Pass + Advanced Pass review" in box and "no person reviewed or revised this report" in box
    assert "**Outcome.**" in box and "#### Corrections" in box and "#### Details: full review log" in box
    assert "(approved by A. Tester)" in box                              # a named person approving is credited by name
    assert "#### Other review options" in box and "**OpenReview or any referee report**" in box and "**Coarse**" in box
    assert "author" not in box.lower() and "editor" not in box.lower()


@pytest.fixture(scope="module")
def agent_study(tmp_path_factory, demo_csv):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("agentfix")
    repo = out / "demo-repo"; repo.mkdir()
    cfg = load_config(overrides={"provider": "mock"})
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-immigration-framing", "title": "Demo", "authors": ["A. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": "https://github.com/x/demo"}, cfg,
                        {"review": "light,advanced", "no_lit": True, "no_exploratory": True, "synthetic": True})


def test_agent_answers_serious_analytical_issue_in_the_run(agent_study):
    """Without anyone running `address`, the pipeline's agent turns the Advanced Pass's analytical issue (A3) into a
    robustness check (H2a), marks the issue answered, and records no human step."""
    rv = json.loads((agent_study / "review.json").read_text())
    by = {i["id"]: i for i in rv["issues"]}
    assert rv["agent_addenda"] == ["H2a"] and by["A3"]["answered_by"] == "H2a"
    from filedrawer.address import select_issues
    assert select_issues(rv, None) == []                            # nothing left for a person to approve
    pap = json.loads((agent_study / "pap.json").read_text())
    assert pap["addenda"][0]["status"] == "run" and pap["addenda"][0]["approved_by"] == "agent"
    prov = json.loads((agent_study / "provenance" / "provenance.json").read_text())
    assert prov["mode"] == "fully_agentic" and not prov.get("human_steps")
    rows = _rows(agent_study / "results" / "registered_summary.csv")
    assert "H2a|" in rows and rows["H2a|"]["estimate"]
    rep = (agent_study / "report.md").read_text()
    box = rep[rep.index("## Review\n"):rep.index("## Technical appendix")]
    assert "**Robustness check H2a** · A3:" in box and "approved by" not in box


def test_study_json_keeps_extension_question_links(reviewed_study):
    """study.json is what the dashboard harvests; each extension row must keep the reviewer questions it answers."""
    from filedrawer.config import load_config
    from filedrawer.address import load_context
    from filedrawer.package import build_study_json
    ctx = load_context(reviewed_study, load_config(overrides={"provider": "mock"}))
    ctx["extensions"] = [{"id": "mechanism", "kind": "mechanism", "title": "T", "addresses": ["U1"]}]
    assert build_study_json(ctx)["extensions"][0]["addresses"] == ["U1"]
