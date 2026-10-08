"""Advanced pass: a methodology reviewer and a statistics reviewer, one call each over the whole report.

Prompts live in `review/prompts/{methodology,statistics}.yaml` (`system` + `user` with `{paper_title}`,
`{abstract}`, `{section_text}`); `advanced.prompts` in the config or `--advanced-prompts DIR` points at another
copy. Each returns comments `{section_ref, issue, detail, severity, suggestion, confidence, kind, target}`,
mapped onto review.json issues A1, A2, ... by the dispatcher.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import budget
from ..llm.agent import Agent
from .common import parse_comments, plan_facts, rank, report_payload, study_note, to_issue

AGENTS = ("methodology", "statistics")
VENDORED = Path(__file__).resolve().parent / "prompts"
MAX_PER_AGENT = 6


def load_prompts(prompts_dir: str | Path | None = None, agents=AGENTS) -> tuple[dict, list[str]]:
    """{agent: {"system", "user", "source"}}; falls back to the packaged copy per agent. Returns (prompts, notes)."""
    prompts, notes = {}, []
    for name in agents:
        cands = ([Path(prompts_dir).expanduser() / f"{name}.yaml"] if prompts_dir else []) + [VENDORED / f"{name}.yaml"]
        for c in cands:
            try:
                d = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if d.get("system") and d.get("user"):
                prompts[name] = {"system": d["system"], "user": d["user"], "source": str(c)}
                if prompts_dir and c.parent == VENDORED:
                    notes.append(f"advanced: {name}.yaml not usable in {prompts_dir}; used the packaged copy")
                break
        else:
            notes.append(f"advanced: no usable {name}.yaml prompt; agent skipped")
    return prompts, notes


def run(ctx: dict, agents=AGENTS, prompts_dir: str | Path | None = None) -> dict:
    """Run the advanced reviewers on report.md. Returns {"models", "issues", "notes"}; never writes files."""
    prompts, notes = load_prompts(prompts_dir or (ctx["cfg"].get("advanced") or {}).get("prompts"), agents)
    fields = report_payload(ctx)
    _, hyps = plan_facts(ctx)
    note = study_note(ctx)
    b = budget(ctx["cfg"], "advanced")
    issues, models = [], {}
    for name, p in prompts.items():
        agent = Agent(f"advanced_{name}", ctx["provider"], b["model"], p["system"].rstrip() + note, None,
                      max_turns=1, max_tokens=b["max_tokens"])
        user = p["user"]
        for k, v in fields.items():          # plain replacement: a custom template may contain other braces
            user = user.replace("{" + k + "}", v)
        res = agent.run(user)
        out = parse_comments(res.content)
        if out is None:
            notes.append(f"advanced: {name} returned no parsable JSON array" + (f" ({res.error})" if getattr(res, "error", "") else ""))
            out = []
        issues += rank([i for i in (to_issue(c, f"advanced:{name}", hyps) for c in out) if i], MAX_PER_AGENT)
        models[name] = agent.model
    return {"models": models, "issues": issues, "notes": notes}
