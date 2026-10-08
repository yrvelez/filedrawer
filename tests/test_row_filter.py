"""Script output shown to a model must never reproduce individual data rows (exec.model_view)."""
import json
import textwrap

import numpy as np
import pandas as pd
import pytest

from filedrawer import exec as fexec
from filedrawer.tools import ToolRegistry


@pytest.fixture
def study(tmp_path):
    rng = np.random.default_rng(7)
    n = 2000
    df = pd.DataFrame({
        "resp_id": np.arange(1, n + 1),
        "arm": rng.choice(["control", "treatment_a", "treatment_b"], n),
        "age": rng.integers(18, 95, n),
        "weight": np.round(rng.lognormal(0, 0.4, n), 6),
        "income_k": np.round(rng.gamma(2, 30, n), 2),
        "party": rng.choice(["Democrat", "Republican", "Independent"], n),
        "likert1": rng.integers(1, 6, n),
        "likert2": rng.integers(1, 6, n),
        "outcome": rng.integers(0, 2, n),
    })
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    df.to_csv(tmp_path / "data" / "clean.csv", index=False)
    return tmp_path


def _run(study, body):
    (study / "scripts" / "s.py").write_text(
        "import sys, json\nimport pandas as pd\ndf = pd.read_csv('data/clean.csv')\n" + textwrap.dedent(body))
    raw = fexec.run_script(study, "scripts/s.py", timeout_s=60)
    assert raw["exit_code"] == 0, raw["stderr_tail"]
    return fexec.model_view(raw, study)


def _leaks(study, text, rows=range(30)):
    """Rows (of the first 30) for which the text shows two identifying values (weight, income)."""
    df = pd.read_csv(study / "data" / "clean.csv")
    bad = []
    for i in rows:
        r = df.iloc[i]
        shown = [f"{r.weight:.4f}"[:-1] in text, f"{r.income_k:.2f}" in text]
        if sum(shown) >= 2:
            bad.append(i)
    return bad


@pytest.mark.parametrize("body", [
    "print(df.head(20))",
    "print(df.head(20).to_string(index=False))",
    "print(df.head(5).to_json(orient='records'))",
    "for _, r in df.head(10).iterrows(): print(dict(r))",
    "print(df.iloc[3])",                                   # one row, printed vertically
    "print(df.sample(12, random_state=1)[['resp_id', 'party', 'age', 'weight', 'income_k']])",
    "print(df.head(15).to_csv(index=False))",
])
def test_row_dumps_withheld_from_stdout(study, body):
    v = _run(study, body)
    assert v.get("lines_withheld", 0) > 0
    assert _leaks(study, v["stdout_tail"]) == []
    assert "withheld" in v["stdout_tail"]


def test_row_dump_on_stderr_withheld(study):
    v = _run(study, "sys.stderr.write(df.head(20).to_string())")
    assert v.get("lines_withheld", 0) > 0 and _leaks(study, v["stderr_tail"]) == []


def test_vertical_single_row_leaks_nothing_identifying(study):
    v = _run(study, "print(df.iloc[3])")
    r = pd.read_csv(study / "data" / "clean.csv").iloc[3]
    assert f"{r.income_k:.2f}" not in v["stdout_tail"] or f"{r.weight}" not in v["stdout_tail"]


def test_rows_of_common_values_withheld(tmp_path):
    rng = np.random.default_rng(3)
    df = pd.DataFrame({f"q{i}": rng.integers(1, 6, 500) for i in range(10)})
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    df.to_csv(tmp_path / "data" / "clean.csv", index=False)
    (tmp_path / "scripts" / "s.py").write_text("import pandas as pd\nprint(pd.read_csv('data/clean.csv').head(8))")
    v = fexec.model_view(fexec.run_script(tmp_path, "scripts/s.py", 60), tmp_path)
    assert v.get("lines_withheld", 0) >= 8


def test_raw_export_in_inputs_is_covered(study):
    (study / "inputs").mkdir()
    raw = pd.read_csv(study / "data" / "clean.csv").assign(email=lambda d: [f"p{i}@x.org" for i in d.resp_id])
    raw.to_csv(study / "inputs" / "export.csv", index=False)
    (study / "data" / "clean.csv").unlink()
    v = _run_raw(study, "print(pd.read_csv('inputs/export.csv').head(10))")
    assert v.get("lines_withheld", 0) > 0 and "@x.org" not in v["stdout_tail"]


def _run_raw(study, body):
    (study / "scripts" / "s.py").write_text("import pandas as pd\n" + body)
    return fexec.model_view(fexec.run_script(study, "scripts/s.py", timeout_s=60), study)


@pytest.mark.parametrize("body", [
    "print(df.groupby('arm').outcome.mean())",
    "print(df.groupby(['arm', 'party']).outcome.agg(['mean', 'count']))",
    "print(f'N={len(df)}; mean age {df.age.mean():.1f}; share treated {df.arm.ne(\"control\").mean():.3f}')",
    "print(df.describe().round(3))",
    "import statsmodels.formula.api as smf\n"
    "m = smf.ols('outcome ~ C(arm) + age + likert1', df).fit(cov_type='HC2')\n"
    "print(m.params.round(4)); print(m.bse.round(4)); print('N', int(m.nobs))",
    "print(pd.crosstab(df.arm, df.party))",
    "print(df.columns.tolist())",
])
def test_aggregates_pass_through(study, body):
    v = _run(study, body)
    assert "lines_withheld" not in v, v["stdout_tail"]


def test_error_messages_pass_through(study):
    (study / "scripts" / "s.py").write_text("import pandas as pd\ndf = pd.read_csv('data/clean.csv')\ndf['no_such_col']")
    v = fexec.model_view(fexec.run_script(study, "scripts/s.py", 60), study)
    assert v["exit_code"] != 0 and "no_such_col" in v["stderr_tail"] and "lines_withheld" not in v


def test_tool_channel_is_filtered(study):
    t = ToolRegistry(study, timeout_s=60)
    t.write_file("scripts/leak.py", "import pandas as pd\nprint(pd.read_csv('data/clean.csv').head(25))")
    out = t.run_script("scripts/leak.py")
    res = json.loads(out.split("\nNOTE:")[0])
    assert res["lines_withheld"] > 0 and "NOTE: some output lines were withheld" in out
    assert _leaks(study, out) == []


def test_no_data_files_means_no_filtering(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "s.py").write_text("print('hello 12.5 99.25')")
    v = fexec.model_view(fexec.run_script(tmp_path, "scripts/s.py", 60), tmp_path)
    assert v["stdout_tail"].strip() == "hello 12.5 99.25"


# ---- long conjoint-style data: 8 rows per respondent, weights shared within cells, sequential ids,
# ---- scales in sixths, a frame wide enough that pandas wraps it. Values repeat within respondents,
# ---- so no single value is unique; rows are identified jointly.

@pytest.fixture
def long_study(tmp_path):
    rng = np.random.default_rng(11)
    n_resp, per = 1000, 8
    cells = np.round(rng.lognormal(0, 0.4, 150), 12)
    resp = pd.DataFrame({"resp_id": np.arange(1, n_resp + 1), "weight": rng.choice(cells, n_resp),
                         "age": rng.integers(18, 95, n_resp), "media_trust": np.round(rng.integers(6, 31, n_resp) / 6, 4),
                         "disputed_self": np.round(rng.integers(0, 7, n_resp) / 6, 4),
                         "survey_language": rng.choice(["English", "Spanish"], n_resp)})
    df = resp.loc[resp.index.repeat(per)].reset_index(drop=True)
    df.insert(0, "row_id", np.arange(1, len(df) + 1))
    df["trial"] = np.tile(np.repeat([1, 2, 3, 4], 2), n_resp)
    df["infotype"] = rng.choice(["True/neutral", "Misinformation", "Fact-check"], len(df))
    df["headline_theme"] = rng.choice(["Tucker Carlson", "Biden Budget", "Guanabana", "Polio", "Debt"], len(df))
    for c in ("close_tie", "native_us", "chosen_share", "chosen_belief"):
        df[c] = rng.integers(0, 2, len(df)).astype(float)
    for c in ("sharer_trust", "sharer_knowledge"):
        df[c] = rng.integers(1, 6, len(df)).astype(float)
    df["sharer_disputed"] = np.round(rng.integers(0, 7, len(df)) / 6, 4)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    df.to_csv(tmp_path / "data" / "clean.csv", index=False)
    return tmp_path


def _long(study, body):
    (study / "scripts" / "s.py").write_text("import pandas as pd\ndf = pd.read_csv('data/clean.csv')\n" + textwrap.dedent(body))
    raw = fexec.run_script(study, "scripts/s.py", timeout_s=120)
    assert raw["exit_code"] == 0, raw["stderr_tail"]
    return raw, fexec.model_view(raw, study)


def _row_shown(study, text, rows=range(40)):
    """Rows of which a shown line carries the full-precision weight together with the age."""
    df = pd.read_csv(study / "data" / "clean.csv")
    return [i for i in rows for ln in text.splitlines()
            if f"{df.weight[i]:.6f}"[:7] in ln and f" {int(df.age[i])}" in ln]


@pytest.mark.parametrize("body", [
    "print(df.head(10))",                                   # wide: wrapped into chunks by pandas
    "print(df.head(10).to_string(index=False))",
    "print(df.head(3).T)",
    "print(df.head(3).values.tolist())",
    "r = df.iloc[9]; print(f'{r.resp_id} {r.age} {r.weight} {r.media_trust} {r.disputed_self} {r.chosen_share}')",
    "for i in range(5): print(i, df.weight[i], df.age[i], df.media_trust[i])",
    "print(df.head(200).to_string())",                       # long: filtered before trimming
])
def test_long_data_row_dumps_withheld(long_study, body):
    raw, v = _long(long_study, body)
    assert v.get("lines_withheld", 0) > 0
    assert _row_shown(long_study, v["stdout_tail"]) == []


@pytest.mark.parametrize("body", [
    "print(df.describe())",
    "print(df.describe().round(3).to_string())",
    "print(df[['weight','media_trust','age']].quantile([.01,.1,.5,.9,.99]))",
    "print(df.groupby('headline_theme')[['chosen_share','sharer_trust','age']].mean().round(3))",
    "print(df[['sharer_trust','age','weight','media_trust']].corr().round(3))",
    "print(f'N={len(df)}, respondents={df.resp_id.nunique()}, mean age {df.age.mean():.2f}, mean weight {df.weight.mean():.4f}')",
    "df.info()",
    # estimate tables: scientific-notation p-values, a label that is also a data value, counts
    "print('arm,estimate,std_error,p_value,headline_theme,n'); print('Misinformation,-0.1950,0.0476,4.17e-05,Polio,775')",
    "print('| Fact-check | -0.104 | 0.025 | 3.74e-05 | -0.153 | -0.054 | 3 | 2973 |')",
    "print('One row per shown profile: 1,000 respondents x 4 trials x 2 profiles = 8,000')",
])
def test_long_data_aggregates_pass_through(long_study, body):
    raw, v = _long(long_study, body)
    assert "lines_withheld" not in v, v["stdout_tail"]


def test_private_full_output_never_reaches_model(long_study):
    raw, v = _long(long_study, "print(df.head(3))")
    assert "_stdout" in raw and not any(k.startswith("_") for k in v)


@pytest.mark.skipif(__import__("shutil").which("Rscript") is None, reason="Rscript not installed")
def test_r_script_output_is_filtered_too(long_study):
    """Author-supplied R scripts (methods packages, language=auto) go through the same row filter."""
    (long_study / "scripts" / "dump.R").write_text(
        'd <- read.csv("data/clean.csv"); print(head(d, 10)); cat("mean age", mean(d$age), "\\n")\n')
    t = ToolRegistry(long_study, timeout_s=120, language="auto")
    out = t.run_script("scripts/dump.R")
    res = json.loads(out.split("\nNOTE:")[0])
    assert res["exit_code"] == 0 and res["lines_withheld"] > 0
    assert "mean age" in res["stdout_tail"] and _row_shown(long_study, out) == []
