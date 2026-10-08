"""Cleaning agent: only invoked when an outcome has kind == "custom". Starts from the template."""
from __future__ import annotations

from .base import make_agent, codebook_summary
from ..analysis.profile import profile_to_text
from ..tools import ToolRegistry

SYSTEM = """You write and run a short pandas script scripts/02_clean.py that turns data/raw_tidy.csv into data/clean.csv. The script already exists (a template). Use read_file to see it, then write_file to edit it so every outcome with kind == "custom" is constructed per its custom_hint, keep the rest unchanged, run it with run_script, fix errors, and stop when it exits 0. Print only aggregates (means, counts), never rows. Do not read data files directly; you cannot."""


def needs_agent(pap: dict) -> bool:
    return any(o.get("kind") == "custom" for o in pap["implemented"]["outcomes"])


def run(ctx: dict) -> dict:
    tools = ToolRegistry(ctx["study_dir"], timeout_s=ctx["cfg"]["analysis"]["script_timeout_s"],
                         language=ctx["cfg"]["analysis"]["language"])
    agent = make_agent("cleaner", ctx, SYSTEM, tools)
    task = (f"## Codebook\n{codebook_summary(ctx['codebook'])}\n\n## Profile of data/raw_tidy.csv\n"
            f"{profile_to_text(ctx['raw_profile'])}\n\n## Outcomes to construct\n{ctx['pap']['implemented']['outcomes']}\n\n"
            "Start by read_file('scripts/02_clean.py').")
    res = agent.run(task)
    return {"stopped": res.stopped, "turns": res.turns}
