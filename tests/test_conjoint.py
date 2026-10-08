"""Conjoint designs: one hypothesis per feature (its own treatment column), continuous treatments,
marginal-mean differences and effect-by-level subgroups, and author-specified exploratory analyses."""
import json

import numpy as np
import pandas as pd
import pytest
import statsmodels.formula.api as smf

from filedrawer import pap as P
from filedrawer import package as PKG
from tests.test_multiarm import run_scripts

FEATS = {"info": ["True", "Misinfo", "Factcheck"], "tie": ["Distant", "Close"], "trust": ["Low", "Mid", "High"]}
EFFECT = {"Misinfo": -0.18, "Factcheck": -0.12, "Close": 0.04, "Mid": 0.02, "High": 0.06}


def make_conjoint(n_resp=600, tasks=4, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(1, n_resp + 1):
        age_t = rng.choice(["Lowest", "Middle", "Highest"])
        lean = rng.uniform(0, 1)
        for t in range(1, tasks + 1):
            prof = [{f: rng.choice(v) for f, v in FEATS.items()} for _ in range(2)]
            util = [sum(EFFECT.get(v, 0.0) for v in p.values()) + (0.10 if age_t == "Highest" and p["info"] == "Misinfo" else 0)
                    + rng.normal(0, 0.3) for p in prof]
            win = int(np.argmax(util))
            for k, p in enumerate(prof):
                rows.append({"resp_id": r, "task": t, "profile": "AB"[k], **p, "age_t": age_t,
                             "trust_num": {"Low": 1, "Mid": 3, "High": 5}[p["trust"]] + rng.integers(0, 2),
                             "lean": lean, "chosen": int(k == win)})
    df = pd.DataFrame(rows)
    p = df["tie"].map(df["tie"].value_counts(normalize=True))
    df["w_tie"] = (1 / p) / (1 / p).mean()
    return df


def feature_h(hid, feat, control):
    others = [f for f in FEATS if f != feat]
    return {"id": hid, "text": f"AMCE of {feat}.", "outcome": "chosen",
            "treatment": {"column": feat, "arms": [v for v in FEATS[feat] if v != control], "control": control},
            "direction": "two_sided", "pooled": False, "subgroup": None, "exclusions": [],
            "estimator": {"kind": "ols", "robust": "HC2", "cluster": "resp_id", "weights": None, "covariates": others,
                          "categorical": others}}


def make_conjoint_pap() -> dict:
    outcome = {"name": "chosen", "label": "Profile chosen", "kind": "single_item", "columns": ["chosen"], "reverse": [],
               "scale": [0, 1], "construction": "forced choice"}
    reg = {"outcomes": [outcome],
           "hypotheses": [feature_h("H1", "info", "True"), feature_h("H2", "tie", "Distant"), feature_h("H3", "trust", "Low")],
           "subgroups": [{"id": "S1", "hypothesis": "H1", "moderator": "age_t", "levels": {"Lowest": "Lowest", "Highest": "Highest"},
                          "expected": "MM differences by age", "estimand": "mm_diff"},
                         {"id": "S2", "hypothesis": "H1", "moderator": "age_t", "levels": {"Lowest": "Lowest", "Highest": "Highest"},
                          "expected": "MM differences by age, all features", "estimand": "mm_diff", "features": list(FEATS)}],
           "sample_exclusions": [], "multiple_testing": "none", "alpha": 0.05}
    rw = feature_h("W2", "tie", "Distant")
    rw["estimator"]["weights"] = "w_tie"; rw["group"] = "RW"; rw["label"] = "Tie (re-weighted)"
    reg["hypotheses"].append(rw)
    reg["groups"] = {"RW": "Re-weighted AMCEs"}
    imp = json.loads(json.dumps(reg))
    # author-specified exploratory analyses: implemented only
    imp["hypotheses"].append({"id": "X1", "text": "Trust slope.", "outcome": "chosen",
                              "treatment": {"column": "trust_num", "continuous": True}, "direction": "two_sided",
                              "subgroup": None, "exclusions": [],
                              "estimator": {"kind": "ols", "robust": "HC2", "cluster": "resp_id", "covariates": ["info", "tie"],
                                            "categorical": ["info", "tie"]}})
    imp["subgroups"].append({"id": "XS1", "hypothesis": "H2", "moderator": "info", "expected": "Tie effect by information type",
                             "estimand": "effect_by", "covariates": True})
    return {"source": "pap.md", "title": "Conjoint", "constructs": [], "keywords": [],
            "design": {"type": "conjoint", "arms": {"column": "info", "treatment": ["Misinfo", "Factcheck"], "control": "True",
                                                     "labels": {}},
                       "features": [{"column": f, "label": f.title(), "levels": v, "randomized": f == "info"} for f, v in FEATS.items()],
                       "population": {"country": "US", "sample": "online_panel"}},
            "registered": reg, "implemented": imp, "ambiguities": []}


@pytest.fixture(scope="module")
def cj(tmp_path_factory):
    study = tmp_path_factory.mktemp("cj") / "study"
    df, pap = make_conjoint(), make_conjoint_pap()
    assert P.validate(pap, list(df.columns)) == []
    run_scripts(study, pap, df)
    summ = pd.read_csv(study / "results" / "registered_summary.csv")
    return {"study": study, "pap": pap, "df": df, "summary": summ}


def test_feature_hypotheses_reproduce_the_joint_additive_model(cj):
    df = cj["df"]
    joint = smf.ols("chosen ~ C(info, Treatment('True')) + C(tie, Treatment('Distant')) + C(trust, Treatment('Low'))",
                    df).fit(cov_type="cluster", cov_kwds={"groups": df["resp_id"]})
    s = cj["summary"].set_index("analysis_id")
    for aid, term in [("H1:Misinfo", "C(info, Treatment('True'))[T.Misinfo]"), ("H1:Factcheck", "C(info, Treatment('True'))[T.Factcheck]"),
                      ("H2:Close", "C(tie, Treatment('Distant'))[T.Close]"), ("H3:High", "C(trust, Treatment('Low'))[T.High]")]:
        assert s.loc[aid, "estimate"] == pytest.approx(joint.params[term], abs=1e-10)
        assert s.loc[aid, "std_error"] == pytest.approx(joint.bse[term], rel=1e-8)
    assert s.loc["H1:Misinfo", "estimate"] == pytest.approx(EFFECT["Misinfo"], abs=0.05)


def test_mm_diff_matches_cell_means(cj):
    df = cj["df"]
    by = pd.read_csv(cj["study"] / "results" / "S1_by_level.csv").set_index("level")
    for lvl in FEATS["info"]:
        hi = df[(df["info"] == lvl) & (df["age_t"] == "Highest")]["chosen"].mean()
        lo = df[(df["info"] == lvl) & (df["age_t"] == "Lowest")]["chosen"].mean()
        assert by.loc[lvl, "difference"] == pytest.approx(hi - lo, abs=1e-10)
        assert by.loc[lvl, "mm_Highest"] == pytest.approx(hi, abs=1e-10)
    assert by.loc["Misinfo", "difference"] > 0.03          # the planted age x misinformation effect
    assert set(by.columns) >= {"std_error", "p_value", "n_Lowest", "n_Highest"}


def test_effect_by_level_and_interactions(cj):
    df = cj["df"]
    by = pd.read_csv(cj["study"] / "results" / "XS1_by_level.csv")
    assert set(by["level"]) == set(FEATS["info"]) and set(by["arm"]) == {"Close"}
    sub = df[df["info"] == "Misinfo"]
    r = smf.ols("chosen ~ C(tie, Treatment('Distant')) + C(trust)", sub).fit(cov_type="cluster", cov_kwds={"groups": sub["resp_id"]})
    got = by[(by["level"] == "Misinfo")]["estimate"].iloc[0]
    assert got == pytest.approx(r.params["C(tie, Treatment('Distant'))[T.Close]"], abs=1e-10)
    inter = cj["summary"][cj["summary"]["analysis_id"] == "XS1"]
    assert len(inter) == 2 and inter["term"].str.contains("Close x").all()


def test_continuous_treatment_is_a_slope(cj):
    df = cj["df"]
    r = smf.ols("chosen ~ trust_num + C(info) + C(tie)", df).fit(cov_type="cluster", cov_kwds={"groups": df["resp_id"]})
    row = cj["summary"].set_index("analysis_id").loc["X1"]
    assert row["estimate"] == pytest.approx(r.params["trust_num"], abs=1e-10)
    assert np.isnan(row["mean_control"]) and bool(row["continuous"])


def test_author_analyses_are_exploratory_and_never_headline(cj):
    tags = {t["analysis_id"]: t["tag"] for t in P.tag_analyses(cj["pap"])}
    assert tags["H1"] == tags["H2"] == tags["H3"] == tags["S1"] == "registered"
    assert tags["X1"] == tags["XS1"] == "exploratory"
    summary = {r["analysis_id"] + ("|" + r["term"] if isinstance(r.get("term"), str) and r["term"] else ""): {k: (str(v) if not isinstance(v, str) else v) for k, v in r.items()}
               for r in cj["summary"].to_dict("records")}
    kf = "\n".join(PKG._key_findings(cj["pap"], summary, max_rows=20))
    assert "(H1)" in kf and "(X1)" not in kf and "XS1" not in kf


def test_author_section_rendering(cj):
    tags = P.tag_analyses(cj["pap"])
    summary = {r["analysis_id"] + ("|" + r["term"] if isinstance(r.get("term"), str) and r["term"] else ""): {k: str(v) for k, v in r.items()}
               for r in cj["summary"].to_dict("records")}
    imp = cj["pap"]["implemented"]
    expl = [h for h in imp["hypotheses"] if P.tag_for(h["id"], tags)["tag"] == "exploratory"]
    block = "\n".join(PKG._hypotheses_block(cj["study"], cj["pap"], tags, expl, [g for g in imp["subgroups"] if g["id"] == "XS1"],
                                            summary, {}, False, "(EXPLORATORY)", "Heterogeneity"))
    assert "Slope of" in block and "`trust_num`" in block and "Close x" in block


def test_mm_diff_over_all_features(cj):
    df = cj["df"]
    by = pd.read_csv(cj["study"] / "results" / "S2_by_level.csv")
    assert set(by["feature"]) == set(FEATS) and len(by) == sum(len(v) for v in FEATS.values())
    row = by[(by["feature"] == "trust") & (by["level"] == "High")].iloc[0]
    hi = df[(df["trust"] == "High") & (df["age_t"] == "Highest")]["chosen"].mean()
    lo = df[(df["trust"] == "High") & (df["age_t"] == "Lowest")]["chosen"].mean()
    assert row["difference"] == pytest.approx(hi - lo, abs=1e-10)


def test_combined_amce_figure_and_group_table(cj):
    assert (cj["study"] / "figures" / "amce_chosen.png").exists()
    tags = P.tag_analyses(cj["pap"])
    imp = cj["pap"]["implemented"]
    reg = [h for h in imp["hypotheses"] if P.tag_for(h["id"], tags)["tag"] != "exploratory"]
    block = "\n".join(PKG._hypotheses_block(cj["study"], cj["pap"], tags, reg, [], {}, {"results": {"RW": "Weights change little."}},
                                            False, "(EXPLORATORY)", "Planned heterogeneity"))
    assert block.count("### Re-weighted AMCEs") == 1 and "Weights change little." in block
    assert "| Profile chosen | Tie | Close |" in block and "(H2)" in block          # unweighted counterpart beside the re-weighted level
    assert "### W2" not in block and "H2_arms.png" not in block           # grouped; no per-feature forest plot in a conjoint


def test_readable_interaction_terms(cj):
    s = cj["summary"]
    xs1 = s[s["analysis_id"] == "XS1"]["term"].tolist()
    assert all(" x " in t and "C(_mod)" not in t for t in xs1)
