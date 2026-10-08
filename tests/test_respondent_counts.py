"""N counts respondents, not rows, when each respondent contributes several rows (conjoint profiles)."""
import pandas as pd

from filedrawer.orchestrator import respondent_counts


def pap(cluster):
    return {"implemented": {"hypotheses": [{"id": "H1", "estimator": {"kind": "ols", "cluster": cluster}}]}}


def test_conjoint_counts_respondents():
    raw = pd.DataFrame({"resp_id": [i // 8 for i in range(1600)], "y": 0})
    clean = raw[raw.resp_id < 150]
    got = respondent_counts(pap("resp_id"), raw, clean)
    assert got == {"n_raw": 200, "n_analysis": 150, "unit_column": "resp_id", "rows_raw": 1600, "rows_analysis": 1200}


def test_one_row_per_respondent_counts_rows():
    raw = pd.DataFrame({"resp_id": range(50), "y": 0})
    assert respondent_counts(pap("resp_id"), raw, raw) == {"n_raw": 50, "n_analysis": 50}
    assert respondent_counts(pap(None), raw, raw.head(40)) == {"n_raw": 50, "n_analysis": 40}
