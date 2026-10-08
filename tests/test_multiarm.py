"""Multi-arm experiments: Lin (2013) adjustment, weights, cluster SEs, pooled random-effects summary,
derived columns, registration status and the --pap-json path."""
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from filedrawer import __version__
from filedrawer import exec as fexec
from filedrawer import index as fdindex
from filedrawer import pap as P
from filedrawer import package as PKG
from filedrawer.analysis import templates as T

K = 11
TRUE = {str(a): e for a, e in zip(range(1, K + 1), [0.00, 0.05, 0.10, 0.15, 0.20, -0.05, 0.08, 0.12, 0.03, 0.18, 0.22])}
TOOLS = [f"tool_{i}" for i in range(1, 8)]


def make_data(n=1800, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    probs = np.array([0.16] + [0.08] * 10 + [0.04])     # unequal assignment probabilities (arm 11 is rare)
    arm = rng.choice(np.arange(K + 1), size=n, p=probs)
    cov1 = rng.uniform(-0.5, 0.5, n)            # bounded so the tool-use probability stays inside (0, 1)
    cov2 = rng.uniform(-0.5, 0.5, n)
    latent = 0.6 + np.array([TRUE.get(str(a), 0.0) for a in arm]) + 0.2 * cov1 - 0.1 * cov2 + rng.normal(0, 0.05, n)
    latent = np.clip(latent, 0.01, 0.99)
    df = pd.DataFrame({"participant_id": np.arange(1000, 1000 + n), "treatment": arm.astype(int),
                       "pr": probs[arm], "cov1": cov1, "cov2": cov2, "Finished": 1,
                       "gender": rng.choice([1, 2], size=n), "pid3": rng.choice([1, 2, 3], size=n)})
    for t in TOOLS:
        df[t] = (rng.uniform(size=n) < latent).astype(int)
    return df


def make_pap(registration=None) -> dict:
    arms = [str(a) for a in range(1, K + 1)]
    labels = {"0": "Control", **{a: f"Treatment {a}" for a in arms}}
    outcome = {"name": "ai_scale", "label": "AI tool use (share of 7 tools)", "kind": "mean_items", "columns": TOOLS,
               "reverse": [], "scale": [0, 1], "construction": "mean of seven binary tool-use indicators"}
    h1 = {"id": "H1", "text": "Each treatment increases AI tool use relative to control.", "outcome": "ai_scale",
          "treatment": {"column": "treatment", "arms": arms, "control": "0"}, "direction": "positive",
          "estimator": {"kind": "lin", "robust": "HC2", "cluster": "participant_id", "weights": "ipw",
                        "covariates": ["cov1", "cov2"]},
          "pooled": True, "subgroup": None, "exclusions": ["Finished == 1"]}
    sec = {"outcomes": [outcome], "hypotheses": [h1], "subgroups": [], "sample_exclusions": ["Finished == 1"],
           "multiple_testing": "none", "alpha": 0.05}
    pap = {"source": "pap.md", "title": "Twelve-arm AI tool study", "constructs": [], "keywords": ["ai"],
           "design": {"type": "survey_experiment", "arms": {"column": "treatment", "treatment": arms, "control": "0",
                      "labels": labels, "descriptions": {"0": "Control: no message is shown.", **{a: f"Arm {a} shows message {a}." for a in arms}}},
                      "derived": {"ipw": "1 / pr"},
                      "population": {"country": "US", "sample": "online_panel"}},
           "registered": json.loads(json.dumps(sec)), "implemented": json.loads(json.dumps(sec)), "ambiguities": []}
    if registration:
        pap["registration"] = registration
    return pap


def run_scripts(study: Path, pap: dict, df: pd.DataFrame, categorical=()) -> None:
    for d in ("data", "scripts", "results", "figures"):
        (study / d).mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.insert(0, "row_id", range(1, len(out) + 1))
    out.to_csv(study / "data" / "raw_tidy.csv", index=False)
    (study / "scripts" / "02_clean.py").write_text(T.render_clean(pap, __version__), encoding="utf-8")
    (study / "scripts" / "03_registered.py").write_text(T.render_registered(pap, list(categorical), __version__), encoding="utf-8")
    for name in ("02_clean.py", "03_registered.py"):
        res = fexec.run_script(study, f"scripts/{name}", 300)
        assert res["exit_code"] == 0, res["stderr_tail"]


@pytest.fixture(scope="module")
def multi(tmp_path_factory):
    study = tmp_path_factory.mktemp("multi") / "study"
    df, pap = make_data(), make_pap()
    run_scripts(study, pap, df)
    return {"study": study, "pap": pap, "df": df}


def test_clean_keeps_all_arms_and_derived_column(multi):
    clean = pd.read_csv(multi["study"] / "data" / "clean.csv")
    assert sorted(clean["arm_code"].astype(str).unique(), key=int) == [str(a) for a in range(K + 1)]
    assert set(clean["arm"].unique()) == {"Control"} | {f"Treatment {a}" for a in range(1, K + 1)}
    assert clean["treat"].eq(0).sum() == (clean["arm_code"] == 0).sum()
    assert np.allclose(clean["ipw"], 1 / clean["pr"])
    assert clean["ai_scale"].between(0, 1).all()


def test_arms_table_recovers_effects(multi):
    study = multi["study"]
    arms = pd.read_csv(study / "results" / "H1_arms.csv")
    assert len(arms) == K
    assert list(arms["analysis_id"]) == [f"H1:{a}" for a in range(1, K + 1)]
    assert set(arms.columns) >= {"arm_code", "arm_label", "estimate", "std_error", "p_value", "conf_low", "conf_high",
                                 "n_arm", "mean_control", "mean_arm"}
    for _, r in arms.iterrows():
        assert abs(r["estimate"] - TRUE[str(int(r["arm_code"]))]) < 2.5 * r["std_error"], r.to_dict()
    assert arms["n_arm"].sum() + (pd.read_csv(study / "data" / "clean.csv")["arm_code"] == 0).sum() == 1800
    assert (study / "figures" / "H1_arms.png").exists()
    assert (study / "results" / "H1.csv").exists()
    full = pd.read_csv(study / "results" / "H1.csv")
    assert any("cov1_c" in t and ":" in t for t in full["term"])         # Lin interactions present


def test_registered_summary_rows_and_pooled(multi):
    s = pd.read_csv(multi["study"] / "results" / "registered_summary.csv")
    h1 = s[s["analysis_id"].str.startswith("H1")]
    assert len(h1) == K + 1
    pooled = s[s["analysis_id"] == "H1:pooled"].iloc[0]
    assert np.isfinite(pooled["tau2"]) and 0 <= pooled["i2"] <= 1 and np.isfinite(pooled["p_value"])
    assert pooled["n"] == 1800 and pooled["arm"] == "pooled"
    assert abs(pooled["estimate"] - np.mean(list(TRUE.values()))) < 0.06
    arm_rows = h1[h1["analysis_id"] != "H1:pooled"]
    assert arm_rows["cov_type"].eq("cluster").all() and arm_rows["weights"].eq("ipw").all()
    assert arm_rows["term"].str.contains("Treatment\\(reference='0'\\)").all()
    assert arm_rows["formula"].iloc[0].startswith("ai_scale ~ C(arm_code, Treatment(reference='0')) * (cov1_c + cov2_c)")


def test_tags_inherit_and_study_json_lists_all_entries(multi, tmp_path):
    pap = multi["pap"]
    tags = P.tag_analyses(pap, [])
    assert P.tag_for("H1:3", tags)["tag"] == "registered" and P.tag_for("H1:pooled", tags)["tag"] == "registered"
    assert P.tag_for("H1", tags)["tag"] == "registered" and P.tag_for("Zed:1", tags)["tag"] == "exploratory"
    # a change in weights or arms is a compared field
    imp = json.loads(json.dumps(pap))
    imp["implemented"]["hypotheses"][0]["estimator"]["weights"] = None
    assert P.tag_analyses(imp, [])[0]["tag"] == "deviation"
    imp = json.loads(json.dumps(pap))
    imp["implemented"]["hypotheses"][0]["treatment"]["arms"] = ["1", "2"]
    assert any(d["field"] == "treatment.arms" for d in P.tag_analyses(imp, [])[0]["differences"])
    ctx = _ctx(multi, tags)
    sj = PKG.build_study_json(ctx)
    h1 = [h for h in sj["hypotheses"] if h["id"].startswith("H1")]
    assert len(h1) == K + 1 and {h["id"] for h in h1} == {f"H1:{a}" for a in range(1, K + 1)} | {"H1:pooled"}
    by = {h["id"]: h for h in h1}
    assert by["H1:3"]["text"].endswith("— Treatment 3") and by["H1:3"]["tag"] == "registered"
    assert by["H1:pooled"]["tau2"] is not None and by["H1:pooled"]["k_arms"] == K
    assert sj["design"]["arms"] == [str(a) for a in range(1, K + 1)] + ["0"]
    rep = PKG.render_report(ctx, {})
    assert "H1_arms.png" in rep and "share" in rep.lower() and "Pooling the 11 arm effects" in rep
    assert "Treatment 3" in rep and "cluster" in rep


def _ctx(multi, tags, registration=None):
    pap = json.loads(json.dumps(multi["pap"]))
    if registration:
        pap["registration"] = registration
    return {"study_dir": multi["study"], "pap": pap, "tags": tags, "exploratory": [], "lit": {},
            "cfg": {"report": {"exploratory_suffix": "(EXPLORATORY — not pre-registered)"}},
            "meta": {"slug": "multi", "title": "Twelve-arm AI tool study", "authors": [], "n_raw": 1800, "n_analysis": 1800,
                     "repo_url": "https://github.com/x/y", "branch": "main"},
            "provenance": {"mode": "fully_agentic", "created": "2026-01-01T00:00:00Z", "provider": "mock",
                           "models": {"strong": "m", "fast": "m"}, "human_steps": [], "pii": {"dropped": [], "kept_with_override": []},
                           "pap_source": "author-supplied pap.json"}}


def test_unregistered_status(multi):
    reg = {"status": "none", "url": "", "note": "the authors' analysis memo"}
    pap = json.loads(json.dumps(multi["pap"]))
    pap["registration"] = reg
    tags = P.tag_analyses(pap, [{"id": "E1"}])
    assert [t["tag"] for t in tags] == ["unregistered", "exploratory"]
    assert P.registration_status(multi["pap"]) == "registered"
    ctx = _ctx(multi, tags, reg)
    rep = PKG.render_report(ctx, {})
    assert "No pre-registration. The analysis plan was reconstructed after data collection from the authors' analysis memo" in rep
    assert "Post hoc, not pre-registered" in rep and "Pre-registered." not in rep
    sj = PKG.build_study_json(ctx)
    assert all(h["tag"] == "unregistered" for h in sj["hypotheses"] if h["id"].startswith("H1"))
    counts = fdindex.normalize_study(sj, "local")["hypotheses"]
    assert counts["unregistered"] == K + 1 and counts["registered"] == 0


def test_two_arm_lin_matches_ols_columns(tmp_path):
    df = make_data(n=600, seed=3)
    df = df[df["treatment"].isin([0, 1])].copy()
    pap = make_pap()
    for sec in ("registered", "implemented"):
        h = pap[sec]["hypotheses"][0]
        h["treatment"] = {"column": "treatment", "contrast": ["1", "0"]}
        h.pop("pooled")
        h["estimator"] = {"kind": "lin", "robust": "HC2", "cluster": None, "weights": None, "covariates": ["cov1", "cov2"]}
        pap[sec]["hypotheses"].append(dict(h, id="H2", estimator=dict(h["estimator"], kind="ols")))
    pap["design"]["arms"] = {"column": "treatment", "treatment": "1", "control": "0"}
    study = tmp_path / "two"
    run_scripts(study, pap, df)
    s = pd.read_csv(study / "results" / "registered_summary.csv")
    assert list(s["analysis_id"]) == ["H1", "H2"]
    assert list(s.columns) == ["analysis_id", "outcome", "formula", "cov_type", "estimate", "std_error", "p_value", "n",
                               "mean_control", "mean_treated", "direction", "p_directional", "n_eligible", "supported"]
    assert s.loc[0, "formula"] == "ai_scale ~ treat * (cov1_c + cov2_c)" and s.loc[1, "formula"] == "ai_scale ~ treat + cov1 + cov2"
    assert abs(s.loc[0, "estimate"] - s.loc[1, "estimate"]) < 0.05
    assert not (study / "results" / "H1_arms.csv").exists()


def test_contrast_hypothesis_inside_multiarm_design(tmp_path):
    df, pap = make_data(n=1200, seed=11), make_pap()
    for sec in ("registered", "implemented"):
        h = json.loads(json.dumps(pap[sec]["hypotheses"][0]))
        h.update(id="H2", treatment={"column": "treatment", "contrast": ["5", "0"]},
                 estimator={"kind": "ols", "robust": "HC2", "cluster": None, "weights": None, "covariates": []})
        h.pop("pooled")
        pap[sec]["hypotheses"].append(h)
    study = tmp_path / "mix"
    run_scripts(study, pap, df)
    s = pd.read_csv(study / "results" / "registered_summary.csv")
    h2 = s[s["analysis_id"] == "H2"].iloc[0]
    clean = pd.read_csv(study / "data" / "clean.csv")
    assert h2["n"] == clean["arm_code"].isin([0, 5]).sum()                  # only arms 5 and 0 enter the model
    assert abs(h2["estimate"] - TRUE["5"]) < 3 * h2["std_error"]
    assert len(s[s["analysis_id"].str.startswith("H1")]) == K + 1


def test_validate_and_normalize_arms():
    pap = make_pap()
    pap["design"]["arms"]["treatment"] = list(range(1, K + 1))
    pap["implemented"]["hypotheses"][0]["treatment"]["arms"] = [1.0, 2, "3"]
    pap["implemented"]["hypotheses"][0]["treatment"]["control"] = 0
    P.normalize_arms(pap)
    assert pap["design"]["arms"]["treatment"][:3] == ["1", "2", "3"]
    assert pap["implemented"]["hypotheses"][0]["treatment"] == {"column": "treatment", "arms": ["1", "2", "3"], "control": "0"}
    cols = ["treatment", "pr", "cov1", "cov2", "participant_id"] + TOOLS
    assert P.validate(pap, cols) == []                      # ipw is a derived column
    pap["implemented"]["hypotheses"][0]["estimator"]["weights"] = "nope"
    assert any("weights column 'nope'" in e for e in P.validate(pap, cols))


def test_resolve_arms_normalizes_numeric_codes():
    from filedrawer.qsf import Codebook, Arms
    from filedrawer.tidy import resolve_arms
    cb = Codebook(survey={}, questions=[], arms=Arms())
    df = pd.DataFrame({"treatment": [0.0, 1.0, 10.0, 2.0, np.nan, 11.0]})
    arms = resolve_arms(cb, df, pap_arm_column="treatment")
    assert arms.column == "treatment" and arms.values == ["0", "1", "2", "10", "11"] and arms.source == "pap"


def test_pipeline_with_pap_json_and_arm_column(tmp_path):
    """filedrawer run --arm-column treatment --pap-json plan.json on the 12-arm data (no QSF, mock provider)."""
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    df, pap = make_data(), make_pap({"status": "none", "url": "", "note": "the authors' analysis memo"})
    csv_path = tmp_path / "export.csv"
    df.to_csv(csv_path, index=False)
    (tmp_path / "pap.md").write_text("# Plan\nTwelve arms.\n")
    (tmp_path / "plan.json").write_text(json.dumps(pap))
    cfg = load_config(overrides={"provider": "mock"})
    study = run_pipeline({"csv": str(csv_path), "qsf": None, "pap": str(tmp_path / "pap.md"), "pap_json": str(tmp_path / "plan.json"),
                          "slug": "twelve", "title": "Twelve arms", "authors": [], "out_dir": str(tmp_path / "s")}, cfg,
                         {"arm_column": "treatment", "no_lit": True, "no_exploratory": True,
                          "pii_keep": ["participant_id"]})
    prov = json.loads((study / "provenance" / "provenance.json").read_text())
    assert prov["pap_source"] == "author-supplied pap.json" and "papreader" not in prov.get("usage_by_agent", {})
    sj = json.loads((study / "study.json").read_text())
    assert len([h for h in sj["hypotheses"] if h["id"].startswith("H1")]) == K + 1
    assert (study / "figures" / "H1_arms.png").exists() and (study / "results" / "H1_arms.csv").exists()
    rep = (study / "report.md").read_text()
    assert "No pre-registration" in rep and "Post hoc, not pre-registered" in rep
    tags = (study / "results" / "analysis_tags.csv").read_text()
    assert "H1,hypothesis,unregistered" in tags
    bad = json.loads(json.dumps(pap)); bad["implemented"]["hypotheses"][0]["estimator"]["covariates"] = ["missing_col"]
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="missing_col"):
        run_pipeline({"csv": str(csv_path), "qsf": None, "pap": str(tmp_path / "pap.md"), "pap_json": str(tmp_path / "bad.json"),
                      "slug": "bad", "title": "Bad", "authors": [], "out_dir": str(tmp_path / "s")}, cfg,
                     {"arm_column": "treatment", "no_lit": True, "no_exploratory": True,
                      "pii_keep": ["participant_id"]})



def test_continuous_override_removes_dummies(tmp_path):
    """estimator.continuous makes a codebook-labelled ordinal covariate enter linearly (and centred, under Lin)."""
    import json as _json
    import pandas as pd
    from filedrawer.analysis import templates as T
    from filedrawer import exec as X
    rng = __import__("numpy").random.default_rng(3)
    n = 400
    df = pd.DataFrame({"row_id": range(1, n + 1), "treatment": rng.integers(0, 2, n), "interest": rng.integers(1, 6, n), "x": rng.normal(size=n)})
    df["y"] = 0.3 * df.treatment + 0.1 * df.interest + rng.normal(size=n)
    study = tmp_path / "s"
    for d in ("data", "scripts", "results", "figures"):
        (study / d).mkdir(parents=True)
    df.to_csv(study / "data" / "raw_tidy.csv", index=False)
    base = {"name": "y", "kind": "single_item", "columns": ["y"], "reverse": [], "scale": [0, 1]}
    hyp = {"id": "H1", "text": "t", "outcome": "y", "treatment": {"column": "treatment", "contrast": ["1", "0"]}, "direction": "positive",
           "estimator": {"kind": "lin", "robust": "HC2", "cluster": None, "covariates": ["interest", "x"], "continuous": ["interest"]}, "exclusions": []}
    pap = {"design": {"type": "survey_experiment", "arms": {"column": "treatment", "treatment": "1", "control": "0"}},
           "registered": {"outcomes": [base], "hypotheses": [hyp], "subgroups": [], "sample_exclusions": [], "alpha": 0.05},
           "implemented": {"outcomes": [base], "hypotheses": [hyp], "subgroups": [], "sample_exclusions": [], "alpha": 0.05}, "ambiguities": []}
    (study / "scripts" / "02_clean.py").write_text(T.render_clean(pap, "t"))
    (study / "scripts" / "03_registered.py").write_text(T.render_registered(pap, ["interest"], "t"))   # codebook says categorical
    for s_ in ("02_clean.py", "03_registered.py"):
        r = X.run_script(study, f"scripts/{s_}", 120)
        assert r["exit_code"] == 0, r["stderr_tail"]
    terms = pd.read_csv(study / "results" / "H1.csv")["term"].tolist()
    assert "interest_c" in terms and not any(t.startswith("interest_2") for t in terms)
    summ = pd.read_csv(study / "results" / "registered_summary.csv")
    assert "interest_c" in summ["formula"].iloc[0] and abs(summ["estimate"].iloc[0] - 0.3) < 0.25
