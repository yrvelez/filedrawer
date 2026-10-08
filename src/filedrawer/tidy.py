"""Read a Qualtrics (or plain) CSV export, reconcile it with the codebook, strip PII,
and write data/raw_tidy.csv. Also builds a degraded codebook when no QSF is given."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .pii import PiiReport, KNOWN_ID_COLS, detect_qualtrics_header_rows, _read_head
from .qsf import Codebook, Question, Arms, Choice, strip_html
from .pap import arm_str

QUALTRICS_SYSTEM = set(KNOWN_ID_COLS) | {"Finished", "UserLanguage", "Progress", "Duration (in seconds)", "Status", "DistributionChannel"}


def read_export(path: str | Path):
    """Return (DataFrame, meta) where meta has question_text and import_ids per column."""
    import pandas as pd
    path = Path(path)
    head = _read_head(path, 4)
    n_meta = detect_qualtrics_header_rows(head)
    meta: dict[str, Any] = {"qualtrics_header_rows": n_meta, "question_text": {}, "import_ids": {}}
    header = head[0]
    if n_meta == 3:
        meta["question_text"] = {c: head[1][i] if i < len(head[1]) else "" for i, c in enumerate(header)}
        for i, c in enumerate(header):
            try:
                meta["import_ids"][c] = json.loads(head[2][i]).get("ImportId", "")
            except Exception:
                meta["import_ids"][c] = ""
    df = pd.read_csv(path, skiprows=list(range(1, n_meta)) if n_meta > 1 else None,
                     dtype=str, keep_default_na=False, encoding="utf-8-sig")
    # coerce numeric-looking columns
    for c in df.columns:
        s = df[c].replace("", None)
        conv = pd.to_numeric(s, errors="coerce")
        if s.notna().sum() > 0 and conv.notna().sum() == s.notna().sum():
            df[c] = conv
        else:
            df[c] = s
    return df, meta


def codebook_from_csv(header: list[str], meta: dict) -> Codebook:
    """Degraded codebook when no QSF is available: labels from the question-text row only."""
    qs = []
    for c in header:
        if c in QUALTRICS_SYSTEM:
            continue
        qid = meta.get("import_ids", {}).get(c) or c
        qs.append(Question(qid=qid, tag=c, type="unknown", selector="", text=strip_html(meta.get("question_text", {}).get(c, "")),
                           columns=[c], free_text=qid.endswith("_TEXT")))
    return Codebook(survey={"name": "", "id": "", "source": "csv-inferred"}, questions=qs, arms=Arms())


def reconcile(cb: Codebook, df, meta: dict, pii: PiiReport) -> list[dict]:
    """Fill cb.columns: one entry per CSV column with kind/dtype/label/values/pii flags."""
    import pandas as pd
    import_ids = meta.get("import_ids", {})
    cols = []
    for c in df.columns:
        q = cb.column_question(c)
        if q is None and import_ids.get(c):
            iid = import_ids[c]
            base = iid.split("_")[0]
            for qq in cb.questions:
                if qq.qid == base:
                    q = qq
                    if c not in qq.columns:
                        qq.columns.append(c)
                    break
        kind = "question" if q else ("embedded" if c in cb.embedded_data or c == cb.arms.column
                                      else ("system" if c in QUALTRICS_SYSTEM else "unknown"))
        values: dict[str, str] = {}
        label = ""
        if q:
            label = q.text
            if q.type == "Matrix":
                m = re.match(rf"{re.escape(q.tag)}_(\w+)$", c)
                if m:
                    row = next((r for r in q.rows if r.id == m.group(1)), None)
                    if row:
                        label = f"{q.text} — {row.label}"
                values = {(ch.recode or ch.id): ch.label for ch in q.choices}
            elif q.type == "MC":
                values = {(ch.recode or ch.id): ch.label for ch in q.choices}
        elif meta.get("question_text", {}).get(c):
            label = strip_html(meta["question_text"][c])
        dtype = "numeric" if pd.api.types.is_numeric_dtype(df[c]) else "text"
        cols.append({"name": c, "qid": q.qid if q else "", "kind": kind, "dtype": dtype, "label": label,
                     "values": values, "free_text": bool(q and q.free_text) or c in cb.free_text_columns(),
                     "pii_flag": c in pii.flagged, "pii_reason": pii.flagged.get(c, ""),
                     "dropped": c in pii.to_drop})
    cb.columns = cols
    return cols


def write_raw_tidy(df, pii: PiiReport, out_path: str | Path) -> list[str]:
    keep = [c for c in df.columns if c not in pii.to_drop]
    out = df[keep].copy()
    out.insert(0, "row_id", range(1, len(out) + 1))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    return keep


def observed_arm_values(series) -> list[str]:
    """Distinct arm values as canonical strings ("1" for 1, 1.0 and "1.0"), numeric codes sorted numerically."""
    vals = {arm_str(v) for v in series.dropna().unique()}
    vals.discard("")

    def key(v: str):
        try:
            return (0, float(v), v)
        except ValueError:
            return (1, 0.0, v)
    return sorted(vals, key=key)


CONVENTIONAL_ARM_COLUMNS = ("treatment", "treat", "condition", "arm", "treatment_arm", "assignment")


def resolve_arms(cb: Codebook, df, pap_arm_column: str | None = None) -> Arms:
    """Make sure the codebook's arm column exists in the data; fall back to the PAP's column.
    Arm values are canonical strings (see pap.arm_str)."""
    arms = cb.arms
    if arms.column and arms.column in df.columns and arms.source == "flow_embedded":
        observed = observed_arm_values(df[arms.column])
        arms.values = [v for v in (arm_str(x) for x in arms.values) if v in observed] or observed
        return arms
    if arms.source == "inferred":
        # arm from non-missingness of block-specific question columns (DB-only blocks have none)
        parts = {}
        for arm, qids in arms.blocks.items():
            cols = [c for q in cb.questions if q.qid in qids for c in q.columns if c in df.columns]
            if cols:
                parts[arm] = df[cols].notna().any(axis=1)
        if parts and len(parts) == len(arms.blocks):
            df["arm"] = None
            for arm, mask in parts.items():
                df.loc[mask, "arm"] = arm
            arms.column = "arm"
            arms.values = sorted(parts)
            return arms
    if pap_arm_column and pap_arm_column in df.columns:
        arms.column = pap_arm_column
        arms.values = observed_arm_values(df[pap_arm_column])
        arms.source = "pap"
        return arms
    if arms.column and arms.column in df.columns:
        arms.values = observed_arm_values(df[arms.column])
        return arms
    for name in CONVENTIONAL_ARM_COLUMNS:
        hit = next((c for c in df.columns if str(c).lower() == name), None)
        if hit is not None:
            vals = observed_arm_values(df[hit])
            if 2 <= len(vals) <= 30:
                print(f"[filedrawer] arms: using column '{hit}' ({len(vals)} values) by naming convention; "
                      f"pass --arm-column to override", flush=True)
                arms.column, arms.values, arms.source = hit, vals, "convention"
                return arms
    raise SystemExit("Could not determine treatment arms: no randomizer embedded data in the QSF, "
                     "no arm-specific question columns, and no arm column named in the PAP. "
                     "Add `treatment.column` to the PAP or an `arms.column` override.")
