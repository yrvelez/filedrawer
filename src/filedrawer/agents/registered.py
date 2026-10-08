"""Registered-analysis agent: only for hypotheses whose estimator kind == "custom"."""
from __future__ import annotations

import json

from .base import make_agent, csv_to_markdown
from ..analysis.profile import profile_to_text
from ..tools import ToolRegistry

SYSTEM = """You implement pre-registered hypothesis tests whose estimator could not be generated automatically. Write scripts/03b_custom.py (pandas + statsmodels) that reads data/clean.csv, runs each listed hypothesis exactly as its custom_hint describes, writes results/<id>.csv (tidy coefficient table: term, estimate, std_error, statistic, p_value, conf_low, conf_high) and appends one row per hypothesis to results/registered_summary.csv with columns analysis_id, outcome, formula, cov_type, estimate, std_error, p_value, n, mean_control, mean_treated, direction, supported. Run it, fix errors, then record_result(key="custom", value={"<id>": {"estimate":..,"std_error":..,"p_value":..,"n":..}}, final=true). Print only aggregates."""


def needs_agent(pap: dict) -> bool:
    return any(h.get("estimator", {}).get("kind") == "custom" for h in pap["implemented"]["hypotheses"])


def run(ctx: dict) -> dict:
    custom = [h for h in ctx["pap"]["implemented"]["hypotheses"] if h.get("estimator", {}).get("kind") == "custom"]
    tools = ToolRegistry(ctx["study_dir"], timeout_s=ctx["cfg"]["analysis"]["script_timeout_s"],
                         language=ctx["cfg"]["analysis"]["language"])
    agent = make_agent("registered", ctx, SYSTEM, tools)
    task = (f"## Profile of data/clean.csv\n{profile_to_text(ctx['clean_profile'])}\n\n## Hypotheses\n{json.dumps(custom, indent=1)}\n\n"
            f"## Existing results/registered_summary.csv\n{csv_to_markdown(ctx['study_dir'] / 'results' / 'registered_summary.csv')}")
    res = agent.run(task)
    return res.records.get("custom", {})
