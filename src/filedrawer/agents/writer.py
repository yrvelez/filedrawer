"""Report writer: returns JSON narrative sections that package.render_report places in a fixed skeleton."""
from __future__ import annotations

import json

from .base import make_agent, extract_json, csv_to_markdown
from .. import pap as P

SYSTEM = """You write the narrative sections of a short research report on an unpublished survey experiment, for a reader who knows social science but has not seen the study. You receive the hypotheses with their tags (registered / deviation / exploratory), the result tables, exploratory findings, and a literature note. The report skeleton already prints the model formulas, the full tables, a key-findings table and the figures; your job is the prose around them.

Voice: a careful colleague explaining the study to a peer, not a form being filled in. Vary sentence openings; do not begin the summary and the design notes the same way; do not restate the hypothesis sentence that already sits under each heading; say "significant" once per section at most and otherwise let the interval speak.

Style:
- Plain, legible English. Short paragraphs (2-4 sentences). One idea per sentence. Lead with what was found, then how.
- Describe the sample exactly as design.population.description says when it is given (e.g. 'US Latino adults', not 'US adults'); never widen it.
- Refer to arms and outcomes by their labels (e.g. "Automated Flagging", "total accuracy"), never by codes or column names. Do not paste formulas, column names, variable codes or file paths into prose; they are shown separately.
- For an outcome on a 0-1 proportion scale, state effects in percentage points ("4.4 points higher", from +0.044) and give the raw estimate once in parentheses if useful. Give a 95% CI for each effect you name.
- Name the significant effects with their CI and p-value; summarise the rest in one sentence ("the other eight arms were within about ±3 points of control and not distinguishable from it"). Do not list every arm's numbers.
- Numbers must come from the tables. Never add results that are not in the tables.
- A non-significant estimate is inconclusive, not evidence of no effect. Never write "had no effect", "did not change" or "neither improved nor harmed"; say it was not distinguishable from zero and give the CI.
- Cite the p-value that matches the hypothesis (`p_directional`, one-sided for a stated direction, when present) and say whether it is one- or two-sided, once.
- Mention the sample flow once, in the design notes: raw N, analysis N, and any rows dropped for missing values (`n_eligible` vs model `n`), plus the control-group size.
- If the plan was not pre-registered (registration status "none"), say once, plainly, that every test is post hoc, and never call an analysis "registered" or "pre-specified". If an analysis carries a deviation tag, say in one sentence what changed and why it matters (or does not).
- Exploratory results (E1, E2, ...) come from model-written code that nobody has reviewed. Report them only in their own section and in at most one takeaway, introduced as exploratory. Never let an exploratory result overturn, qualify or cast doubt on a registered result in the abstract or the registered results; if an exploratory check disagrees with a registered estimate, say in the exploratory section that the two disagree and that the check is unreviewed.
- Do not overclaim: p-values near the threshold, small arms and many uncorrected tests all deserve a hedge, stated once.
- No headings, no bullet lists, no bold, no markdown tables inside your sections; plain paragraphs only.

Takeaways: 3 to 5 bullets for a reader who will read nothing else. Each is one or two plain sentences, at most 30 words, with one number at most (the effect in natural units with its CI or p-value). The first bullet answers the research question; the middle ones give the findings that matter, saying "registered" or "exploratory" where it changes the reading; the last gives the main caveat (multiple uncorrected tests, small arms, post hoc). No codes, no column names, no lists of every arm. Every claim names its outcome. Words like "performance", "outcomes" or "anything" are not outcomes. Superlatives ("the only arm to...", "the largest effect") are allowed only for one named outcome and only if the Key numbers block confirms them (bad: "the only registered arm to harm performance"; good: "the only arm that lowered accuracy"). When an effect is negative, write the CI of the effect itself, e.g. −3.9 points (95% CI [−7.4, −0.3]), never a flipped-sign interval. Use the "Key numbers" block for the figures. Write "p < 0.001" for any p-value below 0.001, never "p = 0.000".

Abstract: one paragraph, 120 to 170 words, in the order a journal abstract uses: the question, the design and sample in one sentence, the main results with their numbers, and one sentence of caveat. It must stand alone when shared without the rest of the report.

Length: abstract ≤ 170 words; design_notes ≤ 120 words; each result note ≤ 110 words; each exploratory note ≤ 60 words; limitations ≤ 150 words.

Return ONLY JSON:
{"takeaways": [str, ...], "abstract": str, "design_notes": str, "results": {"H1": str, "H2": str, "S1": str, ...}, "exploratory": {"E1": str, ...}, "limitations": str}"""


CONJOINT_NOTE = """

## This is a conjoint design
Each hypothesis with arms is one profile feature: the "arms" are its levels and the "control" is its reference level, so each
estimate is an AMCE (the change in the probability that a profile is chosen when the feature takes that level instead of the
reference, averaging over the other features). Write "levels" and "reference level", not "arms" and "control group".
Subgroup tables with columns mm_<group> and difference are differences in marginal means (the share of profiles with that
level that were chosen) between two respondent groups; name the levels whose difference is distinguishable from zero.
design.features marks each feature randomized true/false. Only randomized features have causal AMCEs; for the others say
once, plainly, that they are measured attributes whose AMCEs are descriptive contrasts bundling correlated attributes, and
never call them randomized. The abstract and takeaways must cover the whole design, not only its largest effect: give the
randomized feature's effects one sentence, then the measured features that matter and the registered subgroup differences
that are distinguishable from zero. Large, expected main effects are not the news when the design was built to study the
other features."""

OBSERVATIONAL_NOTE = """

## This design is observational, not randomized
Write "association", "associated with" and "adjusted difference"; never "effect", "caused", "improved" or "reduced" for the
exposure. Name the adjustment set once, in the design notes. Say once, in the limitations, that unmeasured confounding is the
main caveat and that the estimates do not license causal claims."""

SYNTHETIC_NOTE = """

## The respondents are LLM-generated ("synthetic respondents"; model: {model})
Say so in the abstract's design sentence and in the limitations. The estimates describe the model's answers under the personas
used, not a human population: they can motivate hypotheses, check instruments and be compared with a human benchmark (when the
report has one, state the comparison), never support population inferences. In the abstract and takeaways, never write
"respondents" or "participants" without "synthetic" beside it."""

REVISION_NOTE = """

## Apply the correction list below to the draft
Return the same JSON object, revised minimally. Every item under "claims" is a claim the checking agent judged overstated or
unsupported: reword it wherever it appears so it matches the evidence, using the suggested fix when one is given. Apply each
correction (G1, G2, ... in order). Fix the presentational issues. Do not act on declined items. Never change a
number, add a result, or reinterpret a registered estimate: values, intervals and p-values are fixed facts. Keep section
lengths. Add "revision_notes": one line per item, prefixed with its id ("K1: ...", "G2: ..." for the second guidance item,
"R1: ..."), saying what changed or why nothing did. A flagged claim in an exploratory analysis's "Finding" line (ids E1,
E2, ...) is corrected through "exploratory_findings": {"E1": "the corrected one-sentence finding"}; use "" to withdraw a
finding the table does not support. A claim flagged a second time ("Still overstated/unsupported") must be reworded more
conservatively or deleted, never repeated."""

AUTHOR_EXPLORATORY_NOTE = """

## Hypotheses and subgroups tagged exploratory (ids like X1, XS1)
These were specified by the authors after seeing the data and are estimated by the same deterministic scripts as the
registered tests (not by model-written code). Write a results[<id>] note for each, as for the registered ones, and say once
that they are post hoc. They may appear in at most two takeaways, each introduced as exploratory, and must never qualify a
registered result in the abstract. The pipeline's own exploratory analyses (E1, E2, ...) remain unreviewed model code."""


def run(ctx: dict, review_issues: list[dict] | None = None, revision: dict | None = None) -> dict | None:
    """Write the report's sections; with `revision` (the checking agent's correction list plus "previous_sections"), revise that
    draft instead and return None when the model returns nothing usable, so the caller keeps the draft."""
    pap, study, tags = ctx["pap"], ctx["study_dir"], ctx["tags"]
    tag_by = {t["analysis_id"]: t for t in tags}
    hyps = "\n".join(f"- {h['id']} [{tag_by.get(h['id'], {}).get('tag', '?')}{': ' + tag_by[h['id']]['justification'] if tag_by.get(h['id'], {}).get('tag') == 'deviation' else ''}]: {h['text']} (outcome {h['outcome']}, direction {h.get('direction')})"
                     for h in pap["implemented"]["hypotheses"])
    subs = "\n".join(f"- {s['id']} [{tag_by.get(s['id'], {}).get('tag', '?')}]: {s.get('expected', '')} (moderator {s['moderator']})"
                     for s in pap["implemented"].get("subgroups", []))
    expl = "\n".join(f"- {e['id']}: {e.get('title')} — {e.get('finding')} [table {e.get('table')}]" for e in ctx.get("exploratory", []))
    from .. import package as PKG
    summary_rows = {r["analysis_id"] + ("|" + r.get("term", "") if r.get("term") else ""): r for r in PKG.read_summary(study)}
    key_numbers = "\n".join(PKG._key_findings(pap, summary_rows)) or "(none)"
    tables = [f"### Key numbers (significant registered effects and pooled estimates; use these in the takeaways)\n{key_numbers}"]
    rows_by_id: dict[str, list[dict]] = {}
    for r in PKG.read_summary(study):
        rows_by_id.setdefault(r["analysis_id"].split(":", 1)[0], []).append(r)
    keep = ("estimate", "std_error", "p_value", "n", "term", "arm", "p_directional", "continuous")

    def rows_md(rows):
        cols = [c for c in keep if any(r.get(c) not in (None, "") for r in rows)]
        out = ["| analysis_id | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c, "")
                try:
                    v = f"{float(v):.4g}" if c not in ("term", "arm", "continuous") and v not in ("", None) else v
                except ValueError:
                    pass
                vals.append(str(v))
            out.append(f"| {r['analysis_id']} | " + " | ".join(vals) + " |")
        return "\n".join(out)

    for h in pap["implemented"]["hypotheses"]:
        head = f"### {h['id']}" + (f" ({h['label']})" if h.get("label") else "") + (f" [group {h['group']}]" if h.get("group") else "")
        if (study / "results" / f"{h['id']}_arms.csv").exists():
            tables.append(f"{head}: one row per level/arm against the reference/control\n"
                          + csv_to_markdown(study / "results" / f"{h['id']}_arms.csv", max_rows=20))
        elif rows_by_id.get(h["id"]):
            tables.append(f"{head}\n" + rows_md(rows_by_id[h["id"]]))
    for sg in pap["implemented"].get("subgroups", []):
        inter = [r for r in rows_by_id.get(sg["id"], []) if r.get("term")]
        tables.append(f"### {sg['id']}_by_level ({sg.get('expected', '')})\n"
                      + csv_to_markdown(study / "results" / (sg["id"] + "_by_level.csv"), max_rows=40)
                      + (f"\nInteraction / difference tests for {sg['id']}:\n" + rows_md(inter) if inter else ""))
    groups = pap["implemented"].get("groups") or {}
    if groups:
        tables.append("### Groups (write ONE results note per group, keyed by the group id, instead of notes for its members)\n"
                      + "\n".join(f"- {gid}: {title} — members " + ", ".join(h["id"] for h in pap["implemented"]["hypotheses"] if h.get("group") == gid)
                                   for gid, title in groups.items()))
    for e in ctx.get("exploratory", []):
        if e.get("table"):
            tables.append(f"### {e['id']}\n{csv_to_markdown(study / e['table'], max_rows=10)}")
    lit = (ctx.get("lit") or {}).get("text") or "(none)"
    task = (f"Title: {ctx['meta']['title']}\nSynthetic data: {bool(ctx['meta'].get('synthetic'))}\n"
            f"Registration: {json.dumps(pap.get('registration') or {'status': 'registered'})}\n"
            f"Design: {json.dumps(pap.get('design'))}\nN raw/analysis: {ctx['meta']['n_raw']}/{ctx['meta']['n_analysis']}\n\n"
            f"## Hypotheses\n{hyps}\n{subs}\n\n## Tables\n" + "\n\n".join(tables) +
            f"\n\n## Exploratory\n{expl or '(none)'}\n\n## Literature note\n{lit}")
    w = P.words(pap)
    if w["type"] == "conjoint":
        task += CONJOINT_NOTE
    if not w["causal"]:
        task += OBSERVATIONAL_NOTE
    if w["sample_kind"] != "human":
        task += SYNTHETIC_NOTE.format(model=((pap.get("design") or {}).get("synthetic") or {}).get("model", "unstated"))
    if any(tag_by.get(h["id"], {}).get("tag") == "exploratory" for h in pap["implemented"]["hypotheses"]):
        task += AUTHOR_EXPLORATORY_NOTE
    if ctx.get("addenda_prompt"):
        task += ("\n\n## Robustness addenda (ids like H2a) added in response to the automated reviewer\n" + ctx["addenda_prompt"]
                 + "\nFor each addendum write results[<id>] in ≤ 80 words: what changed relative to its base hypothesis, the new estimate with its CI, "
                   "and whether the registered conclusion holds. Do not revise the registered results' text to match the addendum.")
    if review_issues:
        task += "\n\n## Reviewer issues to address in this revision\n" + "\n".join(f"- [{i.get('severity')}] {i.get('location')}: {i.get('issue')} → {i.get('fix')}" for i in review_issues)
    previous = None
    if revision:
        previous = revision.get("previous_sections") or {}
        letter = {k: v for k, v in revision.items() if k != "previous_sections"}
        letter["editorial"] = [f"G{n}: {e}" for n, e in enumerate(letter.get("editorial") or [], 1)]
        task += (REVISION_NOTE + "\n\n### Correction list\n" + json.dumps(letter, ensure_ascii=False)
                 + "\n\n### Draft to revise\n" + json.dumps(previous, ensure_ascii=False))
    name = "writer_revise" if revision else "writer"
    agent = make_agent(name, ctx, SYSTEM.replace("an unpublished survey experiment", f"an unpublished {w['phrase']}"), None)
    agent.max_turns = 1
    res = agent.run(task)
    out = extract_json(res.content)
    if not isinstance(out, dict):
        res = agent.run(task + "\n\nYour previous answer was not valid JSON. Return only the JSON object.")
        out = extract_json(res.content) or {}
    if revision:
        if not isinstance(out, dict) or not (out.get("abstract") or out.get("summary")):
            return None
        notes = [str(n) for n in (out.get("revision_notes") or []) if str(n).strip()]
        ef = {**(previous.get("exploratory_findings") or {}), **(out.get("exploratory_findings") or {})} \
            if isinstance(out.get("exploratory_findings") or {}, dict) else previous.get("exploratory_findings")
        out = {**previous, **out, "revision_notes": notes}
        if ef:
            out["exploratory_findings"] = ef
    for k in ("results", "exploratory"):
        if not isinstance(out.get(k), dict):
            out[k] = {}
    return out
