"""Compact, privacy-preserving data profile: the only data-derived object an agent sees.

Free-text columns are summarized by count and mean length only. Categorical
columns show at most `max_levels` value counts. No cell values from free-text
columns are ever included.
"""
from __future__ import annotations

import json
from pathlib import Path


def profile_df(df, codebook_cols: list[dict] | None = None, max_levels: int = 12,
               free_text: set[str] | None = None, max_columns: int = 80) -> dict:
    import pandas as pd
    free_text = set(free_text or [])
    meta = {c["name"]: c for c in (codebook_cols or [])}
    out = {"n_rows": int(len(df)), "n_cols": int(df.shape[1]), "columns": []}
    for c in list(df.columns)[:max_columns]:
        s = df[c]
        entry = {"name": c, "n_missing": int(s.isna().sum() + (s == "").sum() if s.dtype == object else s.isna().sum())}
        m = meta.get(c, {})
        if m.get("label"):
            entry["label"] = str(m["label"])[:100]
        if c in free_text or m.get("free_text"):
            nonempty = s.astype(str).str.strip().replace("nan", "")
            nonempty = nonempty[nonempty != ""]
            entry.update({"dtype": "free_text", "n_nonempty": int(len(nonempty)),
                          "mean_chars": round(float(nonempty.str.len().mean()), 1) if len(nonempty) else 0.0})
        elif pd.api.types.is_numeric_dtype(s):
            nun = int(s.nunique(dropna=True))
            entry["dtype"] = "numeric"
            entry["n_unique"] = nun
            if nun <= max_levels:
                vc = s.value_counts(dropna=True).sort_index()
                entry["values"] = {str(k): int(v) for k, v in vc.items()}
                if m.get("values"):
                    entry["labels"] = {k: v for k, v in m["values"].items() if k in entry["values"]}
            else:
                d = s.describe()
                entry["summary"] = {k: round(float(d[k]), 3) for k in ("mean", "std", "min", "25%", "50%", "75%", "max") if k in d}
        else:
            nun = int(s.nunique(dropna=True))
            entry["dtype"] = "categorical"
            entry["n_unique"] = nun
            if nun <= max_levels:
                vc = s.value_counts(dropna=True)
                entry["values"] = {str(k)[:40]: int(v) for k, v in vc.items()}
            else:
                entry["note"] = f"{nun} distinct values; not listed"
        out["columns"].append(entry)
    return out


def profile_csv(path: str | Path, **kw) -> dict:
    import pandas as pd
    df = pd.read_csv(path)
    return profile_df(df, **kw)


def profile_to_text(p: dict) -> str:
    lines = [f"N = {p['n_rows']} rows, {p['n_cols']} columns"]
    for c in p["columns"]:
        bits = [f"{c['name']} ({c['dtype']}, missing={c['n_missing']})"]
        if c.get("label"):
            bits.append(f'"{c["label"]}"')
        if "values" in c:
            vals = ", ".join(f"{k}:{v}" for k, v in c["values"].items())
            if c.get("labels"):
                vals += " | " + "; ".join(f"{k}={v}" for k, v in c["labels"].items())
            bits.append("{" + vals + "}")
        if "summary" in c:
            bits.append(json.dumps(c["summary"]))
        if c["dtype"] == "free_text":
            bits.append(f"nonempty={c['n_nonempty']}, mean_chars={c['mean_chars']} (text never shown)")
        lines.append("  " + " ".join(bits))
    return "\n".join(lines)
