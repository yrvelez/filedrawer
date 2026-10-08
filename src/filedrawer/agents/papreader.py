"""Turn the pre-analysis plan text + codebook into pap.json (registered and implemented specs)."""
from __future__ import annotations

import json

from .. import pap as P
from .base import make_agent, codebook_summary, extract_json, VOCAB
from ..tools import ToolRegistry

SYSTEM = """You are a careful methods editor translating a pre-analysis plan (PAP) for a survey experiment into a machine-readable spec. You see the PAP text and the survey codebook (column names, labels, value labels). You never see respondent data.

Produce ONE JSON object and hand it over with the record_result tool: record_result(key="pap", value=<object>, final=true). Do not write prose.

Schema:
{
 "title": str, "constructs": [ids from the allowed list], "keywords": [3-8 short strings],
 "registration": {"status": "registered"|"none", "url": str, "note": str}  (optional; omit or "registered" for a pre-registered plan; "none" when the text is a post hoc analysis plan — then say in "note" what it was reconstructed from),
 "design": {"type": one of DESIGNS,
            "arms": {"column": str, "treatment": value or [values] (a LIST for a multi-arm design), "control": value,
                     "labels": {"value": "short arm label", ...} (optional), "descriptions": {"value": "one-line stimulus description", ...} (optional)},
            "derived": {"new_column": "pandas eval expression over existing columns"} (optional; e.g. {"ipw": "1 / pr"} turns an assignment probability into an inverse-probability weight),
            "population": {"country": str, "sample": one of POPULATIONS, "description": "who was sampled, in the plan's words, e.g. 'US Latino adults, English or Spanish survey' (omit if the plan does not say)"}, "outcome_type": str},
 "registered": {"outcomes": [...], "hypotheses": [...], "subgroups": [...], "sample_exclusions": [pandas query strings selecting the rows to KEEP, e.g. "Finished == 1" for 'exclude unfinished responses'], "multiple_testing": str, "alpha": float},
 "implemented": {same shape as registered},
 "ambiguities": [{"hypothesis": id, "field": dotted field, "kind": "interpretation"|"deviation", "pap_text": quote, "interpretation": what you did, "alternatives": [..], "reason": why}]
}
outcome: {"name": snake_case, "label": str, "kind": "mean_items"|"single_item"|"custom", "columns": [data columns], "reverse": [columns reverse-coded], "scale": [lo, hi], "construction": str, "custom_hint": str (only for custom)}
hypothesis: {"id": "H1", "text": str, "outcome": outcome name, "treatment": {"column": arms column, "contrast": [treated value, control value]} (two-arm) OR {"column": arms column, "arms": [treated values...], "control": value} (multi-arm: one model, one coefficient per arm), "direction": "positive"|"negative"|"two_sided", "estimator": {"kind": "ols"|"diff_means"|"lin"|"custom", "robust": "HC2"|"HC1"|"HC3", "cluster": column or null, "weights": column or null, "covariates": [columns], "custom_hint": str}, "pooled": true|false (multi-arm only; default true = also report a random-effects pooled estimate across the arm effects), "subgroup": null, "exclusions": [pandas query strings]}
Estimators: "ols" = outcome on a treatment indicator plus covariates; "diff_means" = no covariates; "lin" = Lin (2013) regression adjustment (covariates centred and interacted with the arm indicators; use it when the PAP says "Lin", "fully interacted" or "covariate-adjusted ATE"). "weights" names a regression-weight column (WLS); "cluster" gives cluster-robust SEs. Arm values are strings ("1", not 1).
Multi-arm example: {"id": "H1", "text": "Each message increases tool use.", "outcome": "ai_scale", "treatment": {"column": "treatment", "arms": ["1","2","3"], "control": "0"}, "direction": "positive", "estimator": {"kind": "lin", "robust": "HC2", "cluster": null, "weights": "ipw", "covariates": ["media_trust","political_interest"]}, "pooled": true, "subgroup": null, "exclusions": []}
subgroup: {"id": "S1", "hypothesis": "H1", "moderator": column, "levels": {"value": "label", ...} (only the levels compared; empty object = all levels), "expected": str}

Covariate types: codebook-labelled columns are treated as categorical (C()) automatically; set estimator.continuous: [cols] to force a labelled ordinal item (e.g. a 1-5 interest scale) to enter linearly, or estimator.categorical: [cols] to force dummies. Match what the plan says.

Rules:
- "registered" is a literal reading of the PAP, using the PAP's own variable descriptions (if the PAP names a variable that does not exist in the data, keep it in registered, e.g. "age_years").
- "implemented" must be runnable on the listed columns. Where you had to choose because the PAP was silent or vague, copy the choice into both sections and log an ambiguity with kind "interpretation". Where implemented CONTRADICTS the PAP (variable not collected, different estimator, different sample), keep registered as written, change implemented, and log an ambiguity with kind "deviation" naming the exact field (e.g. "estimator.covariates").
- Covariates and moderators must be column names. Prefer HC2 robust SEs when the PAP is silent. Set cluster only if the design has repeated observations per respondent.
- Never substitute a different measure for a planned outcome. If the outcome the PAP names cannot be built from the listed columns, keep it in registered, build the closest faithful version in implemented only if it measures the SAME construct (e.g. the same items scored the same way), and otherwise still list the PAP's outcome in implemented with the columns you could not find, so the run stops and the author can supply them. A different construct (e.g. confidence instead of accuracy) is never an acceptable stand-in.
- "direction": use "positive" or "negative" only when the PAP states the expected sign for that hypothesis; otherwise "two_sided". Never infer a direction from the intervention's purpose.
- If no registration is mentioned, or the text says the plan was written after data collection, set registration.status to "none".
- Keep text fields short. Do not invent hypotheses that are not in the PAP; exploratory ideas are handled elsewhere.
"""


def exclusion_errors(pap: dict, study) -> list[str]:
    """Exclusions are rows to KEEP. A query that keeps under half the sample is almost always inverted
    ("Finished != 1" for "exclude unfinished responses"); report it with the counts so the repair turn can fix it."""
    import pandas as pd
    path = study / "data" / "raw_tidy.csv"
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception:  # noqa: BLE001
        return []
    n = len(df)
    errs, seen = [], set()
    imp = pap.get("implemented") or {}
    queries = [("sample_exclusions", q) for q in imp.get("sample_exclusions") or []]
    queries += [(f"{h.get('id')}.exclusions", q) for h in imp.get("hypotheses") or [] for q in h.get("exclusions") or []]
    for where, q in queries:
        if not isinstance(q, str) or (where, q) in seen or not n:
            continue
        seen.add((where, q))
        try:
            kept = len(df.query(q))
        except Exception:  # noqa: BLE001 - validate() reports unparsable queries
            continue
        if kept < 0.5 * n:
            errs.append(f"{where}: {q!r} keeps {kept} of {n} rows. Exclusion queries select the rows to KEEP; if the plan "
                        f"says to exclude these rows, invert the condition.")
    return errs


def run(ctx: dict) -> dict:
    cb = ctx["codebook"]
    pap_text = ctx["pap_text"]
    data_cols = ctx["data_columns"]
    tools = ToolRegistry(ctx["study_dir"], allowed=("record_result",))
    agent = make_agent("papreader", ctx, SYSTEM, tools)
    task = (f"ALLOWED constructs: {', '.join(VOCAB['constructs'].keys())}\nDESIGNS: {VOCAB['designs']}\n"
            f"POPULATIONS: {VOCAB['populations']}\n\n## Codebook\n{codebook_summary(cb)}\n\n"
            f"## Pre-analysis plan\n{pap_text[:12000]}")
    res = agent.run(task)
    pap = res.records.get("pap")
    if not isinstance(pap, dict) and res.content:
        # some models answer with the JSON as text instead of calling record_result
        cand = extract_json(res.content)
        if isinstance(cand, dict) and ("implemented" in cand or "registered" in cand):
            pap = cand
    if not isinstance(pap, dict):
        hint = (" The model replied with text but no spec: " + res.content[:300]) if res.content else \
               " The model returned no text and no tool call; try a different models.strong."
        raise SystemExit(f"papreader did not return a spec (stopped={res.stopped}, model={agent.model}).{hint}")
    pap.setdefault("source", ctx.get("pap_source", "pap.md"))
    errs = P.validate(pap, data_cols) + exclusion_errors(pap, ctx["study_dir"])
    if errs:
        # one repair turn
        tools2 = ToolRegistry(ctx["study_dir"], allowed=("record_result",))
        agent2 = make_agent("papreader", ctx, SYSTEM, tools2)
        res2 = agent2.run(task + "\n\n## Your previous spec had problems; fix them and re-submit the full object:\n- "
                          + "\n- ".join(errs) + "\n\nPrevious spec:\n" + json.dumps(pap)[:8000])
        pap2 = res2.records.get("pap")
        if isinstance(pap2, dict):
            pap2.setdefault("source", pap.get("source"))
            pap = pap2
        errs = P.validate(pap, data_cols) + exclusion_errors(pap, ctx["study_dir"])
        if errs:
            raise SystemExit("pap.json is not runnable:\n- " + "\n- ".join(errs))
    # constrain constructs to the vocabulary
    pap["constructs"] = [c for c in pap.get("constructs", []) if c in VOCAB["constructs"]]
    return pap
