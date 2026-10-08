"""Shared helpers for agents: compact context builders and robust JSON extraction."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


from ..config import budget, PKG_ROOT
from ..llm.agent import Agent
from ..tools import ToolRegistry

VOCAB = json.loads((PKG_ROOT / "vocab" / "constructs.json").read_text(encoding="utf-8"))


def make_agent(name: str, ctx: dict, system: str, tools: ToolRegistry | None) -> Agent:
    b = budget(ctx["cfg"], name)
    return Agent(name, ctx["provider"], b["model"], system, tools, max_turns=b["turns"], max_tokens=b["max_tokens"])


def csv_to_markdown(path: str | Path, max_rows: int = 30, digits: int = 3) -> str:
    path = Path(path)
    if not path.exists():
        return f"(missing: {path.name})"
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return "(empty)"
    head, body = rows[0], rows[1:max_rows + 1]

    def fmt(v: str) -> str:
        try:
            f = float(v)
        except ValueError:
            return v.replace("|", "/")[:60]
        if f.is_integer() and abs(f) < 1e7 and "." not in v.strip():
            return str(int(f))
        if f.is_integer() and abs(f) >= 1:
            return str(int(f))
        if abs(f) < 1e-3 and f != 0:
            return f"{f:.{digits}g}"
        return f"{f:.{digits}f}"
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(fmt(v) for v in r) + " |" for r in body]
    if len(rows) - 1 > max_rows:
        out.append(f"| … {len(rows) - 1 - max_rows} more rows | " + " |" * (len(head) - 1))
    return "\n".join(out)


def codebook_summary(cb, max_cols: int = 80) -> str:
    """name | kind | label | values — the schema an agent sees. Never includes responses."""
    lines = []
    cols = [c for c in cb.columns if not c.get("dropped")] or []
    for c in cols[:max_cols]:
        vals = c.get("values") or {}
        vs = "; ".join(f"{k}={v[:28]}" for k, v in list(vals.items())[:8]) + ("; …" if len(vals) > 8 else "")
        lines.append(f"{c['name']} | {c.get('kind','')}/{c.get('dtype','')} | {str(c.get('label',''))[:90]} | {vs}")
    arms = cb.arms
    arm_line = f"Arms column `{arms.column}` with values {arms.values} (source: {arms.source})" if arms.column else "No arms detected in the schema."
    dropped = [c["name"] for c in cb.columns if c.get("dropped")]
    return (f"Survey: {cb.survey.get('name','')} (schema source: {cb.survey.get('source')})\n{arm_line}\n"
            f"Identifier/free-text columns removed before analysis: {dropped}\n"
            "Columns (name | kind/dtype | label | value labels):\n" + "\n".join(lines))


def extract_json(text: str) -> Any:
    """Parse JSON from model text; tolerates ```json fences and leading prose."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    cand = m.group(1) if m else text
    for start in (cand.find("{"), cand.find("[")):
        if start >= 0:
            try:
                return json.loads(cand[start:])
            except json.JSONDecodeError:
                dec = json.JSONDecoder()
                try:
                    obj, _ = dec.raw_decode(cand[start:])
                    return obj
                except json.JSONDecodeError:
                    continue
    return None


def table_summary(path: Path) -> str:
    """One line computed over every row of a results table, so a reader shown only its first rows can still check
    claims about all of them: row count, p < 0.05 count and the smallest p, and for tables with several samples
    (e.g. full vs completers) the largest change in an estimate between them."""
    import pandas as pd
    try:
        df = pd.read_csv(path)
    except Exception:
        return ""
    bits = [f"{len(df)} rows"]
    label = next((c for c in ("arm", "term", "level", "subgroup") if c in df.columns), None)
    if "p_value" in df.columns:
        p = pd.to_numeric(df["p_value"], errors="coerce")
        if p.notna().any():
            groups = [(None, df)] + ([(a, g) for a, g in df.groupby("analysis")] if "analysis" in df.columns and df["analysis"].nunique() > 1 else [])
            for a, g in groups:
                pg = pd.to_numeric(g["p_value"], errors="coerce")
                i = pg.idxmin()
                where = f" ({g.at[i, label]})" if label else ""
                bits.append(f"{'' if a is None else str(a) + ': '}{int((pg < 0.05).sum())} of {int(pg.notna().sum())} with p < 0.05, smallest p {pg.min():.4g}{where}")
    if "sample" in df.columns and "estimate" in df.columns and df["sample"].nunique() > 1:
        keys = [c for c in ("analysis", "term", "arm") if c in df.columns]
        wide = df.pivot_table(index=keys, columns="sample", values="estimate", aggfunc="first") if keys else None
        if wide is not None and wide.shape[1] > 1:
            diff = (wide.max(axis=1) - wide.min(axis=1)).abs()
            ns = {s: sorted(set(int(x) for x in g["n"].dropna())) for s, g in df.groupby("sample")} if "n" in df.columns else {}
            bits.append(f"samples {', '.join(map(str, wide.columns))}: largest change in an estimate between samples {diff.max():.4g}"
                        + (f"; n by sample {ns}" if ns else ""))
    return "Summary over all rows: " + "; ".join(bits) + "."


def results_digest(study_dir: Path, max_rows: int = 30, summaries: bool = False) -> str:
    """Markdown digest of results/*.csv for the writer/exploratory/reviewer agents. With `summaries`, each table opens
    with a line computed over all its rows (for checkers, who may see only the first `max_rows`)."""
    parts = []
    rd = study_dir / "results"
    order = ["registered_summary.csv"]
    files = sorted(rd.glob("*.csv")) if rd.exists() else []
    files = [rd / o for o in order if (rd / o).exists()] + [f for f in files if f.name not in order]
    for f in files:
        head = f"### results/{f.name}\n" + (table_summary(f) + "\n\n" if summaries else "")
        parts.append(head + csv_to_markdown(f, max_rows=max_rows))
    return "\n\n".join(parts)
