"""Compare two results/ folders before a rerun replaces a filed package: every registered estimate must still be there
and unchanged (within a tolerance); exploratory analyses may differ and are only listed."""
from __future__ import annotations

import csv
from pathlib import Path


def _rows(path: Path) -> dict[str, dict]:
    p = path / "registered_summary.csv"
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["analysis_id"] + ("|" + r["term"] if r.get("term") else ""): r for r in csv.DictReader(fh)}


def _tags(path: Path) -> dict[str, str]:
    p = path / "analysis_tags.csv"
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["analysis_id"]: r.get("tag", "") for r in csv.DictReader(fh)}


def compare_results(old: Path, new: Path, tol: float = 1e-6) -> dict:
    a, b = _rows(old), _rows(new)
    ta, tb = _tags(old), _tags(new)
    registered = {k for k in a if ta.get(k.split(":", 1)[0].split("|", 1)[0], "registered") in ("registered", "deviation")}
    missing = sorted(k for k in registered if k not in b)
    changed = []
    for k in sorted(registered & set(b)):
        for col in ("estimate", "std_error"):
            try:
                x, y = float(a[k].get(col) or "nan"), float(b[k].get(col) or "nan")
            except ValueError:
                continue
            if x == x and y == y and abs(x - y) > tol:
                changed.append({"analysis_id": k, "column": col, "old": x, "new": y})
    added = sorted(k for k in b if k not in a)
    exploratory_old = sorted(k for k in a if k not in registered)
    exploratory_new = sorted(k for k in b if tb.get(k.split(":", 1)[0].split("|", 1)[0]) == "exploratory")
    return {"ok": not missing and not changed, "registered_compared": len(registered & set(b)), "missing_registered": missing,
            "changed_registered": changed, "added": added, "exploratory_old": exploratory_old, "exploratory_new": exploratory_new,
            "rows_old": len(a), "rows_new": len(b)}
