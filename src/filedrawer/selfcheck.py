"""First-pass self-check run before a package is submitted: shows the author which code they are running
and proves, on a synthetic study, that respondent text never reaches a model or the submission.

1. Code identity. The tool version, the git commit and whether the installed source has local
   changes, plus a content hash of every .py file in the package, so anyone can compare the code
   that ran with the public source.
2. Canary test. A small synthetic study is generated with a canary sentence in a free-text column
   and a canary email address, and the real pipeline is run on it fully offline (mock model, no
   network). The check passes only if the PII scan dropped both columns, the canaries appear in
   no model request and in no file of the submission bundle, and the bundle holds no data/ file.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import subprocess
import tempfile
from pathlib import Path

from . import __version__
from . import bundle as B

SOURCE_REPO = "https://github.com/yrvelez/filedrawer"
CANARY = "FD-SELFCHECK-CANARY-9c41"
CANARY_EMAIL = "selfcheck.canary@example.org"
PKG_DIR = Path(__file__).resolve().parent


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(PKG_DIR), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def code_identity() -> dict:
    files = sorted(p for p in PKG_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(PKG_DIR).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", str(PKG_DIR)) if commit else None
    out = {"tool_version": __version__, "content_sha256": h.hexdigest(), "n_source_files": len(files),
           "commit": commit[:12] if commit else None, "modified": bool(status) if commit else None,
           "source_url": f"{SOURCE_REPO}/tree/{commit}" if commit else SOURCE_REPO}
    return out


def _plan() -> dict:
    hyp = {"id": "H1", "text": "The message raises support.", "outcome": "support",
           "treatment": {"column": "treat", "contrast": ["1", "0"]}, "direction": "two_sided",
           "estimator": {"kind": "ols", "robust": "HC2", "cluster": None, "weights": None, "covariates": []},
           "subgroup": None, "exclusions": []}
    spec = {"outcomes": [{"name": "support", "label": "Support", "kind": "single_item", "columns": ["support"],
                          "reverse": [], "scale": [1, 7], "construction": "single item"}],
            "hypotheses": [hyp], "subgroups": [], "sample_exclusions": [], "multiple_testing": "none", "alpha": 0.05}
    return {"title": "filedrawer self-check (synthetic)", "constructs": ["persuasion"], "keywords": ["self-check"],
            "registration": {"status": "none", "url": "", "note": "Synthetic self-check study."},
            "design": {"type": "survey_experiment", "arms": {"column": "treat", "treatment": "1", "control": "0",
                                                             "labels": {"1": "Message", "0": "Control"}},
                       "population": {"country": "US", "sample": "online_panel"}, "outcome_type": "attitude"},
            "registered": spec, "implemented": json.loads(json.dumps(spec)), "ambiguities": [], "source": "selfcheck"}


def _write_inputs(d: Path, n: int = 60) -> dict:
    rng = random.Random(20261005)
    csv_path = d / "export.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["treat", "support", "age_group", "comment", "contact_email"])
        for i in range(n):
            t = i % 2
            w.writerow([t, min(7, max(1, round(4 + 0.8 * t + rng.gauss(0, 1.2)))), rng.choice(["18-34", "35-54", "55+"]),
                        f"{CANARY} I wrote this answer myself and it must never leave the machine, respondent {i}.",
                        CANARY_EMAIL if i == 0 else f"person{i}@example.org"])
    (d / "pap.json").write_text(json.dumps(_plan()), encoding="utf-8")
    (d / "pap.md").write_text("# Self-check plan\n\nSynthetic study; the structured plan is in pap.json.\n", encoding="utf-8")
    return {"csv": str(csv_path), "pap": str(d / "pap.md"), "pap_json": str(d / "pap.json")}


def canary_check(workdir: str | Path | None = None) -> dict:
    """Run the pipeline offline on a synthetic study with planted canaries; see the module docstring."""
    from .config import load_config
    from .orchestrator import run_pipeline
    checks, failures = [], []
    tmp_ctx = tempfile.TemporaryDirectory(prefix="fd-selfcheck-") if workdir is None else None
    d = Path(workdir) if workdir is not None else Path(tmp_ctx.name)
    try:
        inputs = _write_inputs(d)
        inputs.update({"slug": "selfcheck", "title": "filedrawer self-check (synthetic)", "authors": ["filedrawer"],
                       "out_dir": str(d / "out"), "repo_url": SOURCE_REPO, "branch": "main"})
        cfg = load_config(overrides={"provider": "mock"})
        flags = {"no_lit": True, "no_exploratory": True, "arm_column": "treat", "synthetic": True}
        study = run_pipeline(inputs, cfg, flags)
        prov = json.loads((study / "provenance" / "provenance.json").read_text(encoding="utf-8"))
        dropped = set((prov.get("pii") or {}).get("dropped") or [])
        (checks if {"comment", "contact_email"} <= dropped else failures).append(
            "PII scan dropped the free-text and email columns before anything else ran")
        log = (study / "provenance" / "llm_log.jsonl")
        log_text = log.read_text(encoding="utf-8") if log.exists() else ""
        (checks if CANARY not in log_text and CANARY_EMAIL not in log_text else failures).append(
            "canaries absent from every model request (provenance/llm_log.jsonl)")
        files = B.collect(study)
        leaked = [rel for rel, b in files.items() if CANARY.encode() in b or CANARY_EMAIL.encode() in b]
        (checks if not leaked else failures).append("canaries absent from every file of the submission bundle"
                                                    + (f" (found in {', '.join(leaked)})" if leaked else ""))
        (checks if not any(rel.startswith("data/") for rel in files) else failures).append("no data/ file in the bundle")
        n_calls = sum(1 for _ in log_text.splitlines() if _.strip())
        checks.append(f"pipeline ran offline with a mock model ({n_calls} logged requests, no network)")
    except BaseException as exc:  # noqa: BLE001 - SystemExit from the pipeline included
        failures.append(f"the self-check pipeline did not complete: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
    return {"passed": not failures, "checks": checks, "failures": failures}


def run() -> dict:
    return {"code": code_identity(), "selfcheck": canary_check()}
