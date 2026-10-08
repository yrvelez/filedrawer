import pandas as pd

from filedrawer import pii, tidy
from filedrawer.analysis.profile import profile_df, profile_to_text


def test_read_export_and_reconcile(demo_csv, codebook):
    df, meta = tidy.read_export(demo_csv)
    assert df.shape == (400, 30) and meta["qualtrics_header_rows"] == 3
    assert meta["import_ids"]["imm_att_1"] == "QID9_1"
    rep = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns())
    cols = tidy.reconcile(codebook, df, meta, rep)
    by = {c["name"]: c for c in cols}
    assert by["imm_att_2"]["kind"] == "question" and "reduced" in by["imm_att_2"]["label"]
    assert by["condition"]["kind"] == "embedded"
    assert by["Finished"]["kind"] == "system"
    assert by["open_comment"]["dropped"] and by["pid3"]["values"]["1"] == "Democrat"


def test_write_raw_tidy_drops_pii_and_profile_hides_text(demo_csv, codebook, tmp_path):
    df, meta = tidy.read_export(demo_csv)
    rep = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns())
    cols = tidy.reconcile(codebook, df, meta, rep)
    out = tmp_path / "raw_tidy.csv"
    kept = tidy.write_raw_tidy(df, rep, out)
    assert "IPAddress" not in kept and "open_comment" not in kept and "condition" in kept
    assert pii.assert_no_pii(out) == []
    txt = out.read_text()
    assert "CANARY" not in txt and "203.0.113" not in txt
    prof = profile_df(pd.read_csv(out), cols, free_text=set(codebook.free_text_columns()))
    text = profile_to_text(prof)
    assert "pid3" in text and "Democrat" in text
    assert "CANARY" not in text


def test_profile_free_text_only_counts():
    df = pd.DataFrame({"txt": ["hello CANARY", "", "x"], "k": [1, 2, 2]})
    prof = profile_df(df, free_text={"txt"})
    t = prof["columns"][0]
    assert t["dtype"] == "free_text" and t["n_nonempty"] == 2
    assert "CANARY" not in profile_to_text(prof)


def test_csv_only_codebook(demo_csv):
    df, meta = tidy.read_export(demo_csv)
    cb = tidy.codebook_from_csv(list(df.columns), meta)
    assert cb.survey["source"] == "csv-inferred"
    q = cb.question_by_tag("open_comment")
    assert q.free_text and "views on immigration" in q.text


def test_resolve_arms_from_data(demo_csv, codebook):
    df, _ = tidy.read_export(demo_csv)
    arms = tidy.resolve_arms(codebook, df)
    assert arms.column == "condition" and arms.values == ["control", "treatment"]
