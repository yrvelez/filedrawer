"""GitHub reads behind the paper and survey routes are cached and limited per address; a refresh or removal shows at once;
the admin token works as a header and never reaches the request log. Runs against a fake GitHub (no network)."""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from types import SimpleNamespace

import pytest

from server import app

ADMIN = "s3cret"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
LOCAL = {"schema_version": 1, "slug": "local-one", "title": "Local study one", "authors": ["A"], "created": "2026-10-01",
         "synthetic": True, "design": {"type": "survey_experiment", "arms": ["c", "t"], "n_raw": 100, "n_analysis": 95},
         "population": {"country": "US", "sample": "online_panel"}, "constructs": ["immigration_attitudes"],
         "keywords": ["immigration"], "hypotheses": [{"id": "H1", "tag": "registered", "supported": True}],
         "release_status": "draft", "provenance": {"mode": "fully_agentic", "reviewer_pass": False}}


def _package(slug, title, version=1, survey="Survey"):
    study = dict(LOCAL, slug=slug, title=title, release_status="released")
    return {"study.json": json.dumps(study),
            "report.md": f"# {title}\n\n> **Provenance: FULLY AGENTIC**\n\n## Summary\n\nVersion {version}.\n",
            "extensions/survey.qsf": _qsf(survey)}


def _qsf(name):
    return json.dumps({"SurveyEntry": {"SurveyName": name}, "SurveyElements": []})


class FakeGitHub:
    """Public repositories on branch main: {"owner/repo": {path: text}}. Counts fetches by bare URL (no query string)."""

    def __init__(self):
        self.repos, self.calls, self.down = {}, Counter(), set()

    def add(self, slug, files):
        self.repos[f"someone/{slug}"] = files

    def url(self, slug, rel):
        return f"https://raw.githubusercontent.com/someone/{slug}/main/{rel}"

    def fetch_json(self, url, timeout=10, max_bytes=2_000_000):
        m = re.fullmatch(r"https://api\.github\.com/repos/([^/]+/[^/?]+)(/git/trees/[^?]+\?recursive=1)?", url)
        if not m or m.group(1) not in self.repos:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if m.group(2):
            return {"tree": [{"path": p, "type": "blob"} for p in self.repos[m.group(1)]]}
        return {"default_branch": "main"}

    def fetch_text(self, url, timeout=10, max_bytes=262144):
        self.calls[url.split("?", 1)[0]] += 1
        m = re.fullmatch(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/main/([^?]+)(\?.*)?", url)
        rel = urllib.parse.unquote(m.group(2)) if m else ""
        files = self.repos.get(m.group(1), {}) if m else {}
        if url.split("?", 1)[0] in self.down or rel not in files:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return files[rel]


@pytest.fixture
def clean():
    """Settings and cache state put back after a unit test that changes them."""
    saved = dict(app.SETTINGS)
    yield
    app.SETTINGS.clear()
    app.SETTINGS.update(saved)
    app.GH_CACHE.clear()
    with app._STATE["fetch_lock"]:
        app._STATE["fetch_hits"].clear()


@pytest.fixture
def srv(tmp_path, monkeypatch, clean):
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


def _call(base, path, method="GET", data=None, headers=None):
    body = json.dumps(data).encode() if data is not None else None
    hdrs = dict(headers or {})
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _submit(srv, slug):
    """Submit a package from the fake GitHub; it is held for review. Returns the record id."""
    srv.gh.add(slug, _package(slug, slug.replace("-", " ").capitalize()))
    status, body, _ = _call(srv.base, "/submit", "POST", {"repo_url": f"https://github.com/someone/{slug}"})
    assert status == 202, body
    return json.loads(body)["id"]


def _publish(srv, slug):
    """Submit and approve a package. The fetch counts are cleared, so only the reads that follow are counted."""
    rid = _submit(srv, slug)
    assert _call(srv.base, f"/admin/approve?id={rid}", "POST", headers=AUTH)[0] == 200
    srv.gh.calls.clear()
    return rid


def _paper(srv, rid, headers=None):
    return _call(srv.base, f"/records/{rid}/paper.md", headers=headers)


def _survey(srv, rid, headers=None):
    return _call(srv.base, f"/qsf/{rid}/survey.qsf", headers=headers)


# ---------------------------------------------------------------- the cache


def test_second_paper_read_within_the_ttl_skips_github(srv):
    rid = _publish(srv, "cache-one")
    for _ in range(3):
        status, body, _ = _paper(srv, rid)
        assert status == 200 and body.decode().startswith("# Cache one")
    assert srv.gh.calls[srv.gh.url("cache-one", "report.md")] == 1


def test_survey_file_is_cached_too(srv):
    rid = _publish(srv, "cache-qsf")
    for _ in range(2):
        status, body, headers = _survey(srv, rid)
        assert status == 200 and "attachment" in headers["Content-Disposition"] and json.loads(body)["SurveyEntry"]
    assert srv.gh.calls[srv.gh.url("cache-qsf", "extensions/survey.qsf")] == 1


def test_refresh_drops_the_cached_paper_and_survey(srv):
    rid = _publish(srv, "refresh-one")
    assert b"Version 1" in _paper(srv, rid)[1] and b"Survey" in _survey(srv, rid)[1]
    srv.gh.repos["someone/refresh-one"]["report.md"] = "# Refresh one\n\nVersion 2.\n"
    srv.gh.repos["someone/refresh-one"]["extensions/survey.qsf"] = _qsf("Survey v2")
    assert b"Version 1" in _paper(srv, rid)[1]                     # still the cached copy, before the refresh
    assert _call(srv.base, f"/admin/refresh?id={rid}", "POST", headers=AUTH)[0] == 200
    assert b"Version 2" in _paper(srv, rid)[1]                     # shown at once
    assert b"Survey v2" in _survey(srv, rid)[1]


def test_removal_drops_the_cache_and_a_new_submission_reads_fresh(srv):
    rid = _publish(srv, "gone-one")
    assert b"Version 1" in _paper(srv, rid)[1]
    assert _call(srv.base, f"/admin/remove?id={rid}", "POST", headers=AUTH)[0] == 200
    assert _paper(srv, rid)[0] == 404
    srv.gh.repos["someone/gone-one"]["report.md"] = "# Gone one\n\nVersion 2.\n"
    status, body, _ = _call(srv.base, "/submit", "POST", {"repo_url": "https://github.com/someone/gone-one"})
    assert status == 202 and json.loads(body)["id"] == rid        # the same record id, so the same cache key
    assert _call(srv.base, f"/admin/approve?id={rid}", "POST", headers=AUTH)[0] == 200
    assert b"Version 2" in _paper(srv, rid)[1]


def test_a_failed_read_is_remembered_for_the_failure_ttl(srv):
    rid = _publish(srv, "dead-one")
    url = srv.gh.url("dead-one", "report.md")
    srv.gh.down.add(url)
    for _ in range(3):
        assert _paper(srv, rid)[0] == 404
    assert srv.gh.calls[url] == 1


def test_a_failed_read_is_retried_when_the_failure_ttl_is_zero(srv):
    app.SETTINGS["gh_cache_fail_ttl_s"] = 0
    rid = _publish(srv, "dead-two")
    url = srv.gh.url("dead-two", "report.md")
    srv.gh.down.add(url)
    for _ in range(3):
        assert _paper(srv, rid)[0] == 404
    assert srv.gh.calls[url] == 3


# ---------------------------------------------------------------- the per-address limit


def test_per_address_limit_answers_429_with_retry_after(srv):
    app.SETTINGS["fetch_rate_limit"] = 3
    rid = _publish(srv, "limit-one")
    me = {"X-Forwarded-For": "198.51.100.77"}
    for _ in range(3):
        assert _paper(srv, rid, me)[0] == 200
    status, body, headers = _paper(srv, rid, me)
    assert status == 429 and 0 < int(headers["Retry-After"]) <= app.SETTINGS["fetch_rate_window_s"]
    assert json.loads(body)["ok"] is False
    assert _survey(srv, rid, me)[0] == 429                          # the survey route shares the address's allowance
    assert _paper(srv, rid, {"X-Forwarded-For": "198.51.100.78"})[0] == 200   # another address is not held back
    assert _call(srv.base, "/index.json", headers=me)[0] == 200              # nor are the other endpoints
    assert _call(srv.base, f"/records/{rid}.json", headers=me)[0] == 200


def test_admin_requests_are_exempt_from_the_limit(srv):
    app.SETTINGS["fetch_rate_limit"] = 1
    rid = _publish(srv, "limit-admin")
    me = {"X-Forwarded-For": "198.51.100.79"}
    assert _paper(srv, rid, me)[0] == 200
    assert _paper(srv, rid, me)[0] == 429
    assert _paper(srv, rid, dict(me, **AUTH))[0] == 200
    assert _survey(srv, rid, dict(me, **AUTH))[0] == 200
    assert _call(srv.base, f"/records/{rid}/paper.md?token={ADMIN}", headers=me)[0] == 200


def test_the_window_frees_a_place_after_its_span(clean):
    app.SETTINGS.update(fetch_rate_limit=2, fetch_rate_window_s=100)
    ip = "10.0.0.1"
    assert app._fetch_wait(ip, now=1000) == 0
    assert app._fetch_wait(ip, now=1001) == 0
    assert app._fetch_wait(ip, now=1002) == 98                  # the first request leaves the window at 1100
    assert app._fetch_wait(ip, now=1099) == 1
    assert app._fetch_wait(ip, now=1100) == 0


def test_the_address_table_and_each_history_are_bounded(clean):
    app.SETTINGS.update(fetch_rate_limit=3, fetch_rate_window_s=1000, fetch_rate_max_ips=4)
    for i in range(20):
        app._fetch_wait(f"10.0.0.{i}", now=1000 + i)
    assert list(app._STATE["fetch_hits"]) == ["10.0.0.16", "10.0.0.17", "10.0.0.18", "10.0.0.19"]
    for _ in range(10):
        app._fetch_wait("10.9.9.9", now=2000)
    assert len(app._STATE["fetch_hits"]["10.9.9.9"]) == 3


# ---------------------------------------------------------------- the cache's own rules


def test_cache_evicts_least_recently_used_by_count_and_by_bytes(clean):
    app.SETTINGS.update(gh_cache_max_entries=2, gh_cache_max_bytes=10_000)
    c = app._FetchCache()
    g = c.generation()
    c.put("a", "1", 60, g)
    c.put("b", "2", 60, g)
    assert c.get("a") == (True, "1")                             # a is now the most recently used
    c.put("c", "3", 60, g)
    assert c.get("b") == (False, None) and c.get("a") == (True, "1") and c.get("c") == (True, "3")

    app.SETTINGS.update(gh_cache_max_entries=100, gh_cache_max_bytes=10)
    c = app._FetchCache()
    g = c.generation()
    c.put("x", "123456", 60, g)
    c.put("y", "123456", 60, g)                                  # 12 bytes against a 10-byte cap: x goes
    assert c.get("x") == (False, None) and c.get("y") == (True, "123456")
    c.put("big", "0" * 11, 60, g)                                # larger than the whole cache: not stored
    assert c.get("big") == (False, None) and c.get("y") == (True, "123456")


def test_cache_entries_expire_after_their_ttl(clean):
    c = app._FetchCache()
    c.put("u", "t", 10, c.generation(), now=100)
    assert c.get("u", now=109) == (True, "t")
    assert c.get("u", now=110) == (False, None)


def test_drop_matches_prefixes_and_refuses_reads_that_began_before_it(clean):
    raw = "https://raw.githubusercontent.com/"
    c = app._FetchCache()
    g = c.generation()
    c.put(f"{raw}o/a/main/x", "A", 60, g)
    c.put(f"{raw}o/b/main/x", "B", 60, g)
    c.drop([f"{raw}O/A/"])                                       # case-insensitive
    assert c.get(f"{raw}o/a/main/x") == (False, None) and c.get(f"{raw}o/b/main/x") == (True, "B")
    c.put(f"{raw}o/b/main/y", "old", 60, g)                      # began before the drop: not stored after it
    assert c.get(f"{raw}o/b/main/y") == (False, None)


def test_cache_stays_consistent_under_concurrent_use(clean):
    app.SETTINGS.update(gh_cache_max_entries=16, gh_cache_max_bytes=4096)
    c = app._FetchCache()
    errors = []

    def worker(n):
        try:
            for i in range(400):
                c.put(f"u{(n * 31 + i) % 40}", "x" * (i % 50), 60, c.generation())
                c.get(f"u{i % 40}")
                if i % 97 == 0:
                    c.drop(["u1"])
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(c._items) <= 16 and c._size <= 4096
    assert c._size == sum(size for _, _, size in c._items.values())


# ---------------------------------------------------------------- the admin token


def test_header_token_works_on_get_and_post_admin_routes(srv):
    rid = _submit(srv, "hdr-one")
    assert _call(srv.base, "/admin/pending", headers=AUTH)[0] == 200
    queue = json.loads(_call(srv.base, "/admin/pending", headers=AUTH)[1])
    assert rid in {q["id"] for q in queue}
    assert _call(srv.base, "/admin/pending", headers={"Authorization": f"bearer {ADMIN}"})[0] == 200   # scheme is case-blind
    assert _call(srv.base, "/admin/pending", headers={"X-Admin-Token": ADMIN})[0] == 200
    assert _call(srv.base, "/admin/records", headers=AUTH)[0] == 200
    status, body, headers = _call(srv.base, f"/records/{rid}.json", headers=AUTH)     # a held record, as the admin sees it
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert _paper(srv, rid, AUTH)[0] == 200
    assert _call(srv.base, f"/admin/approve?id={rid}", "POST", headers=AUTH)[0] == 200
    assert _call(srv.base, f"/admin/refresh?id={rid}", "POST", headers={"X-Admin-Token": ADMIN})[0] == 200
    other = _publish(srv, "hdr-two")
    assert _call(srv.base, f"/admin/remove?id={other}", "POST", headers=AUTH)[0] == 200


def test_wrong_or_missing_tokens_are_refused(srv):
    rid = _submit(srv, "deny-one")
    bad = ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {ADMIN}x"}, {"Authorization": f"Basic {ADMIN}"},
           {"Authorization": "Bearer "}, {"X-Admin-Token": "wrong"})
    for headers in bad:
        assert _call(srv.base, "/admin/pending", headers=headers)[0] == 403, headers
        assert _call(srv.base, f"/admin/approve?id={rid}", "POST", headers=headers)[0] == 403, headers
        assert _call(srv.base, f"/records/{rid}.json", headers=headers)[0] == 404, headers   # held: not for the public
    assert _call(srv.base, "/admin/pending?token=wrong")[0] == 403
    assert _call(srv.base, f"/admin/approve?token=wrong&id={rid}", "POST")[0] == 403
    assert json.loads(_call(srv.base, f"/records/{rid}.json", headers=AUTH)[1])["status"] == "pending"


def test_query_token_is_still_accepted_for_existing_scripts(srv):
    rid = _submit(srv, "legacy-token")
    assert _call(srv.base, f"/admin/pending?token={ADMIN}")[0] == 200
    assert _call(srv.base, f"/admin/approve?token={ADMIN}&id={rid}", "POST")[0] == 200


def test_empty_admin_token_disables_admin(srv):
    rid = _submit(srv, "off-one")
    app.SETTINGS["admin_token"] = ""
    for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer x"}, {"X-Admin-Token": ""}):
        assert _call(srv.base, "/admin/pending", headers=headers)[0] == 403, headers
        assert _call(srv.base, f"/admin/approve?id={rid}", "POST", headers=headers)[0] == 403, headers
    assert _call(srv.base, "/admin/pending?token=")[0] == 403
    assert _call(srv.base, f"/admin/approve?token=&id={rid}", "POST")[0] == 403
    assert _call(srv.base, f"/records/{rid}.json", headers={"Authorization": "Bearer "})[0] == 404


def test_logged_request_line_never_contains_the_token(srv, capsys):
    rid = _submit(srv, "log-one")
    capsys.readouterr()
    _call(srv.base, f"/admin/pending?token={ADMIN}")
    _call(srv.base, f"/admin/approve?token={ADMIN}&id={rid}", "POST")
    _call(srv.base, "/admin/pending", headers=AUTH)
    _call(srv.base, f"/records/{rid}.json?id=1&token={ADMIN}")
    _call(srv.base, "/admin/pending?token=wrong-guess")
    out = capsys.readouterr().out
    assert ADMIN not in out and "wrong-guess" not in out
    assert "GET /admin/pending?token=REDACTED " in out
    assert "POST /admin/approve?token=REDACTED&id=" in out
    assert f"GET /records/{rid}.json?id=1&token=REDACTED " in out
    assert "GET /admin/pending " in out                           # the header request is logged, without its secret


@pytest.mark.parametrize("line, redacted", [
    ("GET /admin/pending?token=s3cret HTTP/1.1", "GET /admin/pending?token=REDACTED HTTP/1.1"),
    ("POST /admin/approve?id=abc&token=s3cret", "POST /admin/approve?id=abc&token=REDACTED"),
    ("GET /x?%74oken=s3cret&a=1", "GET /x?%74oken=REDACTED&a=1"),
    ("GET /x?tokenizer=keep&id=1", "GET /x?tokenizer=keep&id=1"),
    ('"GET /x?token=s3cret HTTP/1.1" 200 -', '"GET /x?token=REDACTED HTTP/1.1" 200 -'),
    ("Bad request syntax ('GET /x?token=s3cret HTTP/1.1 extra')", "Bad request syntax ('GET /x?token=REDACTED HTTP/1.1 extra')"),
])
def test_redact_token_covers_each_form(line, redacted):
    assert app.redact_token(line) == redacted
