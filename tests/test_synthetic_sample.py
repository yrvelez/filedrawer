"""Synthetic-respondent studies: the plan flags, the mandatory limitations paragraph and byline, the badge, and the
comparison with a human benchmark table."""
import csv
import json

import pandas as pd
import pytest

from filedrawer import badges as B
from filedrawer import pap as P
from filedrawer.analysis import benchmark
from tests.test_multiarm import make_data, make_pap, run_scripts


def _synthetic_pap(tmp_path):
    pap = make_pap()
    pap["design"].update(sample_kind="synthetic", synthetic={"model": "demo/llm-7b", "personas": 60, "generated_on": "2026-09-01",
                                                             "benchmark_human_study_url": "https://example.org/human-study",
                                                             "benchmark_table": "inputs/human_estimates.csv"})
    return pap


def test_validation_and_words(tmp_path):
    pap = _synthetic_pap(tmp_path)
    assert P.validate(pap, list(make_data().columns)) == [] and P.sample_kind(pap) == "synthetic"
    pap["design"]["synthetic"] = {}
    assert any("synthetic.model" in e for e in P.validate(pap, list(make_data().columns)))


def test_benchmark_table_and_markdown(tmp_path):
    pap, df = _synthetic_pap(tmp_path), make_data()
    study = tmp_path / "syn"
    run_scripts(study, pap, df)
    summ = pd.read_csv(study / "results" / "registered_summary.csv")
    arms = summ[summ["arm"].astype(str).str.match(r"^\d+$")]
    (study / "inputs").mkdir()
    with open(study / "inputs" / "human_estimates.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["analysis_id", "estimate", "std_error", "n"])
        for _, r in arms.iterrows():
            w.writerow([r["analysis_id"], round(float(r["estimate"]) + 0.03, 4), round(float(r["std_error"]) * 2, 4), 900])
    out = benchmark.compare(study, pap)
    assert out is not None and out.name == "benchmark_human.csv"
    rows = list(csv.DictReader(open(out)))
    assert len(rows) == len(arms) and all(abs(float(r["difference"]) + 0.03) < 1e-3 for r in rows)
    assert all(abs(float(r["se_ratio"]) - 0.5) < 1e-2 for r in rows) and all(r["intervals_overlap"] == "True" for r in rows)
    md = benchmark.markdown(out)
    assert "| Analysis | Synthetic | Human |" in md and "over-confident" in md
    assert benchmark.compare(study, make_pap()) is None                        # no benchmark named


def test_badges_and_report_text_for_synthetic(tmp_path):
    pap = _synthetic_pap(tmp_path)
    sj = {"provenance": {"mode": "fully_agentic"}, "design": pap["design"], "registration": {"status": "registered"}, "hypotheses": [{"id": "H1"}]}
    by = {b["name"]: b for b in B.badges_for(sj)}
    assert by["sample"]["value"] == "synthetic (LLM)" and by["data"]["value"] == "synthetic"
    from filedrawer import package as PKG
    assert "comparison group" not in PKG._arms_desc(pap)                      # synthetic does not change the causal words
    w = P.words(pap)
    assert w["sample_kind"] == "synthetic" and w["causal"]
