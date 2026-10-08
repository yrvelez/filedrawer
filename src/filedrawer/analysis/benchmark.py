"""Synthetic respondents against a human benchmark. When design.sample_kind is synthetic or mixed and
design.synthetic.benchmark_table names an author-supplied CSV with the human study's estimates
(columns analysis_id, estimate, std_error; optional n), every registered estimate is set beside its human counterpart:
the difference, the ratio of standard errors, sign agreement and whether the two 95% intervals overlap.
Deterministic; writes results/benchmark_human.csv."""
from __future__ import annotations

import csv
import math
from pathlib import Path

COLUMNS = ["analysis_id", "synthetic_estimate", "synthetic_se", "human_estimate", "human_se", "difference", "se_ratio",
           "same_sign", "intervals_overlap", "human_n"]


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def compare(study: Path, pap: dict) -> Path | None:
    """results/benchmark_human.csv, or None when the plan names no benchmark table or it cannot be read."""
    syn = (pap.get("design") or {}).get("synthetic") or {}
    rel = syn.get("benchmark_table")
    if not rel:
        return None
    src = Path(rel) if Path(rel).is_absolute() else study / rel
    summ = study / "results" / "registered_summary.csv"
    if not src.exists() or not summ.exists():
        return None
    with open(src, newline="", encoding="utf-8") as fh:
        human = {r.get("analysis_id"): r for r in csv.DictReader(fh)}
    rows = []
    with open(summ, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("term") and not r.get("arm"):          # subgroup interaction rows; arm rows carry a term too
                continue
            h = human.get(r["analysis_id"])
            if not h:
                continue
            se_, he = _num(r.get("estimate")), _num(h.get("estimate"))
            ss, hs = _num(r.get("std_error")), _num(h.get("std_error"))
            if se_ is None or he is None:
                continue
            overlap = None
            if ss is not None and hs is not None:
                overlap = (se_ - 1.96 * ss <= he + 1.96 * hs) and (he - 1.96 * hs <= se_ + 1.96 * ss)
            rows.append({"analysis_id": r["analysis_id"], "synthetic_estimate": round(se_, 4), "synthetic_se": round(ss, 4) if ss is not None else "",
                         "human_estimate": round(he, 4), "human_se": round(hs, 4) if hs is not None else "",
                         "difference": round(se_ - he, 4), "se_ratio": round(ss / hs, 3) if ss and hs else "",
                         "same_sign": (se_ >= 0) == (he >= 0), "intervals_overlap": overlap, "human_n": h.get("n", "")})
    if not rows:
        return None
    out = study / "results" / "benchmark_human.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return out


def markdown(path: Path) -> str:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    L = ["| Analysis | Synthetic | Human | Difference | SE ratio | Same sign | 95% CIs overlap |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        fmt = lambda e, s: f"{e} ({s})" if s not in ("", None) else str(e)
        L.append(f"| {r['analysis_id']} | {fmt(r['synthetic_estimate'], r['synthetic_se'])} | {fmt(r['human_estimate'], r['human_se'])} | "
                 f"{r['difference']} | {r['se_ratio']} | {'yes' if r['same_sign'] == 'True' else 'no'} | "
                 f"{'yes' if r['intervals_overlap'] == 'True' else ('no' if r['intervals_overlap'] == 'False' else '')} |")
    agree = sum(1 for r in rows if r["same_sign"] == "True")
    L.append(f"\n{agree} of {len(rows)} estimates share the human study's sign. Standard errors in parentheses; the SE ratio is "
             f"synthetic over human (below 1: the synthetic sample is over-confident relative to the humans, a known failure mode).")
    return "\n".join(L)
