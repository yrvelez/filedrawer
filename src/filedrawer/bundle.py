"""Submission bundles: what may leave the author's machine when a package is filed, and the scan it must pass.

A bundle is a dict {relative path: bytes} built from a study package by an allowlist. The same
allowlist, caps and scan run twice: on the author's machine before anything is sent (`filedrawer
submit <package>`), and again on the server before anything is stored. Respondent-level data
(`data/`, `inputs/`, any raw export), the LLM request log and everything not on the allowlist are
never included.

The scan looks for identifier-shaped strings (MTurk worker ids, Qualtrics response ids, IP
addresses, phone and social security numbers, email addresses) in every text file, and for result
tables shaped like respondent-level data (an identifier or free-text column, or about as many rows
as respondents). Findings carry a path, a reason and a count, never the matched text.

Two tiers. Problems (worker or response ids, IP addresses, SSNs, row-level tables, files off the
allowlist) always block. Review items (phone numbers and email addresses, which are often an
investigator's or IRB's contact in consent text) block until the author acknowledges each one by its
key, "<path>: <reason>"; the record lists what was acknowledged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath

from . import pii

# path pattern -> allowed suffixes
ALLOW_EXACT = {"report.md", "study.json", "pap.json", "pap.md", "codebook.md", "codebook.json", "RUN.md", "README.md",
               "CITATION.cff", "review.json", "review.md", "responses.md", "provenance/provenance.json"}
ALLOW_DIRS = {
    "figures": (".png", ".svg", ".txt"),
    "extensions": (".json", ".md", ".png", ".svg", ".txt"),
    "results": (".csv",),
    "scripts": (".py", ".r", ".R"),
}
NEVER = ("data/", "inputs/", "raw/", "provenance/llm_log", ".git/", ".filedrawer/")
TEXT_SUFFIXES = (".md", ".json", ".csv", ".py", ".r", ".R", ".txt", ".svg", ".cff")

MAX_FILE_BYTES = 3_000_000
MAX_TOTAL_BYTES = 25_000_000
MAX_FILES = 400

ID_PATTERNS = {
    "mturk_worker_id": re.compile(r"(?<![A-Za-z0-9])A[A-Z0-9]{11,14}(?![A-Za-z0-9])"),   # kept only with 2+ digits
    "qualtrics_response_id": re.compile(r"(?<![A-Za-z0-9])R_[A-Za-z0-9]{15}(?![A-Za-z0-9])"),
    "ip_address": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "phone_number": re.compile(r"(?<![\w./-])\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?![\w-])"),
    "ssn": re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
}
# Institutional contacts that appear in consent text and codebooks; not respondent data.
EMAIL_OK = re.compile(r"^(askirb|irb|irboffice|hrpp|humansubjects|research|support|help|info|noreply|no-reply)@", re.I)
REVIEW_CODES = {"phone_number", "email"}
ROW_LEVEL_SHARE = 0.5      # a results table with >= this share of n_analysis rows looks like respondent-level data
ROW_LEVEL_MIN = 50


def is_allowed(rel: str) -> bool:
    p = PurePosixPath(rel)
    if rel != p.as_posix() or p.is_absolute() or ".." in p.parts or not rel or rel.startswith(NEVER):
        return False
    if rel in ALLOW_EXACT:
        return True
    top = p.parts[0] if len(p.parts) > 1 else None
    return top in ALLOW_DIRS and rel.endswith(ALLOW_DIRS[top]) and all(re.fullmatch(r"[A-Za-z0-9_.-]+", s) for s in p.parts)


def collect(package_dir: str | Path) -> dict[str, bytes]:
    """The allowlisted files of a package, as {relative path: bytes}."""
    root = Path(package_dir).resolve()
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file() and not f.is_symlink():
            rel = f.relative_to(root).as_posix()
            if is_allowed(rel):
                out[rel] = f.read_bytes()
    return out


def manifest(files: dict[str, bytes]) -> list[dict]:
    return [{"path": rel, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for rel, b in sorted(files.items())]


def manifest_sha256(files: dict[str, bytes]) -> str:
    return hashlib.sha256(json.dumps(manifest(files), sort_keys=True).encode()).hexdigest()


def _n_analysis(files: dict[str, bytes]) -> int | None:
    try:
        sj = json.loads(files.get("study.json", b"{}").decode("utf-8"))
        n = (sj.get("design") or {}).get("n_analysis")
        return int(n) if n else None
    except (ValueError, TypeError, AttributeError):
        return None


def _scan_table(rel: str, text: str, n_analysis: int | None) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    problems = []
    known = {c.lower() for c in pii.KNOWN_ID_COLS} - {c.lower() for c in pii.SAFE_SYSTEM_COLS}
    for i, col in enumerate(header):
        reason = "identifier column" if col.lower() in known else None
        if reason is None:
            for code, rx in pii.HEADER_PATTERNS.items():
                if code in ("id", "email", "phone", "ip", "ssn", "dob") and rx.search(col):
                    reason = f"identifier-like column ({code})"
                    break
        if reason is None and body:
            vals = [r[i] for r in body[:200] if i < len(r) and r[i].strip()]
            if len(vals) >= 10 and sum(pii.looks_like_prose(v) for v in vals) / len(vals) > 0.5:
                reason = "free-text column"
        if reason:
            problems.append({"path": rel, "reason": f"{reason}: {col}", "count": 1})
    if n_analysis and n_analysis >= ROW_LEVEL_MIN and len(body) >= ROW_LEVEL_SHARE * n_analysis:
        problems.append({"path": rel, "reason": f"{len(body)} rows for {n_analysis} respondents: looks like respondent-level data", "count": len(body)})
    return problems


def review_key(item: dict) -> str:
    return f"{item['path']}: {item['reason']}"


def scan(files: dict[str, bytes], acknowledged: list[str] | set[str] | None = None) -> dict:
    """{"ok", "problems", "review", "notes"} for a bundle. ok is False while any problem or any
    unacknowledged review item remains; notes never block."""
    problems, review, notes = [], [], []
    ack = set(acknowledged or [])
    if len(files) > MAX_FILES:
        problems.append({"path": "*", "reason": f"{len(files)} files (limit {MAX_FILES})", "count": len(files)})
    total = sum(len(b) for b in files.values())
    if total > MAX_TOTAL_BYTES:
        problems.append({"path": "*", "reason": f"{total} bytes (limit {MAX_TOTAL_BYTES})", "count": total})
    for rel in ("study.json", "report.md"):
        if rel not in files:
            problems.append({"path": rel, "reason": "missing: a package needs study.json and report.md", "count": 0})
    n_analysis = _n_analysis(files)
    for rel, data in sorted(files.items()):
        if not is_allowed(rel):
            problems.append({"path": rel, "reason": "not on the allowlist", "count": 1})
            continue
        if len(data) > MAX_FILE_BYTES:
            problems.append({"path": rel, "reason": f"{len(data)} bytes (limit {MAX_FILE_BYTES})", "count": len(data)})
            continue
        if not rel.endswith(TEXT_SUFFIXES) or rel.endswith(".png"):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append({"path": rel, "reason": "not valid UTF-8 text", "count": 1})
            continue
        for code, rx in ID_PATTERNS.items():
            hits = rx.findall(text)
            if code == "mturk_worker_id":
                hits = [h for h in hits if sum(ch.isdigit() for ch in h) >= 2]
            if code == "email":
                ok = [h for h in hits if EMAIL_OK.match(h)]
                hits = [h for h in hits if not EMAIL_OK.match(h)]
                if ok:
                    notes.append({"path": rel, "reason": "institutional contact email (allowed)", "count": len(ok)})
            if hits:
                item = {"path": rel, "reason": code.replace("_", " "), "count": len(hits)}
                if code in REVIEW_CODES:
                    item["acknowledged"] = review_key(item) in ack
                    review.append(item)
                else:
                    problems.append(item)
        if rel.endswith(".csv"):
            problems += _scan_table(rel, text, n_analysis)
    ok = not problems and all(r["acknowledged"] for r in review)
    return {"ok": ok, "problems": problems, "review": review, "notes": notes, "n_files": len(files), "bytes": total}
