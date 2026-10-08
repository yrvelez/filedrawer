"""Research-potential memo: what the study got right, why it may not have landed, which debates it can speak to,
and three typed briefs for follow-up studies (advance the design, speak to a theoretical debate, test the
conditions under which the effect generalizes), plus optional observational briefs. One strong-model call.

The memo is the brief the extensions agent designs from, and it is rendered in the report as "Research potential".
It sees the report (without its appendix), the retrieved literature with each work's stance, the checking agent's open
questions and corrections, the claim re-check, the design's vocabulary and a deterministic power table."""
from __future__ import annotations

import json
import re

from .base import make_agent, extract_json, results_digest
from .. import pap as P
from ..analysis import power

REQUIRED_BRIEFS = ("advance_design", "theoretical_debate", "generalizability_conditional")
OPTIONAL_BRIEFS = ("data_to_collect", "natural_experiment")
CAUSES = ("power", "measurement", "design", "framing", "missing_moderator", "sample", "analysis")
MODES = ("survey_experiment", "conjoint", "observational", "natural_experiment", "data_collection")
LEGACY = {"advance_design": "mechanism", "theoretical_debate": "alternative", "generalizability_conditional": "boundary",
          "data_to_collect": None, "natural_experiment": None}

SYSTEM = """You are a senior methodologist assessing the research potential of an unpublished study that was filed rather than published. Your reader is a researcher deciding whether, and how, to build on it. The report, its wording, its robustness checks (ids like H1a) and its corrections were produced by an automated pipeline of agents, not by the study's authors: credit "the agent" or "the pipeline" for them, never "the authors". The authors are responsible only for the design, the data and the pre-registered plan. You see the report, the literature retrieved for it (each work tagged supporting, contesting, method or citing), the editor's unresolved questions and guidance, the decision on the revised text, and a power table computed from the data. You never see respondent data. Be concrete and sparing: numbers from the tables, works from the list, no generalities. Return ONLY JSON:

{"got_right": ["at most four things the study did well: a design choice, a measure, a result that stands"],
 "why_it_did_not_land": [{"cause": "power|measurement|design|framing|missing_moderator|sample|analysis", "detail": "one or two sentences", "evidence": "the number, table or passage this rests on"}],
 "debates": [{"id": "D1", "label": "a short name for a live disagreement in the literature this study bears on", "position_a": "...", "position_b": "...", "what_this_study_says": "which side the evidence leans to, or why it cannot tell", "works": [{"id": "doi or id from the works list", "side": "a|b|neutral"}]}],
 "briefs": [
   {"id": "advance_design", "kind_legacy": "mechanism", "mode": "survey_experiment|conjoint|observational|natural_experiment|data_collection", "title": "...", "question": "the research question in one sentence", "why_this_fixes_the_failure": "which cause above it answers and how", "debate": "D1 or null", "addresses": ["U1"], "design_sketch": "arms or exposure, outcome, sample, in 3-5 sentences", "moderator_or_condition": null, "power_target": {"n_per_arm": 0, "mde": 0.0, "basis": "which observed estimate or MDE row this is sized against"}, "needs_qsf": true},
   {"id": "theoretical_debate", "kind_legacy": "alternative", ...the same fields; the design must discriminate between the two positions of a debate above...},
   {"id": "generalizability_conditional", "kind_legacy": "boundary", ...the same fields; "moderator_or_condition" names the population, context or stimulus condition whose variation tests where the effect holds...}
 ],
 "verdict": "one paragraph: is a follow-up worth running, and which brief first"}

Rules. Exactly one brief each of advance_design and generalizability_conditional, and one theoretical_debate brief when the debates list is not empty (with no genuine debate, leave the theoretical_debate brief out entirely: never write a brief that declines or says there is no debate); you may add at most two more with id data_to_collect or natural_experiment (kind_legacy null, needs_qsf false) when the question is better answered without a new survey experiment. advance_design pushes the design forward with something the original lacked (a manipulation of the proposed mechanism, a within-subject or multi-wave element, a behavioural outcome, a stronger or more realistic stimulus). Each brief says which failure cause it fixes; a brief that fixes nothing is not worth proposing. Power targets come from the power table: size the follow-up to detect the observed estimate (or the smallest effect worth caring about) at 80% power. Debates cite only works from the list, by their exact id. If no genuine debate is visible in the works, return an empty debates list and say so in the verdict rather than inventing one. Write for readers: refer to outcomes, moderators and arms by their labels, never by column names or codes, and to other briefs by their titles, never by id. Never mention a brief you are not offering (say nothing about a missing debate brief)."""


def _task(ctx: dict) -> str:
    study, pap = ctx["study_dir"], ctx["pap"]
    report = (study / "report.md").read_text(encoding="utf-8") if (study / "report.md").exists() else ""
    for marker in ("\n## Review\n", "\n## Peer review", "\n## Research potential", "\n## Proposed extensions", "\n## Technical appendix"):
        cut = report.find(marker)
        if cut > 0:
            report = report[:cut]
    lit = ctx.get("lit") or {}
    works = "\n".join(f"- id: {w.get('doi') or w.get('id')} | {w.get('stance', 'prior_findings')} | {w.get('title')} — "
                      f"{', '.join(w.get('authors') or [])} ({w.get('year')}). {str(w.get('abstract') or '')[:240]}"
                      for w in lit.get("works") or []) or "(no works retrieved)"
    rv = ctx.get("review") or {}
    syn = rv.get("synthesis") or {}
    open_q = "\n".join(f"- {u['id']}: {u['question']} (why this study cannot settle it: {u.get('why', '')})" for u in syn.get("unresolved") or []) or "(none)"
    guidance = "\n".join(f"- {e}" for e in syn.get("editorial") or []) or "(none)"
    decision = rv.get("decision") or "(no review)"
    w = P.words(pap)
    table = power.mde_table(study, pap)
    return (f"## Study\nTitle: {ctx['meta']['title']}\nDesign: a {w['phrase']}; causal: {w['causal']}; respondents: {w['sample_kind']}; "
            f"N analysed: {ctx['meta'].get('n_analysis')}\nRegistration: {json.dumps(pap.get('registration') or {'status': 'registered'})}\n"
            f"Claim re-check on the corrected text: {decision}\n\n## Report\n{report[:45000]}\n\n"
            f"## Result tables\n{results_digest(study, max_rows=14)[:10000]}\n\n## Power table (MDE at 80% power, two-sided 0.05)\n{power.mde_markdown(table)}\n\n"
            f"## Retrieved literature (cite debates only from these ids)\n{works}\n\nLiterature note's debate hint: {lit.get('debate_hint') or '(none)'}\n\n"
            f"## Unresolved questions from the review\n{open_q}\n\n## Corrections the checking agent asked for\n{guidance}")


def _drop_sentences(text: str, pattern: str) -> str:
    """The text without the sentences (or ', so ...' clauses) that match; used for notes about briefs not offered."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if re.search(pattern, s, re.I):
            head = re.split(r",\s*(?:so|and so|hence|therefore)\b", s)[0].rstrip(" ,")
            if head and head != s and not re.search(pattern, head, re.I):
                out.append(head + ".")
            continue
        out.append(s)
    return " ".join(out)


def validate(memo: dict, work_ids: set[str]) -> list[str]:
    """Structural check; unknown work ids are dropped (not fatal), unknown enum values and missing required briefs are.
    The theoretical_debate brief is required only when a debate is listed; without one it is dropped."""
    errs = []
    if not isinstance(memo, dict):
        return ["memo is not an object"]
    for k in ("got_right", "why_it_did_not_land", "debates", "briefs", "verdict"):
        if k not in memo:
            errs.append(f"missing {k}")
    if errs:
        return errs
    memo["got_right"] = [str(x) for x in (memo.get("got_right") or [])][:4]
    causes = []
    for c in memo.get("why_it_did_not_land") or []:
        if isinstance(c, dict) and c.get("cause") in CAUSES:
            causes.append({"cause": c["cause"], "detail": str(c.get("detail") or ""), "evidence": str(c.get("evidence") or "")})
    memo["why_it_did_not_land"] = causes
    debates = []
    for n, d in enumerate(memo.get("debates") or [], 1):
        if not isinstance(d, dict) or not d.get("label"):
            continue
        d["id"] = f"D{n}"
        d["works"] = [{"id": str(x.get("id")), "side": x.get("side") if x.get("side") in ("a", "b", "neutral") else "neutral"}
                      for x in (d.get("works") or []) if isinstance(x, dict) and str(x.get("id")) in work_ids]
        debates.append({k: d.get(k, "") for k in ("id", "label", "position_a", "position_b", "what_this_study_says", "works")})
    memo["debates"] = debates
    known_debates = {d["id"] for d in debates}
    briefs, seen = [], set()
    for b in memo.get("briefs") or []:
        if not isinstance(b, dict) or b.get("id") not in REQUIRED_BRIEFS + OPTIONAL_BRIEFS or b["id"] in seen:
            continue
        seen.add(b["id"])
        b["kind_legacy"] = LEGACY[b["id"]]
        b["mode"] = b.get("mode") if b.get("mode") in MODES else ("survey_experiment" if b["id"] in REQUIRED_BRIEFS else "observational")
        b["needs_qsf"] = bool(b.get("needs_qsf", b["id"] in REQUIRED_BRIEFS)) and b["mode"] in ("survey_experiment", "conjoint")
        b["debate"] = b.get("debate") if b.get("debate") in known_debates else None
        b["addresses"] = [str(x) for x in (b.get("addresses") or []) if isinstance(x, str)]
        pt = b.get("power_target")
        b["power_target"] = pt if isinstance(pt, dict) and pt.get("n_per_arm") else None
        for k in ("title", "question", "why_this_fixes_the_failure", "design_sketch"):
            b[k] = str(b.get(k) or "")
        b["moderator_or_condition"] = b.get("moderator_or_condition") or None
        briefs.append(b)
    if not debates:                  # no debate in the literature: a debate brief would only be a placeholder
        briefs = [b for b in briefs if b["id"] != "theoretical_debate"]
        memo["verdict"] = _drop_sentences(str(memo.get("verdict") or ""), r"debate (brief|follow-up)|no debate .*(offered|proposed)")
    missing = [r for r in REQUIRED_BRIEFS if r not in seen and (debates or r != "theoretical_debate")]
    if missing:
        errs.append("missing required brief(s): " + ", ".join(missing))
    memo["briefs"] = briefs
    memo["verdict"] = str(memo.get("verdict") or "")
    return errs


def run(ctx: dict) -> tuple[dict | None, list[str]]:
    """Returns (memo, problems). A memo that fails validation after one repair turn is dropped (problems say why)."""
    lit = ctx.get("lit") or {}
    work_ids = {str(w.get("doi") or w.get("id")) for w in lit.get("works") or []} | {str(w.get("id")) for w in lit.get("works") or []}
    task = _task(ctx)
    memo, errs = None, ["no memo returned"]
    for attempt in range(2):
        agent = make_agent("potential", ctx, SYSTEM, None)
        agent.max_turns = 1
        res = agent.run(task if attempt == 0 else task + "\n\n## Your previous memo failed validation; return the full corrected object:\n- "
                        + "\n- ".join(errs[:10]) + "\n\nPrevious memo:\n" + json.dumps(memo)[:20000])
        cand = extract_json(res.content or "")
        if not isinstance(cand, dict):
            errs = ["no JSON object returned"]
            continue
        memo = cand
        errs = validate(memo, work_ids)
        if not errs:
            memo["model"] = agent.model
            return memo, []
    return None, errs
