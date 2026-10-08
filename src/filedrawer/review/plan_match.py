"""Plan match, the deterministic half of the Light Pass: every analysis in the pre-analysis plan against what was run.

No model call. From the plan (pap.json), the analysis tags and the result tables it checks that
  - every registered hypothesis was run and has an estimate,
  - every analysis that differs from the plan is tagged a deviation and carries a stated reason,
  - nothing exploratory is tagged as registered.
The other half (reported estimates against the tables) is the light referee's job.
"""
from __future__ import annotations

FIELD_WORDS = {"estimator.kind": "estimator", "estimator.robust": "standard errors", "estimator.covariates": "covariates",
               "estimator.weights": "weights", "estimator.cluster": "clustering", "exclusions": "exclusions",
               "outcome": "outcome", "treatment": "treatment", "pooled": "pooling", "direction": "test direction"}


def plan_match(pap: dict, tags: list[dict], summary_rows: list[dict]) -> dict:
    registered = [h for h in (pap.get("registered") or {}).get("hypotheses") or [] if h.get("id")]
    implemented = {h["id"]: h for h in (pap.get("implemented") or {}).get("hypotheses") or [] if h.get("id")}
    tag_by = {t.get("analysis_id"): t for t in tags or []}
    with_estimate = {str(r.get("analysis_id", "")).split(":", 1)[0] for r in summary_rows or []
                     if str(r.get("estimate", "")).strip() not in ("", "nan", "NA")}
    rows, problems = [], []
    for h in registered:
        hid = h["id"]
        t = tag_by.get(hid) or {}
        ran, est = hid in implemented, hid in with_estimate
        diffs = [FIELD_WORDS.get(d.get("field"), str(d.get("field", "")).split(".")[-1].replace("_", " ")) for d in t.get("differences") or []]
        status = "as planned" if ran and est and not diffs else ("deviation" if ran and est else "not run" if not ran else "no estimate")
        reason = (t.get("justification") or "").strip()
        row = {"id": hid, "status": status, "differences": diffs, "reason": reason if diffs else ""}
        rows.append(row)
        if not ran:
            problems.append({"id": hid, "severity": "high", "problem": "registered in the plan but not run"})
        elif not est:
            problems.append({"id": hid, "severity": "high", "problem": "run, but the result tables have no estimate for it"})
        elif diffs and not reason:
            problems.append({"id": hid, "severity": "medium",
                             "problem": f"differs from the plan ({', '.join(diffs)}) with no stated reason"})
        elif diffs and t.get("tag") not in ("deviation", "robustness"):
            problems.append({"id": hid, "severity": "medium", "problem": f"differs from the plan ({', '.join(diffs)}) but is tagged {t.get('tag')}"})
    reg_ids = {h["id"] for h in registered}
    for hid, t in tag_by.items():
        if t.get("tag") in ("registered", "unregistered") and hid not in reg_ids and t.get("kind") == "hypothesis" and pap.get("registered"):
            if (pap.get("registration") or {}).get("status") not in ("none",):
                problems.append({"id": hid, "severity": "medium", "problem": "tagged registered but not in the registered plan"})
    n = len(rows)
    summary = {"registered": n, "as_planned": sum(r["status"] == "as planned" for r in rows),
               "deviations": sum(r["status"] == "deviation" for r in rows),
               "missing": sum(r["status"] in ("not run", "no estimate") for r in rows), "problems": len(problems)}
    return {"rows": rows, "problems": problems, "summary": summary}


def sentence(pm: dict) -> str:
    s = pm.get("summary") or {}
    if not s.get("registered"):
        return "no registered analyses to match"
    parts = [f"{s['as_planned']} of {s['registered']} registered analyses run as planned"]
    if s.get("deviations"):
        parts.append(f"{s['deviations']} deviation{'s' if s['deviations'] != 1 else ''} with {'its' if s['deviations'] == 1 else 'their'} reason stated"
                     if not any(p["problem"].startswith("differs") for p in pm.get("problems") or []) else
                     f"{s['deviations']} deviation{'s' if s['deviations'] != 1 else ''}")
    if s.get("missing"):
        parts.append(f"{s['missing']} not run")
    return ", ".join(parts)
