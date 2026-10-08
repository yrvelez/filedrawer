"""The Light Pass referee: checks reported estimates against the result tables and plan statuses against the plan-match table.
Nothing else (design, methods and interpretation belong to the other review options)."""
from __future__ import annotations

import json

from .base import make_agent, extract_json, results_digest

SYSTEM = """You are the Light Pass referee for an AI-generated research report. Your job is narrow and has exactly two parts:
(1) ESTIMATES. Every number the prose reports (estimate, sign, confidence interval, p-value, N, percentage, the outcome it is about, which arm or subgroup) must match the result tables. Flag any mismatch, any number that appears in no table, and any significance or direction statement the tables contradict ("raised" when the estimate is negative, "significant" when p > 0.05, a CI that excludes zero when it does not).
(2) PLAN MATCH. Every analysis must be described with its true plan status, as given in the plan-match table and the analysis tags: registered analyses as registered, deviations named as deviations with the reason the table states, exploratory analyses never presented as registered, confirmatory or pre-specified.
Do NOT comment on anything else: not the design, methods, estimator choice, power, multiple testing, missing analyses, interpretation, framing, writing style or completeness. Those belong to other review options.
Return ONLY JSON:
{"overall": one sentence on whether the estimates and plan statuses check out,
 "issues": [{"severity": "high"|"medium"|"low", "location": section or hypothesis id, "check": "estimate"|"plan",
             "issue": what the text says versus what the table or plan says, with both numbers, "fix": the corrected wording}]}
"high": a wrong number, sign or significance claim, or an exploratory result presented as registered; "medium": a mislabelled plan status or a number with no table behind it; "low": rounding or a missing unit.
At most 8 issues, most important first. If everything checks out, return an empty issues list."""


def run(ctx: dict, write: bool = True) -> dict:
    study = ctx["study_dir"]
    report = (study / "report.md").read_text(encoding="utf-8")
    for marker in ("\n## Review\n", "\n## Peer review", "\n## Research potential", "\n## Proposed extensions", "\n## Technical appendix"):
        cut = report.find(marker)                    # the review never reads earlier review rounds or the follow-up memo
        if cut > 0:
            report = report[:cut]
    tags = json.dumps(ctx["tags"], ensure_ascii=False)[:3000]
    from ..review.plan_match import plan_match
    from .. import package as PKG
    pm = plan_match(ctx["pap"], ctx["tags"], PKG.read_summary(study))
    table = "\n".join(f"- {r['id']}: {r['status']}" + (f" ({', '.join(r['differences'])}; reason: {r['reason'] or 'none stated'})" if r["differences"] else "")
                      for r in pm["rows"]) or "(no registered analyses)"
    task = (f"## Report\n{report[:60000]}\n\n## Plan match (registered analyses against what was run)\n{table}\n\n"
            f"## Analysis tags\n{tags}\n\n## Result tables\n{results_digest(study, max_rows=12)[:14000]}")
    agent = make_agent("reviewer", ctx, SYSTEM, None)
    agent.max_turns = 1
    res = agent.run(task)
    out = extract_json(res.content) or {}
    issues = [i for i in (out.get("issues") or []) if isinstance(i, dict)][:8]
    for n, i in enumerate(issues, 1):
        i["id"] = f"R{n}"
        i["kind"], i["change"] = "presentational", None        # the Light Pass corrects text; it never asks for new estimates
        i["check"] = i.get("check") if i.get("check") in ("estimate", "plan") else "estimate"
    rv = {"model": agent.model, "overall": out.get("overall", ""), "issues": issues}
    if write:
        from ..review import write_review
        write_review(study, {"mode": "light", "modes": ["light"], "models": {"light": agent.model}, **rv})
    return rv
