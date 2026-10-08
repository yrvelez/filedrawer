"""Review orchestrator: the strong model reads every reviewer's issues plus the report and the result tables, then

  - pressure-tests the report's claims (abstract, key findings, results prose) against the tables,
  - assigns each reviewer issue a disposition: address (a robustness re-estimate can answer it), editorial (prose,
    labels, missing detail), declined (would change the registered analysis, e.g. a correction the plan does not
    specify), or unresolved (the data cannot settle it),
  - writes a short correction list for the writing agent and lists the unresolved questions, which feed the extensions agent.

Overstated or unsupported claims become issues of their own (source "claims", ids K1, K2, ...).
"""
from __future__ import annotations

import json

from ..agents.base import make_agent, extract_json, results_digest

SYSTEM = """You are the checking agent in an automated review of a short research report on a survey experiment. You receive the report, its result tables, the registration status of its analysis plan, and the issues raised by one or more automated reviewers. You do four things and return ONLY JSON.

1. Pressure-test claims. Find every quantitative or causal claim in the Abstract, Key findings and Results prose (at most 12, the most consequential first). For each, check it against the tables: the number, its sign, its interval, the outcome it is about, and whether the wording (e.g. "improved", "the only arm", "no effect") is justified. Verdicts: "supported", "overstated" (true number, too-strong wording or wrong scope), "unsupported" (number or direction not in the tables).
2. Give every reviewer issue a disposition:
   - "address": a different or additional estimate on the same data would answer it (covariates, estimator, weights, sample, a robustness re-fit);
   - "editorial": only prose, labels, tables or missing detail need fixing;
   - "declined": answering it would change the pre-registered analysis (for example, a multiple-testing correction the plan does not specify, a different primary outcome). Say which part of the plan it conflicts with. Registered analyses are never changed to satisfy a reviewer;
   - "unresolved": the data cannot settle it (needs a new sample, a different design, a measure that was not collected), or reviewers disagree in a way the tables cannot resolve.
3. Corrections: at most 6 short, concrete instructions to the writing agent that will revise the report, most important first. Address the writing agent, never "the authors": no person revises this report.
4. Unresolved questions: each phrased as a research question a follow-up study could answer, with why this study cannot, and the issue ids it came from.

Return:
{"assessment": "3-4 sentences: what the evidence supports, what it does not, and the single most important caveat",
 "claims": [{"claim": "short quote", "location": "Abstract|Key findings|H1|...", "verdict": "supported|overstated|unsupported", "evidence": "table and row, with the number", "fix": "rewording if not supported, else empty"}],
 "dispositions": [{"id": "R1", "disposition": "address|editorial|declined|unresolved", "reason": "one sentence"}],
 "editorial": ["..."],
 "unresolved": [{"question": "...", "why": "...", "from": ["R2"]}]}"""


SYSTEM_SIGNOFF = """You are the checking agent re-checking a CORRECTED research report on a survey experiment. You receive the revised report, its result tables, and the claims you judged in the previous round (ids C1, C2, ...). Do one thing and return ONLY JSON.

Pressure-test the claims of the revised text. Re-check every previous claim: find its current wording (it may have been reworded or removed) and judge it again against the tables. Then check any new quantitative or causal claim in the Abstract, Key findings and Results prose (at most 12 claims in all, the most consequential first). Verdicts: "supported", "overstated" (true number, too-strong wording or wrong scope), "unsupported" (number or direction not in the tables). A claim that was removed from the text is "supported" with the evidence "removed in revision".

Return:
{"claims": [{"claim": "short quote of the CURRENT wording", "location": "Abstract|Key findings|H1|...", "verdict": "supported|overstated|unsupported", "evidence": "table and row, with the number", "fix": "rewording if not supported, else empty", "previous": "C1 or empty for a new claim", "resolved": true|false}],
 "note": "one sentence: what, if anything, still needs rewording"}"""


LIGHT_SCOPE = (
    ("3. Corrections: at most 6 short, concrete instructions to the writing agent that will revise the report, most important first.",
     "3. Corrections: at most 6, and ONLY to fix a reported number, sign, interval, significance statement or plan status (registered, deviation, exploratory). This is a Light Pass: no corrections about style, framing, interpretation, design or methods."),
    ("4. Unresolved questions: each phrased as a research question a follow-up study could answer, with why this study cannot, and the issue ids it came from.",
     "4. Unresolved questions: return an empty list (a Light Pass only verifies estimates and plan statuses)."),
)


def _system(scope: str | None) -> str:
    s = SYSTEM
    if scope == "light":
        for a, b in LIGHT_SCOPE:
            s = s.replace(a, b)
    return s


def _report_text(study) -> str | None:
    report_path = study / "report.md"
    if not report_path.exists():
        return None
    report = report_path.read_text(encoding="utf-8")
    for marker in ("\n## Technical appendix", "\n## Review\n", "\n## Peer review", "\n## Reviewer responses", "\n## Research potential", "\n## Proposed extensions"):
        cut = report.find(marker)
        if cut > 0:
            report = report[:cut]
    return report


def _design_line(ctx: dict) -> tuple[str, dict]:
    from .. import pap as P
    from .common import design_note
    w = P.words(ctx["pap"])
    return (f"## Design\nA {w['phrase']}; causal design: {'yes' if w['causal'] else 'no'}; respondents: {w['sample_kind']}."
            + design_note(ctx) + ("" if w["causal"] else " Causal wording about the exposure is an \"overstated\" claim.")), w


def run(ctx: dict, review: dict, claims_only: bool = False, previous_claims: list[dict] | None = None, scope: str | None = None) -> dict | None:
    """The full checking pass (claims, dispositions, corrections, unresolved questions), or with claims_only=True the
    re-check on the corrected report: the previous round's claims are re-judged against the current text."""
    study = ctx["study_dir"]
    report = _report_text(study)
    if report is None:
        return None
    design_line, w = _design_line(ctx)
    if claims_only:
        prev = [{"id": f"C{n}", **{k: c.get(k) for k in ("claim", "location", "verdict", "evidence", "fix")}}
                for n, c in enumerate(previous_claims or [], 1)]
        task = (f"{design_line}\n\n## Revised report\n{report[:50000]}\n\n## Result tables\n{results_digest(study, max_rows=14)[:14000]}\n\n"
                f"## Claims judged in the previous round\n{json.dumps(prev, ensure_ascii=False)[:8000]}")
        agent = make_agent("review_signoff", ctx, SYSTEM_SIGNOFF.replace("report on a survey experiment", f"report on a {w['phrase']}"), None)
        agent.max_turns = 1
        res = agent.run(task)
        out = extract_json(res.content or "")
        if not isinstance(out, dict):
            return None
        claims = [c for c in (out.get("claims") or []) if isinstance(c, dict) and c.get("claim")][:12]
        return {"model": agent.model, "claims": claims, "note": str(out.get("note") or "").strip()}
    reg = (ctx["pap"].get("registration") or {})
    issues = [{k: i.get(k) for k in ("id", "source", "severity", "kind", "location", "issue", "fix")} for i in review.get("issues", [])]
    task = (f"## Registration\n{json.dumps(reg, ensure_ascii=False)}\nMultiple-testing correction in the plan: "
            f"{ctx['pap'].get('implemented', {}).get('multiple_testing', 'not stated')}\n\n{design_line}\n\n## Report\n{report[:50000]}\n\n"
            f"## Result tables\n{results_digest(study, max_rows=14)[:14000]}\n\n## Reviewer issues\n{json.dumps(issues, ensure_ascii=False)[:14000]}")
    agent = make_agent("review_orchestrator", ctx, _system(scope).replace("report on a survey experiment", f"report on a {w['phrase']}"), None)
    agent.max_turns = 1
    res = agent.run(task)
    out = extract_json(res.content or "")
    if not isinstance(out, dict):
        return None
    known = {i["id"] for i in issues}
    disp = {d.get("id"): d for d in (out.get("dispositions") or []) if isinstance(d, dict) and d.get("id") in known}
    claims = [c for c in (out.get("claims") or []) if isinstance(c, dict) and c.get("claim")][:12]
    unresolved = [u for u in (out.get("unresolved") or []) if isinstance(u, dict) and u.get("question")][:6]
    for n, u in enumerate(unresolved, 1):
        u["id"] = f"U{n}"
        u["from"] = [x for x in (u.get("from") or []) if x in known]
    return {"model": agent.model, "assessment": str(out.get("assessment") or "").strip(), "claims": claims,
            "dispositions": disp, "editorial": [str(e) for e in (out.get("editorial") or [])][:6], "unresolved": unresolved}


def claim_issues(synthesis: dict) -> list[dict]:
    """Overstated or unsupported claims as review issues (K1, K2, ...), so they show up beside the reviewers' issues."""
    out = []
    for c in synthesis.get("claims") or []:
        if c.get("verdict") in ("overstated", "unsupported"):
            out.append({"id": f"K{len(out) + 1}", "source": "claims", "severity": "high" if c["verdict"] == "unsupported" else "medium",
                        "kind": "presentational", "change": None, "location": c.get("location", ""),
                        "issue": f"{c['verdict'].capitalize()} claim: \"{c['claim']}\". {c.get('evidence', '')}".strip(),
                        "fix": c.get("fix") or "Reword to match the table."})
    return out
