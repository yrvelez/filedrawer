"""External reviewers: coarse (run locally through its CLI) and refine (no API: the author uploads the report
on refine.ink and imports the returned review).

Both return a free-form referee report. `structure()` turns it into review.json issues with one call to the
`review_import` agent, so `filedrawer address` can answer them like the in-platform reviews.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..config import budget
from ..llm.agent import Agent
from .common import parse_comments, plan_facts, rank, study_note, to_issue

EXTERNAL = {"coarse": "C", "refine": "RF", "openreview": "OR"}          # source -> issue id prefix
MAX_IMPORTED = 12

IMPORT_SYSTEM = """You convert an external referee report on a research report into a JSON array of comments.
Keep every substantive point the referee makes; merge duplicates; drop praise and summaries. Do not add points of
your own and do not soften or sharpen the referee's claims. Respond ONLY with a JSON array of objects with keys:
    section_ref: string (the section or hypothesis the comment is about)
    issue: string (short title, max 15 words)
    detail: string (the referee's point, with any quoted passage or number)
    severity: "critical" | "major" | "minor" | "suggestion" (use the referee's own rating when there is one)
    suggestion: string (the referee's proposed fix, or "" when none)
    confidence: 1.0"""


def read_review_file(path: Path) -> str:
    """Text of an exported review: .md/.txt/.html as is, .pdf through pdftotext."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            raise SystemExit("reading a PDF review needs pdftotext (poppler); or export the review as Markdown/text.")
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise SystemExit(f"pdftotext failed on {path}: {out.stderr.strip()[:300]}")
        return out.stdout
    if path.suffix.lower() == ".docx":
        raise SystemExit("export the review as PDF or Markdown/text; .docx is not read.")
    return path.read_text(encoding="utf-8", errors="replace")


def structure(ctx: dict, text: str, source: str) -> dict:
    """External referee text -> {"model", "issues"} (ids assigned by the caller)."""
    _, hyps = plan_facts(ctx)
    b = budget(ctx["cfg"], "review_import")
    agent = Agent("review_import", ctx["provider"], b["model"], IMPORT_SYSTEM + study_note(ctx), None,
                  max_turns=1, max_tokens=b["max_tokens"])
    out = parse_comments(agent.run(f"## Referee report ({source})\n\n{text[:80000]}").content) or []
    issues = rank([i for i in (to_issue(c, source, hyps) for c in out) if i], MAX_IMPORTED)
    return {"model": agent.model, "issues": issues}


def run_coarse(ctx: dict) -> dict:
    """Review report.md with coarse (`coarse-ink review`); returns {"model", "issues", "notes", "raw"}.

    Output streams to provenance/coarse.log. The command comes from `coarse.command` (default `uvx --python 3.12 coarse-ink`, so no install is needed);
    OPENROUTER_API_KEY is inherited from the environment. The raw review is kept at provenance/coarse_review.md.
    """
    study = ctx["study_dir"]
    ccfg = ctx["cfg"].get("coarse") or {}
    cmd = list(ccfg.get("command") or ["uvx", "--python", "3.12", "coarse-ink"])
    if ctx["provider"].name == "mock":                     # tests: the fixture stands in for coarse's report
        fx = Path(getattr(ctx["provider"], "fixtures", "")) / "coarse" / "review.md"
        raw = fx.read_text(encoding="utf-8") if fx.exists() else ""
    else:
        if not shutil.which(cmd[0]):
            return {"model": "", "issues": [], "raw": "", "notes": [f"coarse: `{cmd[0]}` not found; install uv or set coarse.command"]}
        if not os.environ.get("OPENROUTER_API_KEY"):
            return {"model": "", "issues": [], "raw": "", "notes": ["coarse: OPENROUTER_API_KEY is not set"]}
        out_md = study / "provenance" / "coarse_review.md"
        args = cmd + ["review", str(study / "report.md"), "--yes", "-o", str(out_md)]
        if ccfg.get("model"):
            args += ["--model", ccfg["model"]]
        log_path = study / "provenance" / "coarse.log"        # coarse takes many minutes; tail this to watch it
        timeout = int(ccfg.get("timeout_s", 3600))
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                rc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                    timeout=timeout, cwd=study).returncode
            except subprocess.TimeoutExpired:
                rc = None
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-400:]
        if rc is None:
            return {"model": "", "issues": [], "raw": "", "notes": [f"coarse: timed out after {timeout}s (see provenance/coarse.log)"]}
        if rc != 0 or not out_md.exists():
            return {"model": "", "issues": [], "raw": "", "notes": [f"coarse failed (see provenance/coarse.log): {tail}"]}
        raw = out_md.read_text(encoding="utf-8")
    if not raw.strip():
        return {"model": "", "issues": [], "raw": "", "notes": ["coarse: empty review"]}
    st = structure(ctx, raw, "coarse")
    return {"model": ccfg.get("model") or "coarse default", "issues": st["issues"], "raw": raw, "notes": []}


REFINE_STEPS = """Refine has no API. To add its review:
1. Upload `report.md` (or a PDF of it) at https://www.refine.ink and wait for the review.
2. Save the review (PDF, Markdown or text) and run:
   filedrawer review-import {study} refine <file>"""

OPENREVIEW_STEPS = """OpenReview (or any referee report) has no API here. To add one:
1. Save the referee report (PDF, Markdown or text), e.g. a review posted on an OpenReview submission.
2. Run: filedrawer review-import {study} openreview <file>"""
