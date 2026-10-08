"""`filedrawer publish <package>`: put a finished package in the author's own GitHub repository.

The File Drawer lists only self-run, self-hosted packages, so this is the step between a run on your own machine
and `filedrawer submit`. It scans what the repository would make public (identifier columns in data/, identifiers
and contact details in the package's text files), commits the package, creates the GitHub repository with the `gh`
CLI if there is none (private unless --public), and pushes. With --submit it then lists the package on the
File Drawer (only for a public repository).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

GITIGNORE = """# raw exports carry identifiers and free text: never commit them
inputs/*.csv
inputs/*.sav
inputs/*.dta
inputs/*.xlsx
raw_export.csv
.mplconfig/
__pycache__/
.filedrawer/
run.log
results.prev/
"""


def _git(package: Path, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=package, capture_output=True, text=True, check=check)


def scan(package: Path, acknowledge: list[str] | None = None) -> list[str]:
    """What blocks publishing: identifier-like columns in data/, identifiers or unacknowledged contact details in text."""
    from . import pii as PII
    from . import bundle as B
    problems = [f"data/{f.relative_to(package / 'data')}: identifier-like columns {cols}"
                for f in sorted((package / "data").rglob("*.csv")) if (cols := PII.assert_no_pii(f))] if (package / "data").is_dir() else []
    res = B.scan(B.collect(package), acknowledged=set(acknowledge or []))
    problems += [f"{p['path']}: {p['reason']} ({p['count']})" for p in res["problems"]]
    problems += [f"{B.review_key(r)} ({r['count']}): contact details; pass --ack \"{B.review_key(r)}\" if this is a researcher "
                 f"or IRB contact, not respondent data" for r in res["review"] if not r["acknowledged"]]
    return problems


def publish(package: str | Path, repo: str | None = None, public: bool = False, message: str = "Study package",
            acknowledge: list[str] | None = None, yes: bool = False, say=print, ask=input, run=_git) -> tuple[int, str | None]:
    """Returns (exit code, the GitHub URL or None)."""
    package = Path(package).resolve()
    if not (package / "study.json").is_file():
        say(f"{package} is not a study package (no study.json).")
        return 2, None
    problems = scan(package, acknowledge)
    if problems:
        for p in problems:
            say(f"  BLOCK {p}")
        say("Nothing was committed or pushed.")
        return 1, None
    say("Scan: no identifier columns in data/, no identifiers or unacknowledged contact details in the text files.")
    if run(package, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        run(package, "init", "-q", "-b", "main")
        say("Initialized a git repository.")
    gi = package / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    run(package, "add", "-A")
    if run(package, "diff", "--cached", "--quiet", check=False).returncode != 0:
        run(package, "commit", "-q", "-m", message)
        say(f"Committed: {message}")
    origin = run(package, "remote", "get-url", "origin", check=False).stdout.strip()
    if not origin:
        name = repo or (package.name if package.name not in ("", ".") else "study")
        vis = "--public" if public else "--private"
        if public and not yes and ask(f"Create the PUBLIC GitHub repository {name}? Everything committed becomes visible. [y/N] ").strip().lower() != "y":
            say("Nothing was pushed.")
            return 1, None
        r = subprocess.run(["gh", "repo", "create", name, vis, "--source", str(package), "--remote", "origin", "--push"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            say(f"gh repo create failed: {r.stderr.strip()[:400]}\nInstall and log in to the GitHub CLI (`gh auth login`), or add "
                f"a remote yourself (`git remote add origin ...`) and run publish again.")
            return 1, None
        origin = run(package, "remote", "get-url", "origin", check=False).stdout.strip()
        say(f"Created {'public' if public else 'private'} repository {origin} and pushed.")
    else:
        branch = run(package, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        r = run(package, "push", "-q", "-u", "origin", branch, check=False)
        if r.returncode != 0:
            say(f"git push failed: {r.stderr.strip()[:400]}")
            return 1, None
        say(f"Pushed {branch} to {origin}.")
    from .submit import github_url
    url, problem = github_url(package)
    if problem:
        say(problem)
        return 1, None
    if not public:
        say("The repository is private: make it public on GitHub when you are ready, then run `filedrawer submit` to list it.")
    return 0, url
