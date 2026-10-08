"""Exploratory agent: proposes and runs up to three additional analyses, all tagged exploratory."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .base import make_agent, results_digest, codebook_summary
from ..analysis.profile import profile_to_text
from ..tools import ToolRegistry

SCRIPT = "scripts/04_exploratory.py"

SYSTEM = """You are a quantitative social scientist adding a small set of EXPLORATORY analyses (at most three) to a survey experiment whose registered tests are already done. Choose analyses that help interpret the registered results: e.g. effects on component items, robustness to attention-check failures or alternative exclusions, heterogeneity by a demographic the PAP did not register, dose/manipulation checks, or a placebo. Avoid fishing; each analysis must have a one-sentence rationale.

Write ONE script scripts/04_exploratory.py (pandas, statsmodels, matplotlib Agg) that reads data/clean.csv, writes one tidy CSV per analysis to results/E<k>_<slug>.csv and at most two PNG figures to figures/ (minimal style: white background, no top/right/left spines, no gridlines, dark ink, one accent colour at most, direct labels instead of legends, 200 dpi), and prints exactly one summary line per analysis in the form `E1 <title>: <finding with the key number>`. Use HC2 robust SEs. That is the only file you may write: never create helper or debug scripts; to inspect something, print it from 04_exploratory.py.
Run it with run_script. As soon as a run exits 0 with tables written, call in your NEXT turn
record_result(key="exploratory", value=[{"id":"E1","title":str,"rationale":str,"method":"one sentence for readers: what was compared and how, with no code, function or column names","table":"results/E1_x.csv","figure":"figures/..png" or null,"finding":one sentence with the key number}], final=true).
Do not polish a script that already works. If one analysis keeps failing, delete it from the script and record the others.
Reuse the registered script's helpers instead of writing your own model code. At the top of your script:
    import importlib.util, pathlib
    _spec = importlib.util.spec_from_file_location('reg', pathlib.Path(__file__).with_name('03_registered.py')); reg = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(reg)
Then `reg.reestimate(hid, d)` re-fits registered hypothesis `hid` (e.g. "H1") with exactly its registered specification (exclusions, treatment arms, Lin centring, weights, HC2) on any subset `d` of the clean data and returns a DataFrame with one row per treatment arm (columns term, arm, estimate, std_error, p_value, conf_low, conf_high, n). `reg.reestimate(hid, d, moderator="col")` returns the arm x moderator interactions instead: one centred slope per arm for a numeric moderator, one row per level for a text or 0/1 column (bin a numeric moderator into a 0/1 column to compare two groups). These cover robustness checks and heterogeneity; do not build formulas or look up coefficient names yourself. `reg.HYPOTHESES` lists the hypothesis dicts (ids, outcomes, estimators). For anything else (e.g. a component item as the outcome), copy the hypothesis and change its outcome: `h = dict(reg.HYPOTHESES[0], outcome="item_col"); reg.reestimate(h, d)`. Convert any object-typed numeric column with `pd.to_numeric(..., errors='coerce')` first.
A robustness table must hold the original and the re-estimated estimate and SE side by side (both from reg.reestimate). If a standard error changes by more than a factor of two while the sample changes by less than 10%, drop that analysis.
Write rationales and methods in the third person (never 'I'). Call the plan's analyses 'planned', not 'registered', unless the study was pre-registered (see Registration in the task). Describe every variable only as the codebook does; a column the codebook does not describe is named by its column name (never call a knowledge score an attention check, for example). With several arms, a finding states the pattern across arms and how many estimates were tested, not just the most extreme one.
Never print rows of data. Do not read data files directly."""


def run(ctx: dict) -> list[dict]:
    # Everything the agent needs to know is in the task text; browsing tools only burn turns.
    tools = ToolRegistry(ctx["study_dir"], timeout_s=ctx["cfg"]["analysis"]["script_timeout_s"],
                         language=ctx["cfg"]["analysis"]["language"],
                         allowed=("write_file", "run_script", "record_result"), writable=(SCRIPT,))
    agent = make_agent("exploratory", ctx, SYSTEM, tools)
    agent.finish_turn = True
    pap = ctx["pap"]
    task = (f"You have {agent.max_turns} turns. Turn 1: write_file the full script. Turn 2: run_script. Fix and re-run if needed; "
            f"the turn after the first clean run, record_result with final=true. The last turn is reserved for record_result.\n\n"
            f"## Design\n{json.dumps(pap.get('design'))}\n\n## Registration\n{json.dumps(pap.get('registration') or {'status': 'registered'})}\n\n## Planned hypotheses (already tested)\n"
            + "\n".join(f"- {h['id']}: {h['text']} (outcome {h['outcome']})" for h in pap['implemented']['hypotheses'])
            + f"\n\n## Registered results\n{results_digest(ctx['study_dir'], max_rows=12)}\n\n"
            f"## Codebook\n{codebook_summary(ctx['codebook'])}\n\n## Profile of data/clean.csv\n{profile_to_text(ctx['clean_profile'])}")
    res = agent.run(task)
    out = res.records.get("exploratory") or []
    if isinstance(out, dict):
        out = [out]
    out = [e for e in out if isinstance(e, dict) and e.get("id")][:3]
    return out or salvage(Path(ctx["study_dir"]), tools.runs.get(SCRIPT))


def salvage(study: Path, run: dict | None) -> list[dict]:
    """The agent ran the script cleanly but never recorded it: rebuild the records from the script's own
    `E<k> <title>: <finding>` summary lines and the tables it wrote. Only a clean run of the script as it stands
    counts (any later write clears the run), and only analyses with both a summary line and a table."""
    if not run or run.get("exit_code") != 0:
        return []
    out = []
    for m in re.finditer(r"(?m)^(E\d)\b[\s:-]*([^:\n]{2,80}):\s*(.+)$", run.get("stdout_tail") or ""):
        k, title, finding = m.group(1), m.group(2).strip(), m.group(3).strip()
        tables = sorted((study / "results").glob(f"{k}_*.csv"))
        if not tables or any(e["id"] == k for e in out):
            continue
        figs = sorted((study / "figures").glob(f"{k}_*.png"))
        out.append({"id": k, "title": title[:1].upper() + title[1:], "rationale": "", "method": "see scripts/04_exploratory.py",
                    "table": str(tables[0].relative_to(study)), "figure": str(figs[0].relative_to(study)) if figs else None,
                    "finding": finding, "salvaged": True})
    return out[:3]
