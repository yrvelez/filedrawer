"""Shared pieces of the review modes: the report payload, the study context note, and comment -> issue mapping.

Every reviewer that returns free-form comments (advanced pass, coarse, refine) is mapped onto the review.json
issue shape the light pass produces, so `filedrawer address` can answer any of them.
"""
from __future__ import annotations

import json
import re

from ..agents.base import extract_json, results_digest

SEVERITY = {"critical": "high", "major": "high", "high": "high", "minor": "medium", "medium": "medium",
            "suggestion": "low", "low": "low"}
RANK = {"high": 0, "medium": 1, "low": 2}
MIN_CONFIDENCE = 0.3
MULTIPLE_TESTING = re.compile(r"multiple[- ](comparison|testing|hypothes)|bonferroni|holm|false discovery|\bfdr\b|q-value", re.I)

# What every reviewer is told about the study, and the two keys that make a comment actionable by `address`.
STUDY_NOTE = """

CONTEXT FOR THIS REVIEW: the document is an AI-generated report of a {design_phrase}, produced from the authors'
data and pre-analysis plan. Analyses are tagged "registered" (as planned), "deviation" (planned, implemented
differently, with the reason stated) or "exploratory" (not planned). The plan is fixed: never ask for it to be
rewritten. Its multiple-testing policy is: {multiple_testing}. If that policy is "none", it was the authors' choice;
you may note it as a limitation, but do not ask for corrections to be applied. The report was written by an agent and
an agent applies the fixes: refer to "the report", never to "the authors", in every issue and fix.{design_note}

Add two keys to every object:
    kind: "analytical" when answering the issue needs a different or additional estimate (other covariates,
          estimator or standard errors, weights, a different sample or missing-data treatment, a robustness re-fit);
          "presentational" when only the prose, labels or tables need to change
    target: the hypothesis id the issue concerns (one of {hypotheses}), or null"""


OBSERVATIONAL_REVIEW_NOTE = (" The design is observational, not randomized: causal wording about the exposure (\"effect\", "
                             "\"caused\", \"improved\") is a presentational issue; raise it.")
SYNTHETIC_REVIEW_NOTE = (" The respondents are LLM-generated (synthetic): any inference to a human population is a "
                         "presentational issue; raise it.")


def design_note(ctx: dict) -> str:
    """The one or two sentences every reviewer and the editor get about the design's language rules."""
    from .. import pap as P
    w = P.words(ctx.get("pap") or {})
    note = "" if w["causal"] else OBSERVATIONAL_REVIEW_NOTE
    if w["sample_kind"] != "human":
        note += SYNTHETIC_REVIEW_NOTE
    return note


def study_note(ctx: dict) -> str:
    from .. import pap as P
    multiple_testing, hyps = plan_facts(ctx)
    return STUDY_NOTE.format(multiple_testing=multiple_testing, hypotheses=", ".join(hyps) or "none",
                             design_phrase=P.words(ctx.get("pap") or {})["phrase"], design_note=design_note(ctx))


def plan_facts(ctx: dict) -> tuple[str, list[str]]:
    imp = (ctx.get("pap") or {}).get("implemented") or {}
    hyps = [h.get("id") for h in imp.get("hypotheses", []) if h.get("id")]
    return str(imp.get("multiple_testing") or "not stated"), hyps


def split_sections(report: str) -> list[tuple[str, str]]:
    """(heading, body) per `##` section; the technical appendix and anything before the first heading are dropped."""
    for marker in ("\n## Review\n", "\n## Peer review", "\n## Research potential", "\n## Proposed extensions", "\n## Technical appendix"):
        cut = report.find(marker)                    # the review never reads earlier review rounds or the follow-up memo
        if cut > 0:
            report = report[:cut]
    parts = re.split(r"(?m)^## +(.+)$", report)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def report_payload(ctx: dict) -> dict:
    """{"paper_title", "abstract", "section_text"}: the report without its appendix, plus the result tables."""
    study = ctx["study_dir"]
    sections = split_sections((study / "report.md").read_text(encoding="utf-8"))
    sec = ctx.get("sections") or {}
    abstract = str(sec.get("abstract") or sec.get("summary") or "").strip() or \
        next((b for h, b in sections if h.lower().startswith(("abstract", "summary"))), "")
    text = "\n\n".join(f"## {h}\n{b}" for h, b in sections)[:60000]
    text += "\n\n## Result tables (source of every number above)\n" + results_digest(study, max_rows=40, summaries=True)[:40000]
    return {"paper_title": (ctx.get("meta") or {}).get("title", ""), "abstract": abstract[:3000], "section_text": text}


def parse_comments(text: str):
    """Comments come as a JSON array; extract_json would return its first object, so try the array first."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text or "", re.S)
    cand = m.group(1) if m else (text or "")
    start = cand.find("[")
    if start >= 0 and (cand.find("{") < 0 or start < cand.find("{")):
        try:
            return json.JSONDecoder().raw_decode(cand[start:])[0]
        except json.JSONDecodeError:
            pass
    out = extract_json(text or "")
    if isinstance(out, dict):
        out = out.get("comments") or out.get("issues") or []
    return out if isinstance(out, list) else None


def to_issue(c: dict, source: str, hyps: list[str]) -> dict | None:
    """One comment {section_ref, issue, detail, severity, suggestion, confidence, kind, target} -> one review.json
    issue (id assigned by the caller), or None if unusable or below the confidence floor."""
    if not isinstance(c, dict) or not (c.get("issue") or c.get("detail")):
        return None
    try:
        conf = float(c.get("confidence", 1.0))
    except (TypeError, ValueError):
        conf = 1.0
    if conf < MIN_CONFIDENCE:
        return None
    title, detail = str(c.get("issue") or "").strip(), str(c.get("detail") or "").strip()
    text = f"{title}: {detail}" if title and detail else (title or detail)
    target = c.get("target") if c.get("target") in hyps else None
    if target is None:
        loc = str(c.get("section_ref") or "")
        target = next((h for h in hyps if re.search(rf"\b{re.escape(h)}\b", loc)), None)
    kind = c.get("kind") if c.get("kind") in ("analytical", "presentational") else "analytical"
    if MULTIPLE_TESTING.search(text):      # the plan's multiple-testing policy is the authors' call; never re-estimated
        kind = "presentational"
    return {"severity": SEVERITY.get(str(c.get("severity", "")).lower(), "medium"),
            "location": str(c.get("section_ref") or target or ""), "issue": text, "fix": str(c.get("suggestion") or ""),
            "kind": kind, "change": {"target": target, "type": "robustness"} if kind == "analytical" and target else None,
            "source": source, "confidence": round(conf, 2)}


def rank(issues: list[dict], cap: int) -> list[dict]:
    return sorted(issues, key=lambda i: (RANK[i["severity"]], -i.get("confidence", 1.0)))[:cap]
