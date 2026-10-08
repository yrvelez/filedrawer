"""Power facts for the research-potential memo: the minimum detectable effect of each registered contrast, from the
realized cell sizes and the outcome's spread in data/clean.csv. Pure numpy; nothing here calls a model.

MDE at 80% power, two-sided alpha 0.05, for a difference in means between two cells of sizes n1 and n0 with a pooled
standard deviation s: (1.96 + 0.84) * s * sqrt(1/n1 + 1/n0). Clustered designs are flagged, not corrected."""
from __future__ import annotations

import csv
import math
from pathlib import Path

from .. import pap as P

Z_POWER = 1.96 + 0.8416     # two-sided 0.05, power 0.80


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _sd(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def mde_table(study: Path, pap: dict) -> list[dict]:
    """One row per registered level-vs-reference contrast: cell sizes, pooled SD, the MDE, the observed estimate when
    results/registered_summary.csv has it, and the ratio |estimate| / MDE (below 1: the design could not have detected
    an effect of the size it found)."""
    clean = study / "data" / "clean.csv"
    if not clean.exists():
        return []
    with open(clean, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []
    observed: dict[str, dict] = {}
    summ = study / "results" / "registered_summary.csv"
    if summ.exists():
        with open(summ, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("term"):
                    observed[r["analysis_id"]] = r
    arms_col = (pap.get("design") or {}).get("arms", {}).get("column")
    clustered = any((h.get("estimator") or {}).get("cluster") for h in pap["registered"].get("hypotheses", []))
    out = []
    for h in pap["registered"].get("hypotheses", []):
        t = h.get("treatment") or {}
        col = t.get("column") or arms_col
        y = h.get("outcome")
        if not col or not y or t.get("continuous") or col not in rows[0] or y not in rows[0]:
            continue
        levels, control = P.hypothesis_arms(h)
        if not levels or control is None:
            continue
        by_level: dict[str, list[float]] = {}
        for r in rows:
            v = _num(r.get(y))
            if v is None:
                continue
            by_level.setdefault(P.arm_str(r.get(col)), []).append(v)
        ref = by_level.get(control, [])
        for lvl in levels:
            cell = by_level.get(lvl, [])
            if len(cell) < 2 or len(ref) < 2:
                continue
            s = _sd(cell + ref)
            if not s:
                continue
            mde = Z_POWER * s * math.sqrt(1 / len(cell) + 1 / len(ref))
            key = f"{h['id']}:{lvl}" if len(levels) > 1 or f"{h['id']}:{lvl}" in observed else h["id"]
            obs = observed.get(key) or observed.get(h["id"])
            est = _num(obs.get("estimate")) if obs else None
            out.append({"hypothesis": h["id"], "outcome": y, "level": P.arm_label(pap, lvl), "reference": P.arm_label(pap, control),
                        "n_level": len(cell), "n_reference": len(ref), "sd_outcome": round(s, 4), "mde_80": round(mde, 4),
                        "observed": round(est, 4) if est is not None else None,
                        "ratio_observed_to_mde": round(abs(est) / mde, 2) if est is not None and mde else None,
                        "clustered": clustered})
    return out


def mde_markdown(table: list[dict]) -> str:
    if not table:
        return "(no two-cell contrasts to compute)"
    L = ["| Analysis | Level vs reference | n | SD | MDE (80% power) | Observed | Observed / MDE |", "|---|---|---|---|---|---|---|"]
    for r in table:
        L.append(f"| {r['hypothesis']} | {r['level']} vs {r['reference']} | {r['n_level']} / {r['n_reference']} | {r['sd_outcome']} | "
                 f"{r['mde_80']} | {'' if r['observed'] is None else r['observed']} | {'' if r['ratio_observed_to_mde'] is None else r['ratio_observed_to_mde']} |")
    if table and table[0].get("clustered"):
        L.append("\nStandard errors in the study are clustered; these MDEs ignore clustering and are therefore optimistic.")
    return "\n".join(L)
