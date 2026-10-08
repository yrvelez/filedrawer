"""Responder: turns ONE analytical reviewer issue into a robustness addendum (a spec delta on one hypothesis).

The registered analysis is never changed. An addendum re-runs the base hypothesis with a documented change
(covariates, estimator kind or SEs, weights, extra exclusions, pooling) and is tagged "robustness" in the report.
"""
from __future__ import annotations

import json

from .. import pap as P
from .base import make_agent, codebook_summary
from ..tools import ToolRegistry

SYSTEM = """You are a methods agent responding to ONE reviewer comment about an analysis in a survey-experiment report. Propose at most ONE additional analysis that addresses the comment, as a change to one existing hypothesis. The registered analysis stays as it is; yours is added beside it and labelled a robustness check.

Hand the proposal over with record_result(key="addendum", value=<object>, final=true). Do not write prose.

Schema:
{"base": hypothesis id to re-run (e.g. "H1"),
 "label": short label (≤ 12 words) for what changes, e.g. "add education as a covariate",
 "rationale": one or two sentences linking the change to the reviewer's point,
 "delta": {"estimator": {any of: "kind": "ols"|"diff_means"|"lin", "robust": "HC1"|"HC2"|"HC3", "cluster": column or null, "weights": column or null, "covariates": [full new list of column names], "continuous": [...], "categorical": [...]},
           "exclusions": [additional pandas query strings applied on top of the base hypothesis's exclusions],
           "pooled": true|false,
           "collapse_arms": true  (multi-arm designs only: compare ALL treated respondents with the control arm in one two-arm model; use this when the comment is about pooling arms that share a control group)},
 "expected": one sentence on what result would reassure or worry a reader}

Rules:
- Never change the outcome or the treatment/arms; if the comment needs that, say so in "rationale" and still propose the closest legitimate robustness check, or record_result with {"base": null, "rationale": why no addendum is possible}.
- Only columns listed in the codebook may be used; "covariates" is the complete list after the change, not just the additions.
- Keep it to one change per addendum: one estimator change, or one set of extra exclusions, or a weights/cluster change, or collapse_arms.
- If the comment is about multiple testing or p-value adjustment, say so in "rationale": no addendum can answer it, so set "base": null.
- A missing-data comment is answered with an unadjusted re-fit (drop covariates) or a complete-case definition via exclusions, not with imputation code.
"""


def run(ctx: dict, issue: dict, base_h: dict) -> dict | None:
    cb = ctx.get("codebook")
    cols = ctx["data_columns"]
    codebook_txt = codebook_summary(cb) if cb is not None else "Columns: " + ", ".join(cols)
    tools = ToolRegistry(ctx["study_dir"], allowed=("record_result",))
    agent = make_agent("responder", ctx, SYSTEM, tools)
    task = (f"## Reviewer issue {issue.get('id', '')} [{issue.get('severity', '')}] at {issue.get('location', '')}\n{issue.get('issue', '')}\n"
            f"Suggested fix: {issue.get('fix', '')}\n\n## Base hypothesis\n{json.dumps(base_h, ensure_ascii=False)}\n\n"
            f"## Codebook\n{codebook_txt[:6000]}")
    res = agent.run(task)
    add = res.records.get("addendum")
    if not isinstance(add, dict) or not add.get("base"):
        return None
    errs = P.addendum_errors(ctx["pap"], add, cols)
    if errs:
        tools2 = ToolRegistry(ctx["study_dir"], allowed=("record_result",))
        agent2 = make_agent("responder", ctx, SYSTEM, tools2)
        res2 = agent2.run(task + "\n\n## Your previous proposal was not runnable; fix and resubmit:\n- " + "\n- ".join(errs)
                          + "\n\nPrevious:\n" + json.dumps(add)[:3000])
        add2 = res2.records.get("addendum")
        if isinstance(add2, dict) and add2.get("base") and not P.addendum_errors(ctx["pap"], add2, cols):
            add = add2
        else:
            return None
    return add
