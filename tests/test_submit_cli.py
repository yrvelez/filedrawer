"""filedrawer submit <package>: self-check, scans, a pushed GitHub repository, then only the link is sent."""
import json
import shutil
import subprocess

import pytest

from filedrawer import selfcheck as SC
from filedrawer import submit as S

STUDY = {"slug": "cli-one", "title": "CLI study one", "authors": ["A. Author"], "created": "2026-10-05",
         "design": {"type": "survey_experiment", "n_raw": 300, "n_analysis": 290},
         "population": {"country": "US", "sample": "online_panel"}, "release_status": "released",
         "provenance": {"mode": "fully_agentic", "reviewer_pass": False}}
URL = "https://github.com/someone/cli-one"
OK_AUDIT = [{"step": "Read small text files only", "ok": True, "detail": "3 file(s)"}]


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "pkg"
    for rel, text in {"study.json": json.dumps(STUDY), "report.md": "# CLI study one\n\n## Abstract\n\nShort.\n",
                      "results/H1.csv": "analysis_id,estimate\nH1,0.5\n", "data/clean.csv": "row,treat,y\n1,1,0.3\n2,0,0.1\n",
                      "provenance/llm_log.jsonl": "{}\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    monkeypatch.setattr(S, "github_url", lambda p: (URL, None))
    return root


class Server:
    """Records what the client sends; answers like /submit (a dry run first, then the filing)."""

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, url, body):
        self.sent.append((url, body))
        if not self.ok:
            return 422, {"ok": False, "error": "not a research project", "audit": OK_AUDIT}
        if body.get("dry_run"):
            return 200, {"ok": True, "dry_run": True, "audit": OK_AUDIT}
        return 200, {"ok": True, "message": "Listed 'CLI study one'", "record_url": "/records/abc123def456.json", "audit": OK_AUDIT}


def _run(package, post, **kw):
    out = []
    kw.setdefault("run_selfcheck", False)
    kw.setdefault("ask", lambda q: "y")
    code = S.submit_package(package, "https://filedrawer.org", say=out.append, post=post, **kw)
    return code, "\n".join(out)


def test_sends_only_the_repository_link(package):
    srv = Server()
    code, out = _run(package, srv)
    assert code == 0 and "Listed 'CLI study one'" in out
    assert [b for _, b in srv.sent] == [{"repo_url": URL, "dry_run": True}, {"repo_url": URL}]
    assert all(u == "https://filedrawer.org/submit" for u, _ in srv.sent)
    assert not (package / ".filedrawer").exists()


def test_blocks_leaks_before_sending(package):
    (package / "report.md").write_text("# CLI study one\n\nworker A1B2C3D4E5F6G7\n")
    srv = Server()
    code, out = _run(package, srv)
    assert code == 1 and "BLOCK report.md: mturk worker id" in out and not srv.sent and "A1B2C3D4E5F6G7" not in out


def test_blocks_identifier_columns_in_data(package):
    (package / "data" / "clean.csv").write_text("email,y\na@b.org,1\nc@d.org,0\n")
    srv = Server()
    code, out = _run(package, srv)
    assert code == 1 and "BLOCK data/clean.csv" in out and not srv.sent


def test_contacts_must_be_acknowledged(package):
    (package / "codebook.md").write_text("Questions: call the IRB at 212-555-0147.\n")
    srv = Server()
    assert _run(package, srv, ask=lambda q: "n")[0] == 1 and not srv.sent
    code, out = _run(package, srv, yes=True)
    assert code == 1 and '--ack "codebook.md: phone number"' in out
    assert _run(package, srv, yes=True, acknowledge=["codebook.md: phone number"])[0] == 0


def test_dry_run_and_declining_file_nothing(package):
    srv = Server()
    assert _run(package, srv, dry_run=True)[0] == 0
    assert _run(package, srv, ask=lambda q: "n")[0] == 1
    assert [b for _, b in srv.sent] == [{"repo_url": URL, "dry_run": True}] * 2


def test_server_refusal_is_reported(package):
    code, out = _run(package, Server(ok=False))
    assert code == 1 and "would not list it" in out and "not a research project" in out


def test_unpushed_package_sends_nothing(package, monkeypatch):
    monkeypatch.setattr(S, "github_url", lambda p: (None, "Branch main is not pushed to origin: push it first."))
    srv = Server()
    code, out = _run(package, srv)
    assert code == 1 and "not pushed" in out and not srv.sent


def test_not_a_package(tmp_path):
    assert _run(tmp_path, Server())[0] == 2


def test_selfcheck_passes_and_failure_blocks(package, monkeypatch):
    res = SC.canary_check()
    assert res["passed"], res["failures"]
    assert any("model request" in c for c in res["checks"])
    monkeypatch.setattr(SC, "canary_check", lambda: {"passed": False, "checks": [], "failures": ["canary in model log"]})
    srv = Server()
    code, out = _run(package, srv, run_selfcheck=True)
    assert code == 1 and "FAIL  canary in model log" in out and not srv.sent


def test_code_identity_names_the_source():
    c = SC.code_identity()
    assert c["tool_version"] and len(c["content_sha256"]) == 64 and c["source_url"].startswith(SC.SOURCE_REPO)


def test_github_url_from_a_pushed_checkout(tmp_path):
    """The link is derived from origin and the pushed branch; uncommitted or unpushed work is refused."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "study-repo"
    (repo / "studies" / "one").mkdir(parents=True)
    (repo / "studies" / "one" / "study.json").write_text("{}")
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("-c", "user.email=a@b.c", "-c", "user.name=A", "add", ".")
    git("-c", "user.email=a@b.c", "-c", "user.name=A", "commit", "-qm", "x")
    git("remote", "add", "origin", "https://github.com/someone/study-repo.git")
    url, problem = S.github_url(repo / "studies" / "one")
    assert url is None and "not pushed" in problem
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert S.github_url(repo / "studies" / "one") == ("https://github.com/someone/study-repo/tree/main/studies/one", None)
    assert S.github_url(repo) == ("https://github.com/someone/study-repo", None)
    (repo / "studies" / "one" / "study.json").write_text('{"changed": 1}')
    assert "uncommitted" in S.github_url(repo / "studies" / "one")[1]
