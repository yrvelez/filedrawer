"""Extensions agent: three follow-up survey experiments (mechanism, boundary, alternative) in autoexperiment's schema."""
from __future__ import annotations

import json

from .. import extensions as X
from .base import make_agent, results_digest
from ..tools import ToolRegistry

DESIGN_RULES = """Produce a complete, concise standalone English survey experiment using the exact schema. Every collecting question needs a unique readable tag. IDs start with letters and contain letters/numbers/underscores. MC choices have stable local IDs, text, numeric recode strings. Matrix rows are statements and choices are scale points. Use empty arrays/strings for irrelevant fields. DB is participant-facing descriptive text. Do not include executable HTML or code. Flow is a flat list with unique IDs, and "roots" lists the top-level node ids in order; child references form a tree. block nodes reference blocks; group nodes contain sequences; randomizer nodes select exactly one equally allocated child arm. Group each arm with an embedded field recording the condition plus its treatment block: randomizer r1 has children [arm_a, arm_b]; arm_a is a group whose children are [set_a, treat_a]; set_a is an embedded node with field "condition" and value "a"; treat_a is a block node. Each condition's nodeId is the group (arm_a), never the embedded node, the block, or the randomizer. block, embedded and end nodes have empty children. A branch after consent is a sibling of the consent block, not its child. Every FlowNode has all fields: id, type, label, children, blockId, field, value, questionId, choiceId, operator ("equals" when unused); unused string fields are "". Every Question has all fields: id, type, text, tag, multiple, required, choices, rows, sourceQuestionId. Ask consent before treatment and route non-consent to an end node through a branch on the consent MC question (operator "selected"). The full instrument includes consent, every treatment, the outcome measures and a debrief. Stimuli that are media live in an embedded node piped into a DB question with {{ed:field}}: [IMAGE: what the image shows] for a still image, [VIDEO: what the clip shows and its length] for a clip the research team hosts. Use one embedded node per stimulus (field names like post1, post2); the DB question text pipes it, e.g. "{{ed:post1}}", and the outcome question for that post follows it. Every {{ed:field}} used in question text must be set by an embedded node earlier in the flow. NEVER put [RESEARCHER INPUT REQUIRED], TBD, TODO or placeholders in question text: the validator rejects the whole design. Anything only the research team can supply (the actual media files, IRB numbers, compensation) goes in design.unresolved as a plain sentence, and the participant wording stays complete. Record practical judgment calls (sample size with rationale, platform, compensation) in design.assumptions."""

SYSTEM = f"""You design ONE follow-up survey experiment that extends a finished study. You see the study's abstract, key results and limitations, its design, and the questions of its original survey. You never see respondent data.

Hand the design over with record_result(key="proposal", value=<object>, final=true). Do not write prose outside the object.

The object is an ExtensionProposal:
{{"id": the kind given in the task, "kind": "mechanism"|"boundary"|"alternative", "addresses": [ids of unresolved reviewer questions this design answers, e.g. "U1"; may be empty], "title": str, "rationale": 2-3 sentences tying the extension to a specific result or limitation of the source study (cite numbers from the results), "contribution": one sentence, "limitations": [str],
 "design": {{"title", "overview", "hypothesis", "primaryOutcome", "comparison", "population", "conditions": [{{"id","title","description","nodeId"}}], "blocks": [{{"id","title","questions": [Question]}}], "flow": [FlowNode], "roots": [ids], "assumptions": [str], "unresolved": [str], "evidence": [{{"id","title","url","claim","excerpt","origin": "supplied"}}]}}}}

Kinds: "mechanism" tests WHY the source effect (or null) happened; "boundary" tests WHERE it holds (a population, stimulus or context the source did not cover); "alternative" tests a rival explanation or a different intervention for the same outcome.

Design rules (they are checked by a validator; violations are sent back to you):
{DESIGN_RULES}

Grounding rules:
- Reuse the source survey's outcome questions verbatim where they fit, with sourceQuestionId set to their QSF id (e.g. "QID12"); use "" for new questions. Only ids listed under "Source survey questions" are valid.
- evidence: cite only the allowed sources listed in the task, by their exact url; origin "supplied"; excerpt may be "".
- Two to four arms. Keep it short enough for a 10-minute survey.
"""


SYSTEM_OBS = """You write the plan for ONE non-survey follow-up to a finished study: an observational design, a natural experiment, or a data collection, as specified in the brief you are given. You see the study's abstract, key findings and limitations and the brief. You never see respondent data. Hand the plan over with record_result(key="proposal", value=<object>, final=true); no prose outside the object.

{"id": the brief id given in the task, "kind": "observational", "title": str, "rationale": "2-3 sentences tying the follow-up to a specific result or limitation of the source study", "contribution": "one sentence", "limitations": [str],
 "design": {"data_sources": ["named datasets, registries, platforms or collections, with what each provides"], "units": "the unit of analysis and the sample", "exposure": "the variable whose variation does the work, and where that variation comes from", "outcome": "the outcome and how it is measured", "identification": "the identification strategy in plain words (what is compared with what, and what would have to hold)", "threats": ["the main threats to that strategy"], "analysis_plan": "the estimator, the adjustment set, the standard errors, and the one or two pre-specified tests", "hypothesis": "one sentence"}}

Be specific enough that a research assistant could start tomorrow. Name real, obtainable data sources; if one must be collected, say how. No placeholders."""


def _brief_block(ctx: dict, brief: dict) -> str:
    memo = ctx.get("potential") or {}
    debate = next((d for d in memo.get("debates") or [] if d.get("id") == brief.get("debate")), None)
    pt = brief.get("power_target") or {}
    lines = [f"## Brief (design exactly this; use id {brief['id']!r})",
             f"Type: {brief['id']} ({brief.get('mode')}). Title: {brief.get('title')}",
             f"Question: {brief.get('question')}",
             f"Why this follow-up fixes what held the source study back: {brief.get('why_this_fixes_the_failure')}",
             f"Design sketch: {brief.get('design_sketch')}"]
    if brief.get("moderator_or_condition"):
        lines.append(f"Moderator or condition whose variation the design must cover: {brief['moderator_or_condition']}")
    if pt:
        lines.append(f"Power target: about {pt.get('n_per_arm')} per arm to detect {pt.get('mde')} at 80% power ({pt.get('basis', '')}); "
                     f"record it in design.assumptions")
    if debate:
        lines.append(f"Debate the design must discriminate between ({debate['id']}: {debate.get('label')}): (a) {debate.get('position_a')} "
                     f"versus (b) {debate.get('position_b')}. The source study says: {debate.get('what_this_study_says')}")
    if brief.get("addresses"):
        lines.append(f"Reviewer questions it answers (set \"addresses\" to these): {', '.join(brief['addresses'])}")
    return "\n".join(lines)


def _task(ctx: dict, kind: str, others: list[dict], brief: dict | None = None) -> str:
    study, meta, pap = ctx["study_dir"], ctx["meta"], ctx["pap"]
    sec = ctx.get("sections") or {}
    d = pap.get("design", {})
    arms = (d.get("arms") or {})
    outcomes = [{"name": o.get("name"), "label": o.get("label"), "construction": o.get("construction")} for o in pap["implemented"]["outcomes"]]
    qs = X.source_questions(study)
    src = X.allowed_sources(ctx)
    prior = "\n".join(f"- {p['kind']}: {p['title']} — {p['design'].get('hypothesis', '')}" for p in others) or "(none yet)"
    rv_path = study / "review.json"
    syn = (json.loads(rv_path.read_text(encoding="utf-8")).get("synthesis") or {}) if rv_path.exists() else {}
    open_q = "\n".join(f"- {u['id']}: {u['question']} (why this study cannot settle it: {u.get('why', '')})" for u in syn.get("unresolved") or []) or "(none)"
    guidance = "\n".join(f"- {e}" for e in syn.get("editorial") or []) or "(none)"
    head = (f"Write the {kind!r} extension. Use id {kind!r}." if brief is None
            else f"Write the {kind!r}-type extension specified in the brief below.\n\n{_brief_block(ctx, brief)}")
    return (f"{head}\n\n## Source study\nTitle: {meta['title']}\n\nAbstract: {sec.get('abstract') or sec.get('summary', '')}\n\n"
            f"Key findings:\n" + "\n".join(f"- {t}" for t in (sec.get("takeaways") or [])) +
            f"\n\nLimitations: {sec.get('limitations', '')}\n\n## Design\nArms ({arms.get('column')}): {json.dumps(arms.get('labels') or {}, ensure_ascii=False)}\n"
            f"Arm descriptions: {json.dumps(arms.get('descriptions') or {}, ensure_ascii=False)[:3000]}\nOutcomes: {json.dumps(outcomes, ensure_ascii=False)}\n"
            f"Population: {json.dumps(d.get('population') or {}, ensure_ascii=False)}\n\n## Results (registered summary)\n{results_digest(study, max_rows=14)[:5000]}\n\n"
            f"## Source survey questions (QSF ids usable as sourceQuestionId)\n{json.dumps(qs, ensure_ascii=False)[:9000]}\n\n"
            f"## Allowed evidence sources\n{json.dumps(src, ensure_ascii=False)}\n\n"
            f"## Questions the reviewers raised that this study cannot settle (prefer a design that answers one; list its id in \"addresses\")\n{open_q}\n\n"
            f"## The checking agent's corrections for this study (avoid repeating its weaknesses)\n{guidance}\n\n"
            f"## Extensions already written (make yours distinct)\n{prior}")


def revise(ctx: dict, current: dict, request: str) -> tuple[dict | None, str, list[str]]:
    """Revise one existing proposal in response to the author's request; same checks and one repair turn."""
    study = ctx["study_dir"]
    qsf_ids = {q["id"] for q in X.source_questions(study) if q.get("id")}
    urls = {s["url"] for s in X.allowed_sources(ctx)}
    kind = current["kind"]
    task = (_task(ctx, kind, []) + f"\n\n## Current design (revise this; preserve ids and wording that the request does not touch)\n"
            + json.dumps(current, ensure_ascii=False)[:24000] + f"\n\n## The author's request\n{request}")
    prop, errs, validator = None, ["no proposal returned"], "structural"
    for attempt in range(2):
        tools = ToolRegistry(study, allowed=("record_result",))
        agent = make_agent("extensions", ctx, SYSTEM, tools)
        agent.max_turns = 1
        res = agent.run(task if attempt == 0 else task + "\n\n## Your revision failed validation; return the full corrected object:\n- "
                        + "\n- ".join(errs[:25]) + "\n\nPrevious attempt:\n" + json.dumps(prop)[:20000])
        cand = res.records.get("proposal")
        if not isinstance(cand, dict):
            errs = ["no proposal returned (call record_result with key 'proposal')"]
            continue
        cand["id"], cand["kind"] = kind, kind
        prop = cand
        errs, validator = X.validate(cand, qsf_ids, urls, ctx["cfg"], tmp_dir=study)
        if not errs:
            break
    return (prop if not errs else None), validator, errs


BRIEF_ORDER = ("advance_design", "generalizability_conditional", "theoretical_debate", "data_to_collect", "natural_experiment")


def _plan(ctx: dict, brief: dict) -> tuple[dict | None, list[str]]:
    """A non-survey follow-up: one fast-tier call producing a plan (no QSF), checked structurally."""
    study = ctx["study_dir"]
    sec = ctx.get("sections") or {}
    task = (f"Write the plan for the follow-up in this brief.\n\n{_brief_block(ctx, brief)}\n\n## Source study\nTitle: {ctx['meta']['title']}\n\n"
            f"Abstract: {sec.get('abstract') or sec.get('summary', '')}\n\nKey findings:\n" + "\n".join(f"- {t}" for t in (sec.get("takeaways") or []))
            + f"\n\nLimitations: {sec.get('limitations', '')}")
    prop, errs = None, ["no proposal returned"]
    for attempt in range(2):
        tools = ToolRegistry(study, allowed=("record_result",))
        agent = make_agent("extensions_obs", ctx, SYSTEM_OBS, tools)
        agent.max_turns = 1
        res = agent.run(task if attempt == 0 else task + "\n\n## Your previous plan failed validation; return the full corrected object:\n- "
                        + "\n- ".join(errs[:10]) + "\n\nPrevious plan:\n" + json.dumps(prop)[:12000])
        cand = res.records.get("proposal")
        if not isinstance(cand, dict):
            errs = ["no proposal returned (call record_result with key 'proposal')"]
            continue
        cand["id"], cand["kind"] = brief["id"], "observational"
        prop = cand
        errs = X.plan_errors(cand)
        if not errs:
            return cand, []
    return None, errs


def _briefs(ctx: dict) -> list[dict]:
    memo = ctx.get("potential") or {}
    briefs = [b for b in memo.get("briefs") or [] if b.get("id")]
    return sorted(briefs, key=lambda b: BRIEF_ORDER.index(b["id"]) if b["id"] in BRIEF_ORDER else 99)


def run(ctx: dict, kinds=X.KINDS, briefs: list[dict] | None = None) -> tuple[list[dict], str, list[str]]:
    """Returns (proposals, validator, problems). With a research-potential memo in ctx (or `briefs`), each brief is
    designed: survey briefs through the design agent (one draft and one repair turn), others as plans. Without a memo,
    the legacy kinds (mechanism, boundary, alternative) are designed."""
    study = ctx["study_dir"]
    qsf_ids = {q["id"] for q in X.source_questions(study) if q.get("id")}
    urls = {s["url"] for s in X.allowed_sources(ctx)}
    proposals, problems, validator = [], [], "structural"
    briefs = briefs if briefs is not None else _briefs(ctx)
    jobs = [(b["kind_legacy"] or "alternative", b) for b in briefs] if briefs else [(k, None) for k in kinds]
    for kind, brief in jobs:
        if brief is not None and not brief.get("needs_qsf"):
            plan, errs = _plan(ctx, brief)
            if plan is not None:
                plan.update(brief_id=brief["id"], mode=brief.get("mode"), debate=brief.get("debate"), why=brief.get("why_this_fixes_the_failure", ""),
                            addresses=[str(x) for x in (brief.get("addresses") or [])])
                proposals.append(plan)
            else:
                problems.append(f"{brief['id']}: " + "; ".join(errs[:6]))
            continue
        pid = brief["id"] if brief is not None else kind
        task = _task(ctx, kind, proposals, brief)
        prop, errs = None, ["no proposal returned"]
        for attempt in range(2):
            tools = ToolRegistry(study, allowed=("record_result",))
            agent = make_agent("extensions", ctx, SYSTEM, tools)
            agent.max_turns = 1
            res = agent.run(task if attempt == 0 else task + "\n\n## Your previous design failed validation; return the full corrected object:\n- "
                            + "\n- ".join(errs[:25]) + "\n\nPrevious design:\n" + json.dumps(prop)[:20000])
            cand = res.records.get("proposal")
            if not isinstance(cand, dict):
                errs = ["no proposal returned (call record_result with key 'proposal')"]
                continue
            cand["id"], cand["kind"] = pid, kind
            # keys that are not part of the autoexperiment schema (which forbids extra keys) are kept out of validation
            # and carried to the index row by save(): the reviewer questions answered, and the brief's identity
            extras = {k: cand.pop(k) for k in X.EXTRA_KEYS if k in cand}
            addresses = [str(x) for x in (extras.get("addresses") or []) if isinstance(x, str)]
            prop = cand
            errs, validator = X.validate(cand, qsf_ids, urls, ctx["cfg"], tmp_dir=study)
            if not errs:
                cand["addresses"] = addresses or [str(x) for x in ((brief or {}).get("addresses") or [])]
                if brief is not None:
                    cand.update(brief_id=brief["id"], mode=brief.get("mode"), debate=brief.get("debate"),
                                why=brief.get("why_this_fixes_the_failure", ""), power_target=brief.get("power_target"))
                break
        if prop is not None and not errs:
            proposals.append(prop)
        else:
            problems.append(f"{pid}: " + "; ".join(errs[:6]))
    return proposals, validator, problems
