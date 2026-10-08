"""`filedrawer submit <package> --server URL`: list a self-run, self-hosted study package.

The author runs the pipeline on their own machine and hosts the package in their own public GitHub repository;
the File Drawer only keeps a metadata record that links there. Before anything is sent this command runs the
self-check (code identity and the offline canary test), the leak scan over the package's text files with
acknowledgment of contact details, and a personal-data scan of data/. It then confirms the package is committed
and pushed, and sends only the repository link. The server reads a few small text files from GitHub, screens and
audits the submission, and stores one metadata record.
"""
from __future__ import annotations

import contextlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import bundle as B
from . import selfcheck as SC

STATE = Path(".filedrawer") / "submission.json"


def _post(url: str, body: dict, timeout: float = 120) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {"ok": False, "error": f"HTTP {e.code}"}


def _human(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB" if n >= 1e3 else f"{n} B"


def submit_package(package: str | Path, server: str, *, yes: bool = False, dry_run: bool = False,
                   acknowledge: list[str] | None = None, run_selfcheck: bool = True,
                   say=print, ask=input, post=_post) -> int:
    package = Path(package).resolve()
    server = server.rstrip("/")
    if not (package / "study.json").is_file():
        say(f"{package} is not a study package (no study.json).")
        return 2

    # 1. self-check
    code = SC.code_identity()
    say("Code")
    say(f"  filedrawer {code['tool_version']}" + (f", commit {code['commit']}" if code["commit"] else "")
        + (" with LOCAL CHANGES" if code["modified"] else ""))
    say(f"  source: {code['source_url']}")
    say(f"  content hash of {code['n_source_files']} source files: {code['content_sha256'][:16]}…")
    sc = {"passed": None, "checks": ["skipped by flag"], "failures": []}
    if run_selfcheck:
        say("Self-check (synthetic study with planted canaries, run offline with a mock model)")
        with contextlib.redirect_stdout(io.StringIO()):
            sc = SC.canary_check()
        for c in sc["checks"]:
            say(f"  ok    {c}")
        for f in sc["failures"]:
            say(f"  FAIL  {f}")
        if not sc["passed"]:
            say("The self-check failed; nothing was sent.")
            return 1

    # 2. bundle and scan
    files = B.collect(package)
    state_path = package / STATE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    acks = set(acknowledge or []) | set(state.get("acknowledged") or [])
    result = B.scan(files, acknowledged=acks)
    say(f"Bundle: {result['n_files']} files, {_human(result['bytes'])} (allowlist; data/, inputs/ and the model log are never included)")
    for m in B.manifest(files):
        say(f"  {_human(m['bytes']):>8}  {m['path']}")
    for n in result["notes"]:
        say(f"  note  {n['path']}: {n['reason']} ({n['count']})")
    if result["problems"]:
        for p in result["problems"]:
            say(f"  BLOCK {p['path']}: {p['reason']} ({p['count']})")
        say("The bundle did not pass the scan; nothing was sent.")
        return 1
    for r in result["review"]:
        key = B.review_key(r)
        if r["acknowledged"]:
            say(f"  ack   {key} ({r['count']})")
            continue
        if yes:
            say(f"  REVIEW {key} ({r['count']}): pass --ack \"{key}\" if this is a contact, not respondent data")
            continue
        if ask(f"  REVIEW {key} ({r['count']}). Is this a researcher or IRB contact, not respondent data? [y/N] ").strip().lower() == "y":
            acks.add(key)
    result = B.scan(files, acknowledged=acks)
    if not result["ok"]:
        say("Unacknowledged contact details remain; nothing was sent.")
        return 1

    # 3. personal data in what the repository makes public
    from . import pii as PII
    flagged = {str(f.relative_to(package)): PII.assert_no_pii(f) for f in sorted((package / "data").rglob("*.csv"))}
    flagged = {k: v for k, v in flagged.items() if v}
    if flagged:
        for k, v in flagged.items():
            say(f"  BLOCK {k}: identifier-like columns {v}")
        say("Remove them before publishing the repository; nothing was sent.")
        return 1

    # 4. the package must live, committed and pushed, in a GitHub repository
    url, problem = github_url(package)
    if problem:
        say(problem + " Nothing was sent.")
        return 1
    say(f"Repository: {url}. Only this link is sent; the server reads a few small text files from it and stores a metadata record.")
    status, out = post(server + "/submit", {"repo_url": url, "dry_run": True})
    for st in out.get("audit") or []:
        say(f"  {'ok  ' if st.get('ok') else 'FAIL'}  {st.get('step')}: {st.get('detail')}")
    if not out.get("ok"):
        say(f"The server would not list it (HTTP {status}): {out.get('error')}")
        return 1
    if dry_run:
        say("Dry run: nothing was filed.")
        return 0
    if not yes and ask("File it? [y/N] ").strip().lower() != "y":
        say("Nothing was filed.")
        return 1
    status, out = post(server + "/submit", {"repo_url": url})
    if not out.get("ok"):
        say(f"The server refused it (HTTP {status}): {out.get('error')}")
        return 1
    say(out.get("message", "Filed."))
    say(f"  record: {server}{out['record_url']}")
    return 0


def github_url(package: Path) -> tuple[str | None, str | None]:
    """The GitHub URL of the package folder at its pushed branch, or why there is none."""
    import subprocess

    def git(*args):
        r = subprocess.run(["git", *args], cwd=package, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if not top:
        return None, f"{package} is not in a git repository: push the package to GitHub first."
    remote = git("remote", "get-url", "origin") or ""
    m = __import__("re").match(r"(?:https://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?$", remote)
    if not m:
        return None, f"origin ({remote or 'none'}) is not a GitHub repository."
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if git("status", "--porcelain", "--", "."):
        return None, "The package has uncommitted changes: commit and push them first."
    if git("rev-parse", "HEAD") != git("rev-parse", f"origin/{branch}"):
        return None, f"Branch {branch} is not pushed to origin: push it first."
    rel = Path(package).resolve().relative_to(Path(top).resolve()).as_posix()
    base = f"https://github.com/{m.group(1)}/{m.group(2)}"
    return (base if rel == "." and branch in ("main", "master") else f"{base}/tree/{branch}" + ("" if rel == "." else f"/{rel}")), None
