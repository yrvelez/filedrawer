"""Regression tests for failures seen running a real standalone study repo (ai-discernment)."""
import json
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---- reruns never delete author files; reproduce works from "." --------------------------------
def test_package_dir_rerun_keeps_author_files_and_reproduce_dot(tmp_path):
    from filedrawer.scaffold import init_study
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline, MANIFEST
    from filedrawer.release import reproduce
    from demo.make_demo import generate
    repo = tmp_path / "study-repo"
    init_study(repo, "s", "S", "A", "https://github.com/x/s")
    generate(repo / "inputs" / "export.csv")
    (repo / "inputs" / "survey.qsf").write_bytes((ROOT / "demo" / "demo.qsf").read_bytes())
    (repo / "inputs" / "pap.md").write_text((ROOT / "demo" / "pap.md").read_text())
    author = {"data/replication_data.csv": "a,b\n1,2\n", "scripts/replication_script.R": "x <- 1\n",
              "figures/hand_made.png": "png", "code/analysis.R": "y <- 2\n", ".git/HEAD": "ref: refs/heads/main\n"}
    for rel, txt in author.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(txt)
    cfg = load_config(overrides={"provider": "mock"})
    args = {"csv": str(repo / "inputs" / "export.csv"), "qsf": str(repo / "inputs" / "survey.qsf"),
            "pap": str(repo / "inputs" / "pap.md"), "slug": "s", "title": "S", "authors": [],
            "out_dir": str(tmp_path), "package_dir": str(repo)}
    flags = {"no_lit": True, "no_exploratory": True}
    for _ in range(2):
        run_pipeline(args, cfg, flags)
        for rel, txt in author.items():
            assert (repo / rel).read_text() == txt, f"author file {rel} was changed or deleted"
    man = json.loads((repo / MANIFEST).read_text())["files"]
    assert "data/clean.csv" in man and "data/replication_data.csv" not in man
    files_section = (repo / "report.md").read_text().split("### Files", 1)[-1]
    assert ".git/" not in files_section and "`data/replication_data.csv`" in files_section
    assert not any(f.startswith("inputs/") for f in man)
    cwd = os.getcwd()
    try:
        os.chdir(repo)
        out = reproduce(Path("."))
    finally:
        os.chdir(cwd)
    assert out["ok"], out


def test_clear_previous_outputs_without_manifest(tmp_path):
    from filedrawer.orchestrator import clear_previous_outputs, MANIFEST
    for rel in ("data/clean.csv", "data/raw_tidy.csv", "data/mine.csv", "scripts/02_clean.py", "scripts/mine.R",
                "results/registered_summary.csv", "report.md", "README.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    clear_previous_outputs(tmp_path)
    assert (tmp_path / "data/mine.csv").exists() and (tmp_path / "scripts/mine.R").exists()
    assert (tmp_path / "README.md").exists()
    assert not (tmp_path / "data/clean.csv").exists() and not (tmp_path / "scripts/02_clean.py").exists()
    assert not (tmp_path / "results/registered_summary.csv").exists() and not (tmp_path / "report.md").exists()


def test_manifest_cannot_escape_study_dir(tmp_path):
    from filedrawer.orchestrator import clear_previous_outputs, MANIFEST
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    study = tmp_path / "study"
    study.mkdir()
    (study / MANIFEST).write_text(json.dumps({"files": ["../outside.txt"]}))
    clear_previous_outputs(study)
    assert outside.read_text() == "keep"


# ---- the analysed outcome must be the planned outcome ------------------------------------------
def _pap(reg_outcome, reg_cols, imp_outcome, imp_cols):
    def sec(o, cols):
        return {"outcomes": [{"name": o, "columns": cols}],
                "hypotheses": [{"id": "H1", "outcome": o, "treatment": {"column": "t", "contrast": ["1", "0"]}}]}
    return {"registered": sec(reg_outcome, reg_cols), "implemented": sec(imp_outcome, imp_cols)}


def test_outcome_swap_detected():
    from filedrawer.pap import outcome_changes
    swaps = outcome_changes(_pap("total_accuracy", ["total_score"], "ai_confidence", ["ai_outcome_1", "ai_outcome_2"]))
    assert len(swaps) == 1 and "total_accuracy" in swaps[0] and "ai_confidence" in swaps[0]


def test_outcome_rename_over_same_columns_is_fine():
    from filedrawer.pap import outcome_changes
    assert outcome_changes(_pap("accuracy", ["total_score"], "total_score", ["total_score"])) == []
    assert outcome_changes(_pap("y", ["a"], "y", ["a"])) == []


# ---- empty responses from reasoning models ------------------------------------------------------
class _EmptyProvider:
    name = "openrouter"

    def __init__(self):
        self.calls = []

    def chat(self, *, agent, messages, model, max_tokens, tools=None, temperature=0.0, meta=None):
        from filedrawer.llm.client import LLMResponse
        self.calls.append(max_tokens)
        return LLMResponse(content="", finish_reason="length", model=model)


def test_empty_response_retries_then_stops_with_clear_error():
    from filedrawer.llm.agent import Agent, EmptyResponseError
    prov = _EmptyProvider()
    with pytest.raises(EmptyResponseError) as e:
        Agent("papreader", prov, "z-ai/glm-5.3", "sys", None, max_tokens=4000).run("task")
    assert prov.calls == [4000, 8000]
    assert "z-ai/glm-5.3" in str(e.value) and "budgets.papreader.max_tokens" in str(e.value)


def test_empty_response_degrades_optional_agent():
    from filedrawer.llm.agent import Agent
    res = Agent("litreview", _EmptyProvider(), "m", "sys", None, max_tokens=100).run("task")
    assert res.stopped == "empty" and "litreview" in res.error


# ---- arm column by convention ----------------------------------------------------------------
def test_arm_column_found_by_convention():
    from filedrawer.qsf import Codebook, Arms
    from filedrawer.tidy import resolve_arms
    cb = Codebook(survey={}, questions=[], arms=Arms())
    df = pd.DataFrame({"treatment": [0, 1, 2, 0, 1, 2], "y": range(6)})
    arms = resolve_arms(cb, df)
    assert arms.column == "treatment" and arms.source == "convention" and len(arms.values) == 3


# ---- directional p-values -----------------------------------------------------------------------
def test_directional_p_in_rendered_template():
    from filedrawer.analysis.templates import render_registered
    src = render_registered({"implemented": {"outcomes": [], "hypotheses": [], "subgroups": []}}, [], "test")
    ns: dict = {}
    start = src.index("def directional_p")
    exec(src[start:src.index("def pool")].replace("ALPHA", "0.05"), ns)
    assert ns["directional_p"](0.1, 0.09, "positive") == pytest.approx(0.045)
    assert ns["directional_p"](-0.1, 0.09, "positive") == pytest.approx(0.955)
    assert ns["directional_p"](0.1, 0.09, "two_sided") == pytest.approx(0.09)
    assert ns["supported"](0.1, 0.09, "positive") is True
    assert ns["supported"](0.1, 0.09, "two_sided") is False


# ---- a documented choice where the plan was silent is not a deviation -----------------------------
def test_gap_filling_interpretation_is_not_a_deviation():
    from filedrawer.pap import tag_analyses
    def sec(covs):
        return {"outcomes": [{"name": "y", "columns": ["y"]}],
                "hypotheses": [{"id": "H1", "outcome": "y", "treatment": {"column": "t", "contrast": ["1", "0"]},
                                "estimator": {"kind": "lin", "covariates": covs}}]}
    pap = {"registration": {"status": "none"}, "registered": sec([]), "implemented": sec(["age"]),
           "ambiguities": [{"hypothesis": "H1", "field": "estimator.covariates", "kind": "interpretation"}]}
    t = tag_analyses(pap)[0]
    assert t["tag"] == "unregistered" and t["interpretations"][0]["field"] == "estimator.covariates"
    # without the logged interpretation it stays an undocumented deviation
    pap["ambiguities"] = []
    t = tag_analyses(pap)[0]
    assert t["tag"] == "deviation" and t["justification"] == "undocumented deviation"
    # changing a value the plan did state is still a deviation even if called an interpretation
    pap["registered"] = sec(["income"])
    pap["ambiguities"] = [{"hypothesis": "H1", "field": "estimator.covariates", "kind": "interpretation"}]
    assert tag_analyses(pap)[0]["tag"] == "deviation"


# ---- exploratory agent that produced nothing leaves no dead script behind ----------------------
def test_prune_exploratory_removes_dead_script(tmp_path):
    from filedrawer.orchestrator import prune_exploratory
    (tmp_path / "scripts").mkdir(); (tmp_path / "results").mkdir()
    (tmp_path / "scripts" / "04_exploratory.py").write_text("raise SystemExit(1)")
    (tmp_path / "results" / "E1_partial.csv").write_text("a\n")
    (tmp_path / "results" / "registered_summary.csv").write_text("a\n")
    assert prune_exploratory(tmp_path, [{"id": "E1"}]) == []          # results recorded: keep everything
    removed = prune_exploratory(tmp_path, [])
    assert sorted(removed) == ["results/E1_partial.csv", "scripts/04_exploratory.py"]
    assert (tmp_path / "results" / "registered_summary.csv").exists()


def test_post_hoc_badge_wording():
    from filedrawer.package import render_report
    import inspect
    src = inspect.getsource(render_report)
    assert "reconstructed post hoc from No pre-registration" not in src
    # the helper logic, checked directly
    note = "No pre-registration. This plan is a transcription of the authors' script."
    lead = "No pre-registration: the analysis plan was reconstructed post hoc"
    body = f"{lead}. {note[len('no pre-registration'):].lstrip(' .:;-')}".rstrip(".")
    assert body == lead + ". This plan is a transcription of the authors' script"


# ---- record_result accepts a double-encoded JSON payload ------------------------------------------
def test_record_result_decodes_json_string(tmp_path):
    from filedrawer.tools import ToolRegistry
    reg = ToolRegistry(tmp_path, allowed=("record_result",))
    reg.dispatch("record_result", {"key": "exploratory", "value": '[{"id": "E1", "title": "t"}]', "final": True})
    assert reg.records["exploratory"] == [{"id": "E1", "title": "t"}] and reg.final
    reg.dispatch("record_result", {"key": "note", "value": "plain text, not JSON"})
    assert reg.records["note"] == "plain text, not JSON"



# ---- deposit routine ships with the package and with every scaffolded study -------------------------
def test_agents_md_in_scaffold_and_repo(tmp_path):
    from filedrawer.scaffold import init_study
    pkg = Path(__file__).resolve().parents[1] / "src" / "filedrawer" / "AGENTS.md"
    root = Path(__file__).resolve().parents[1] / "AGENTS.md"
    assert pkg.read_text() == root.read_text(), "root AGENTS.md must be a copy of src/filedrawer/AGENTS.md"
    init_study(tmp_path / "s", "s", "S", "A", "https://github.com/x/s")
    text = (tmp_path / "s" / "AGENTS.md").read_text()
    assert "Never commit the raw export" in text and "filedrawer release" in text


# ---- key findings fall back to the numeric table when the writer gives no takeaways ---------------
def test_key_findings_fallback_table():
    from filedrawer.package import _key_findings
    pap = {"implemented": {"outcomes": [{"name": "y", "label": "Outcome Y (share correct)"}],
                           "hypotheses": [{"id": "H1"}]},
           "design": {"arms": {"column": "t", "labels": {"1": "Arm one"}, "control": "0", "treatment": ["1"]}}}
    summary = {"H1:1|C(t)[T.1]": {"analysis_id": "H1:1", "arm": "1", "outcome": "y", "estimate": "0.05", "std_error": "0.01",
                                   "p_value": "0.0001", "supported": "True"}}
    rows = _key_findings(pap, summary)
    assert rows[2].startswith("| **+0.050** | Arm one: Outcome Y (H1) |") and rows[2].endswith("| <0.001 |")


# ---- a run that fails on its inputs must not destroy the committed package -----------------------------
def test_failed_run_leaves_previous_package_intact(tmp_path):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    repo = tmp_path / "r"; (repo / "results").mkdir(parents=True)
    (repo / "report.md").write_text("old report"); (repo / "results" / "registered_summary.csv").write_text("a\n")
    (repo / "pap.md").write_text("plan")
    cfg = load_config(overrides={"provider": "mock"})
    with pytest.raises(SystemExit):
        run_pipeline({"csv": str(repo / "missing.csv"), "qsf": None, "pap": str(repo / "pap.md"), "slug": "r", "title": "R",
                      "authors": [], "out_dir": str(tmp_path), "package_dir": str(repo)}, cfg, {"no_lit": True, "no_exploratory": True})
    assert (repo / "report.md").read_text() == "old report" and (repo / "results" / "registered_summary.csv").exists()


def test_cleanup_never_removes_author_facing_files(tmp_path):
    from filedrawer.orchestrator import clear_previous_outputs, MANIFEST
    for rel in (".gitignore", "README.md", "run.sh", "data/clean.csv"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True); (tmp_path / rel).write_text("x")
    (tmp_path / MANIFEST).write_text(json.dumps({"files": [".gitignore", "README.md", "run.sh", "data/clean.csv"]}))
    removed = clear_previous_outputs(tmp_path)
    assert removed == ["data/clean.csv"]
    assert all((tmp_path / rel).exists() for rel in (".gitignore", "README.md", "run.sh"))
