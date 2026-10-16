"""Submission moderation: a new package is held as pending until the admin approves it.

A pending record is listed nowhere public (index, records, paper, QSF, llms.txt, similar). Approval publishes it. The
global caps hold across all addresses. FD_MODERATION=0 lists at once, as the server did before the queue existed.
"""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import app

ROOT = Path(__file__).resolve().parent.parent
ADMIN = "s3cret"
LOCAL = {"schema_version": 1, "slug": "local-one", "title": "Local study one", "authors": ["A"], "created": "2026-10-01",
         "synthetic": True, "design": {"type": "survey_experiment", "arms": ["c", "t"], "n_raw": 100, "n_analysis": 95},
         "population": {"country": "US", "sample": "online_panel"}, "constructs": ["immigration_attitudes"],
         "keywords": ["immigration"], "hypotheses": [{"id": "H1", "tag": "registered", "supported": True}],
         "release_status": "draft", "provenance": {"mode": "fully_agentic", "reviewer_pass": False}}


def _package(slug, title):
    study = dict(LOCAL, slug=slug, title=title, release_status="released")
    return {"study.json": json.dumps(study), "report.md": f"# {title}\n\n> **Provenance: FULLY AGENTIC**\n\n## Summary\n\nIt worked.\n",
            "extensions/survey.qsf": json.dumps({"SurveyEntry": {}, "SurveyElements": []})}


class FakeGitHub:
    """Public repositories (all on branch main) served to the harvester: {"owner/repo": {repo-relative path: text}}."""

    def __init__(self):
        self.repos = {}

    def add(self, slug, files):
        self.repos[f"someone/{slug}"] = files

    def fetch_json(self, url, timeout=10, max_bytes=2_000_000):
        m = re.fullmatch(r"https://api\.github\.com/repos/([^/]+/[^/?]+)(/git/trees/[^?]+\?recursive=1)?", url)
        if not m or m.group(1) not in self.repos:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if m.group(2):
            return {"tree": [{"path": p, "type": "blob"} for p in self.repos[m.group(1)]]}
        return {"default_branch": "main"}

    def fetch_text(self, url, timeout=10, max_bytes=262144):
        m = re.fullmatch(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/main/([^?]+)(\?.*)?", url)
        rel = urllib.parse.unquote(m.group(2)) if m else ""
        files = self.repos.get(m.group(1), {}) if m else {}
        if rel not in files:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return files[rel]


@pytest.fixture
def srv(tmp_path, monkeypatch):
    saved = dict(app.SETTINGS)
    studies = tmp_path / "studies"
    (studies / "local-one").mkdir(parents=True)
    (studies / "local-one" / "study.json").write_text(json.dumps(LOCAL))
    data_dir = tmp_path / "data"
    app.configure(studies_dir=studies, data_dir=data_dir, registry_path=tmp_path / "registry.json",
                  admin_token=ADMIN, include_drafts=True, moderation=True)
    app.SETTINGS.update(rate_limit=100, daily_cap=20, pending_cap=50)
    gh = FakeGitHub()
    monkeypatch.setattr(app.fdharvest, "fetch_text", gh.fetch_text)
    monkeypatch.setattr(app.fdharvest, "fetch_json", gh.fetch_json)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    httpd = app.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}", data_dir=data_dir, gh=gh)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        app.SETTINGS.clear()
        app.SETTINGS.update(saved)
        with app._STATE["index_lock"]:
            app._STATE["index"] = None
        app._STATE["submissions"].clear()


def _call(base, path, method="GET", data=None, ip=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    if ip:
        headers["X-Forwarded-For"] = ip
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _submit(srv, slug, ip="198.51.100.10", **extra):
    status, body, _ = _call(srv.base, "/submit", "POST", {"repo_url": f"https://github.com/someone/{slug}", **extra}, ip=ip)
    return status, json.loads(body)


def _add(srv, slug, title=None):
    srv.gh.add(slug, _package(slug, title or slug.replace("-", " ").capitalize()))


def _approve(srv, rid):
    return _call(srv.base, f"/admin/approve?token={ADMIN}&id={rid}", "POST", {})[0]


def _listed(srv):
    return {s["slug"] for s in json.loads(_call(srv.base, "/index.json")[1])["studies"]}


def _stored(srv, rid):
    return json.loads((srv.data_dir / "records" / f"{rid}.json").read_text())


def test_pending_submission_is_held_back_everywhere(srv):
    _add(srv, "pending-one", "Pending study one")
    status, resp = _submit(srv, "pending-one")
    assert status == 202
    assert resp["ok"] is True and resp["pending"] is True
    assert resp["message"] == "Received. It will be listed after a quick review."
    assert "record_url" not in resp and "paper_url" not in resp
    rid = resp["id"]
    assert _stored(srv, rid)["status"] == "pending" and _stored(srv, rid)["submitted_at"]
    assert "approved_at" not in _stored(srv, rid)

    assert _listed(srv) == {"local-one"}
    assert rid not in {r["id"] for r in json.loads(_call(srv.base, "/records.json")[1])["records"]}
    assert _call(srv.base, f"/records/{rid}.json")[0] == 404
    assert _call(srv.base, f"/records/{rid}/paper.md")[0] == 404
    assert _call(srv.base, f"/qsf/{rid}/survey.qsf")[0] == 404
    assert "Pending study one" not in _call(srv.base, "/llms.txt")[1].decode()
    assert "pending-one" not in {s["slug"] for s in json.loads(_call(srv.base, "/similar/local-one")[1])["similar"]}

    # the admin token still reads it, so it can be reviewed before it is approved
    status, _, headers = _call(srv.base, f"/records/{rid}.json?token={ADMIN}")
    assert status == 200 and headers["Cache-Control"] == "no-store"          # a held record is never cached
    status, body, headers = _call(srv.base, f"/records/{rid}/paper.md?token={ADMIN}")
    assert status == 200 and body.decode().startswith("# Pending study one") and headers["Cache-Control"] == "no-store"
    assert _call(srv.base, f"/qsf/{rid}/survey.qsf?token={ADMIN}")[0] == 200


def test_approval_publishes_the_paper(srv):
    _add(srv, "approve-one", "Approve study one")
    rid = _submit(srv, "approve-one")[1]["id"]
    assert _call(srv.base, f"/admin/approve?id={rid}", "POST", {})[0] == 403
    assert _call(srv.base, f"/admin/approve?token=wrong&id={rid}", "POST", {})[0] == 403
    assert _call(srv.base, f"/admin/approve?token={ADMIN}&id=000000000000", "POST", {})[0] == 404
    status, body, _ = _call(srv.base, f"/admin/approve?token={ADMIN}&id={rid}", "POST", {})
    assert status == 200 and json.loads(body)["status"] == "approved"
    rec = json.loads(_call(srv.base, f"/records/{rid}.json")[1])
    assert rec["status"] == "approved" and rec["approved_at"]
    assert "approve-one" in _listed(srv)
    assert rid in {r["id"] for r in json.loads(_call(srv.base, "/records.json")[1])["records"]}
    assert _call(srv.base, f"/records/{rid}/paper.md")[0] == 200
    assert "Approve study one" in _call(srv.base, "/llms.txt")[1].decode()
    assert "approve-one" in {s["slug"] for s in json.loads(_call(srv.base, "/similar/local-one")[1])["similar"]}
    status, body, _ = _call(srv.base, f"/admin/approve?token={ADMIN}&id={rid}", "POST", {})
    assert status == 200 and json.loads(body)["already"] is True          # approving twice changes nothing


def test_duplicate_of_a_pending_submission_is_409(srv):
    _add(srv, "dup-one")
    first = _submit(srv, "dup-one")[1]
    status, dup = _submit(srv, "dup-one/")                               # the same repository, with a trailing slash
    assert status == 409
    assert dup["error"] == "already submitted; pending review" and dup["id"] == first["id"] and dup["pending"] is True
    assert "record_url" not in dup
    _approve(srv, first["id"])
    status, dup = _submit(srv, "dup-one")
    assert status == 409 and dup["error"] == "already listed" and dup["record_url"] == f"/records/{first['id']}.json"


def test_dry_run_is_unchanged_and_stores_nothing(srv):
    _add(srv, "dry-one")
    status, resp = _submit(srv, "dry-one", dry_run=True)
    assert status == 200 and resp["dry_run"] is True and resp["message"] == "Checked. Nothing has been stored yet."
    assert [s["step"] for s in resp["audit"]][-1] == "Would store"
    assert list((srv.data_dir / "records").glob("*.json")) == []


def test_daily_cap_is_global_across_addresses(srv):
    app.SETTINGS["daily_cap"] = 2
    for slug in ("cap-a", "cap-b", "cap-c"):
        _add(srv, slug)
    status, first = _submit(srv, "cap-a", ip="198.51.100.1")
    assert status == 202
    assert _submit(srv, "cap-b", ip="198.51.100.2")[0] == 202
    status, resp = _submit(srv, "cap-c", ip="198.51.100.3")              # a third address does not get around it
    assert status == 429 and "at most 2 new submissions in any 24 hours" in resp["error"]
    assert len(list((srv.data_dir / "records").glob("*.json"))) == 2
    _approve(srv, first["id"])                                           # approved submissions count too
    assert _submit(srv, "cap-c", ip="198.51.100.3")[0] == 429


def test_pending_cap_is_global_and_a_review_frees_a_place(srv):
    app.SETTINGS["pending_cap"] = 1
    _add(srv, "queue-a")
    _add(srv, "queue-b")
    status, first = _submit(srv, "queue-a", ip="198.51.100.4")
    assert status == 202
    status, resp = _submit(srv, "queue-b", ip="198.51.100.5")
    assert status == 429 and "waiting for review" in resp["error"]
    assert _approve(srv, first["id"]) == 200
    assert _submit(srv, "queue-b", ip="198.51.100.5")[0] == 202


def test_admin_queue_lists_pending_and_rejecting_uses_remove(srv):
    _add(srv, "queue-one", "Queue one")
    _add(srv, "queue-two", "Queue two")
    p1 = _submit(srv, "queue-one", ip="198.51.100.6")[1]["id"]
    p2 = _submit(srv, "queue-two", ip="198.51.100.7")[1]["id"]
    assert _call(srv.base, "/admin/pending")[0] == 403
    assert _call(srv.base, "/admin/pending?token=wrong")[0] == 403
    status, body, _ = _call(srv.base, f"/admin/pending?token={ADMIN}")
    queue = json.loads(body)
    assert status == 200 and {q["id"] for q in queue} == {p1, p2}
    item = next(q for q in queue if q["id"] == p1)
    assert item["title"] == "Queue one" and item["repo_url"] == "https://github.com/someone/queue-one"
    assert item["submitted_at"] and item["screening"]["ok"] is True and item["screening"]["checks"]
    _approve(srv, p1)
    assert {q["id"] for q in json.loads(_call(srv.base, f"/admin/pending?token={ADMIN}")[1])} == {p2}
    assert _call(srv.base, f"/admin/remove?token={ADMIN}&id={p2}", "POST", {})[0] == 200
    assert json.loads(_call(srv.base, f"/admin/pending?token={ADMIN}")[1]) == []
    assert not (srv.data_dir / "records" / f"{p2}.json").exists()


def test_refresh_never_changes_moderation_state(srv):
    _add(srv, "keep-pending", "Keep pending")
    _add(srv, "keep-approved", "Keep approved")
    pending = _submit(srv, "keep-pending")[1]["id"]
    approved = _submit(srv, "keep-approved")[1]["id"]
    _approve(srv, approved)
    assert _call(srv.base, f"/admin/refresh?token={ADMIN}&id={pending}", "POST", {})[0] == 200
    assert _stored(srv, pending)["status"] == "pending"
    assert _call(srv.base, f"/records/{pending}.json")[0] == 404 and "keep-pending" not in _listed(srv)
    assert _call(srv.base, f"/admin/refresh?token={ADMIN}&id={approved}", "POST", {})[0] == 200
    assert _stored(srv, approved)["status"] == "approved" and _stored(srv, approved)["approved_at"]
    assert "keep-approved" in _listed(srv)


def test_record_from_before_moderation_stays_listed(srv):
    app.SETTINGS["moderation"] = False
    _add(srv, "legacy-one", "Legacy study one")
    status, resp = _submit(srv, "legacy-one")
    assert status == 200 and resp["record_url"] == f"/records/{resp['id']}.json"
    rid = resp["id"]
    assert "status" not in _stored(srv, rid)
    app.SETTINGS["moderation"] = True                                  # the queue is switched on later
    assert "legacy-one" in _listed(srv)
    assert _call(srv.base, f"/records/{rid}.json")[0] == 200
    assert _call(srv.base, f"/records/{rid}/paper.md")[0] == 200
    assert _call(srv.base, f"/admin/refresh?token={ADMIN}&id={rid}", "POST", {})[0] == 200
    assert "status" not in _stored(srv, rid) and "legacy-one" in _listed(srv)


def test_moderation_off_lists_at_once_without_caps(srv):
    app.SETTINGS.update(moderation=False, daily_cap=0, pending_cap=0)   # the caps only apply to the queue
    _add(srv, "open-one", "Open study one")
    status, resp = _submit(srv, "open-one")
    assert status == 200 and "pending" not in resp and resp["record_url"] == f"/records/{resp['id']}.json"
    assert "open-one" in _listed(srv)
    assert _call(srv.base, f"/records/{resp['id']}.json")[0] == 200
    assert _call(srv.base, f"/records/{resp['id']}/paper.md")[0] == 200


def _settings_in_fresh_process(overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FD_")}
    env.update(overrides)
    code = "import json; from server import app; print(json.dumps({k: app.SETTINGS[k] for k in ('moderation', 'daily_cap', 'pending_cap')}))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


def test_moderation_settings_come_from_the_environment():
    assert _settings_in_fresh_process({}) == {"moderation": True, "daily_cap": 20, "pending_cap": 50}
    assert _settings_in_fresh_process({"FD_MODERATION": "0", "FD_DAILY_SUBMISSION_CAP": "7", "FD_PENDING_CAP": "9"}) == \
        {"moderation": False, "daily_cap": 7, "pending_cap": 9}
    assert _settings_in_fresh_process({"FD_MODERATION": "False", "FD_PENDING_CAP": "lots"}) == \
        {"moderation": False, "daily_cap": 20, "pending_cap": 50}
