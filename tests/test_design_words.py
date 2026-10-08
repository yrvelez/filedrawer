"""Design-type vocabulary and robustness: words per type, the causal override, observational wording in the
generated script and the report helpers, fixed effects, difference in differences, and the missing-treatment guard."""
import json

import numpy as np
import pandas as pd
import pytest
import statsmodels.formula.api as smf

from filedrawer import __version__
from filedrawer import exec as fexec
from filedrawer import package as PKG
from filedrawer import pap as P
from filedrawer.analysis import templates as T
from tests.test_multiarm import make_data, make_pap, run_scripts


def test_words_by_type_and_causal_override():
    assert P.words({"design": {"type": "survey_experiment"}})["reference"] == "control group"
    w = P.words({"design": {"type": "conjoint"}})
    assert w["unit"] == "level" and w["estimate"] == "AMCE" and w["causal"]
    w = P.words({"design": {"type": "observational_survey"}})
    assert not w["causal"] and w["relation"] == "association" and w["reference"] == "comparison group"
    w = P.words({"design": {"type": "survey_experiment", "causal": False}})      # explicit flag wins over the type
    assert w["relation"] == "association" and w["phrase"] == "survey experiment"
    w = P.words({"design": {"type": "methods_comparison", "causal": False}})     # methods keep their own words
    assert w["reference"] == "benchmark" and w["estimate"] == "discrepancy"
    assert P.words({"design": {"type": "weird"}})["type"] == "other"
    assert P.words({})["phrase"] == "survey experiment"
    assert P.sample_kind({"design": {"sample_kind": "synthetic"}}) == "synthetic"
    assert P.sample_kind({"design": {"sample_kind": "robots"}}) == "human"


def test_validate_new_design_and_estimator_fields():
    pap, cols = make_pap(), list(make_data().columns)
    assert P.validate(pap, cols) == []
    pap["design"]["causal"] = "yes"
    assert any("design.causal" in e for e in P.validate(pap, cols))
    pap["design"]["causal"] = True
    pap["design"]["sample_kind"] = "synthetic"
    assert any("synthetic.model" in e for e in P.validate(pap, cols))
    pap["design"]["synthetic"] = {"model": "some/model"}
    assert P.validate(pap, cols) == []
    h = pap["implemented"]["hypotheses"][0]
    h["estimator"]["absorb"] = ["nope"]
    assert any("absorb column 'nope'" in e for e in P.validate(pap, cols))
    h["estimator"]["absorb"] = ["gender"]
    assert P.validate(pap, cols) == []
    h["estimator"]["kind"] = "did"
    errs = P.validate(pap, cols)
    assert any("estimator.period" in e for e in errs) and any("two-arm" in e for e in errs)


def test_observational_wording_in_helpers_and_script():
    pap = make_pap()
    pap["design"]["type"] = "observational_survey"
    assert "comparison group" in PKG._arms_desc(pap) and "control" not in PKG._arms_desc(pap)
    assert PKG._arms_table([], "Exposure level").startswith("| Exposure level |")
    script = T.render_registered(pap, [], __version__)
    assert "adjusted difference" in script and "association" in script          # the WORDS constant carries the vocabulary


def test_fixed_effects_enter_as_categorical_terms(tmp_path):
    pap, df = make_pap(), make_data()
    h = pap["implemented"]["hypotheses"][0]
    h["estimator"].update({"kind": "ols", "absorb": ["gender"]})
    pap["registered"]["hypotheses"][0]["estimator"].update({"kind": "ols", "absorb": ["gender"]})
    run_scripts(tmp_path / "fe", pap, df)
    s = pd.read_csv(tmp_path / "fe" / "results" / "registered_summary.csv")
    assert s["formula"].str.contains(r"C\(gender\)").all()


def _did_frame(n_units=400, seed=5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    unit = np.repeat(np.arange(n_units), 2)
    post = np.tile([0, 1], n_units)
    treat = np.repeat(rng.integers(0, 2, n_units), 2)
    y = 1 + 0.5 * treat + 0.3 * post + 0.8 * treat * post + rng.normal(0, 1, len(unit))
    return pd.DataFrame({"unit": unit, "post_col": post, "treat_col": treat.astype(int), "y": np.round(y, 6)})


def _did_pap() -> dict:
    outcome = {"name": "y", "label": "Outcome", "kind": "single_item", "columns": ["y"], "reverse": [], "scale": [0, 10], "construction": ""}
    h = {"id": "H1", "text": "Treated units rise more after the change.", "outcome": "y",
         "treatment": {"column": "treat_col", "contrast": ["1", "0"]}, "direction": "positive",
         "estimator": {"kind": "did", "robust": "HC2", "cluster": "unit", "weights": None, "covariates": [], "period": "post_col"},
         "subgroup": None, "exclusions": []}
    sec = {"outcomes": [outcome], "hypotheses": [h], "subgroups": [], "sample_exclusions": [], "multiple_testing": "none", "alpha": 0.05}
    return {"source": "pap.md", "title": "DiD", "constructs": [], "keywords": [],
            "design": {"type": "panel", "arms": {"column": "treat_col", "treatment": "1", "control": "0", "labels": {}},
                       "population": {"country": "US", "sample": "other"}},
            "registered": json.loads(json.dumps(sec)), "implemented": json.loads(json.dumps(sec)), "ambiguities": []}


def test_difference_in_differences_matches_statsmodels(tmp_path):
    df, pap = _did_frame(), _did_pap()
    assert P.validate(pap, list(df.columns)) == []
    assert not P.is_causal(pap)
    run_scripts(tmp_path / "did", pap, df)
    s = pd.read_csv(tmp_path / "did" / "results" / "registered_summary.csv").set_index("analysis_id").loc["H1"]
    ref = smf.ols("y ~ treat_col * post_col", df).fit(cov_type="cluster", cov_kwds={"groups": df["unit"]})
    assert s["estimate"] == pytest.approx(ref.params["treat_col:post_col"], abs=1e-9)
    assert s["std_error"] == pytest.approx(ref.bse["treat_col:post_col"], rel=1e-8)
    assert "treat * post" in s["formula"]
    assert "difference in differences" in "\n".join(PKG._model_block(pap["implemented"]["hypotheses"][0], pap, s.to_dict()))


def test_missing_treatment_indicator_is_a_clear_error(tmp_path):
    pap, df = make_pap(), make_data()
    pap["design"]["arms"] = {}                                            # no assignment column
    for sec in ("registered", "implemented"):
        pap[sec]["hypotheses"][0]["treatment"] = {"column": "cov1"}      # neither a contrast, arms nor continuous
    study = tmp_path / "bad"
    for d in ("data", "scripts", "results", "figures"):
        (study / d).mkdir(parents=True)
    out = df.copy()
    out.insert(0, "row_id", range(1, len(out) + 1))
    out.to_csv(study / "data" / "raw_tidy.csv", index=False)
    (study / "scripts" / "02_clean.py").write_text(T.render_clean(pap, __version__), encoding="utf-8")
    (study / "scripts" / "03_registered.py").write_text(T.render_registered(pap, [], __version__), encoding="utf-8")
    assert fexec.run_script(study, "scripts/02_clean.py", 300)["exit_code"] == 0
    res = fexec.run_script(study, "scripts/03_registered.py", 300)
    assert res["exit_code"] != 0 and "no treatment indicator" in res["stderr_tail"]
