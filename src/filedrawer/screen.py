"""Screening a submission from its harvested metadata alone (no model call): the drawer takes research projects that
carry data or analysis code and describe a study. Poetry, fiction, personal notes, link lists and empty repositories
are turned away before anything is stored.

screen(rec) -> {"ok": bool, "checks": [{"name", "passed", "required", "detail"}], "reasons": [...]}
"""
from __future__ import annotations

import re

RESEARCH_TERMS = (
    "study", "studies", "experiment", "experimental", "survey", "respondent", "participants", "sample", "hypothesis",
    "hypotheses", "pre-regist", "preregist", "registered", "treatment", "control group", "randomi", "regression",
    "estimate", "effect", "analysis", "analyses", "replication", "dataset", "data set", "variable", "outcome",
    "measure", "method", "results", "findings", "statistical", "p-value", "confidence interval", "model", "panel",
    "observational", "conjoint", "codebook", "questionnaire", "fieldwork", "interview", "coding scheme", "corpus",
)
OFF_TOPIC = ("poem", "poetry", "poetic", "verse", "lyrics", "song", "short story", "fiction", "novel", "fanfic",
             "fan fiction", "haiku", "sonnet", "recipe", "diary", "journal entry", "my thoughts", "manifesto", "meme")
MIN_TERMS = 3


def _text(rec: dict) -> str:
    parts = [rec.get("title"), rec.get("summary"), rec.get("readme_excerpt"), " ".join(rec.get("keywords") or []),
             " ".join(str(h.get("text") or h.get("label") or "") for h in rec.get("hypotheses") or [] if isinstance(h, dict))]
    return " ".join(str(p) for p in parts if p).lower()


def screen(rec: dict, require_package: bool = True) -> dict:
    """require_package: only self-run filedrawer packages (a study.json the author's own pipeline run wrote) are
    listed; plain archives are turned away."""
    files = rec.get("files") or {}
    package = rec.get("kind") == "filedrawer_package"
    n_data, n_scripts = int(files.get("n_data") or 0), int(files.get("n_scripts") or 0)
    langs = files.get("languages") or []
    text = _text(rec)
    hits = sorted({t for t in RESEARCH_TERMS if t in text})
    off = sorted({t for t in OFF_TOPIC if re.search(r"\b" + re.escape(t) + r"\b", text)})
    need = 1 if (n_data > 0 and n_scripts > 0) else MIN_TERMS     # data plus code is strong evidence by itself
    checks = [
        {"name": "data or analysis code", "required": True, "passed": package or n_data > 0 or n_scripts > 0,
         "detail": (f"{n_data} data file(s), {n_scripts} script(s)" + (f" ({', '.join(langs)})" if langs else "")
                    + ("; a filedrawer study package" if package else ""))},
        {"name": "describes a study", "required": True, "passed": package or len(hits) >= need,
         "detail": (f"research vocabulary found: {', '.join(hits[:8])}" if hits else "no research vocabulary in the title, summary or README")
                   + ("" if package or len(hits) >= need else f" (needs at least {need} term{'s' if need > 1 else ''})")},
        {"name": "not creative or personal writing", "required": True, "passed": not off or len(hits) >= 2 * MIN_TERMS,
         "detail": f"flagged words: {', '.join(off)}" if off else "none of the off-topic markers"},
        {"name": "self-run filedrawer package", "required": require_package, "passed": package,
         "detail": "study.json and report.md written by the author's own filedrawer run" if package
                   else "no study.json: run filedrawer on your own machine, push the package to GitHub, then submit it"},
        {"name": "structured analyses", "required": False, "passed": bool(rec.get("hypotheses")),
         "detail": f"{len(rec.get('hypotheses') or [])} tagged analyses" if rec.get("hypotheses") else "none (a plain replication archive is fine)"},
    ]
    failed = [c for c in checks if c["required"] and not c["passed"]]
    reasons = [f"{c['name']}: {c['detail']}" for c in failed]
    return {"ok": not failed, "checks": checks, "reasons": reasons}
