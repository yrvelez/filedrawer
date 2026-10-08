import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from filedrawer import index as fdindex
from server import app


STUDY = {
    "schema_version": 1, "slug": "local-one", "title": "Local study one", "authors": ["A"],
    "created": "2026-10-01", "synthetic": True,
    "design": {"type": "survey_experiment", "arms": ["c", "t"], "n_raw": 100, "n_analysis": 95},
    "population": {"country": "US", "sample": "online_panel"},
    "constructs": ["immigration_attitudes"], "keywords": ["immigration"],
    "hypotheses": [{"id": "H1", "tag": "registered", "supported": True}],
    "release_status": "draft", "provenance": {"mode": "fully_agentic", "reviewer_pass": False},
}


def _req(base, path, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("srv")
    studies = tmp / "studies"
    (studies / "local-one").mkdir(parents=True)
    (studies / "local-one" / "study.json").write_text(json.dumps(STUDY))
    data_dir = tmp / "data"
    app.configure(studies_dir=studies, data_dir=data_dir, registry_path=tmp / "registry.json",
                  admin_token="s3cret", include_drafts=True)
    app.SETTINGS["rate_limit"] = 100          # tests share one client IP
    srv = app.make_server("127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield {"base": base, "data_dir": data_dir}
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def test_root_html(server):
    status, body, headers = _req(server["base"], "/")
    assert status == 200 and "text/html" in headers["Content-Type"]
    assert "File Drawer" in body.decode()


def test_healthz(server):
    status, body, _ = _req(server["base"], "/healthz")
    assert status == 200 and body == b"ok"


def test_index_json_lists_local_study(server):
    status, body, headers = _req(server["base"], "/index.json")
    assert status == 200 and headers["Cache-Control"] == "max-age=60"
    assert headers["Access-Control-Allow-Origin"] == "*"          # personal sites can show the live drawer
    idx = json.loads(body)
    assert [s["slug"] for s in idx["studies"]] == ["local-one"]
    assert idx["studies"][0]["source"] == "local"


def test_submit_endpoint_detectable(server):
    status, _, _ = _req(server["base"], "/submit", method="OPTIONS")
    assert status == 204
    status, _, _ = _req(server["base"], "/submit", method="HEAD")
    assert status == 204


def test_submit_rejects_non_github(server):
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://gitlab.com/a/b"})
    assert status == 400 and json.loads(body)["ok"] is False
    status, body, _ = _req(server["base"], "/submit", "POST", {"nope": 1})
    assert status == 400


ARCHIVE_FILES = {
    "README.md": "# Cues replication\n\nStata and R code and YouGov survey data for a paper on party cues and redistribution.\n",
    "data/survey.dta": "<binary>", "code/analysis.do": "use data/survey.dta", "code/figs.R": "x <- 1",
}
PACKAGE_FILES = {
    "study.json": json.dumps(dict(STUDY, slug="remote-two", title="Remote study two", release_status="released",
                                  constructs=["immigration_attitudes", "political_trust"])),
    "report.md": "# Remote study two\n\n> **Provenance: FULLY AGENTIC** x\n\n## Summary\n\nIt worked.\n\n## Design\n", "data/clean.csv": "a,b\n1,2\n",
    "scripts/02_clean.py": "print(1)",
}


class FakeGitHub:
    def __init__(self, files, branch="main", prefix=""):
        self.files, self.branch, self.prefix, self.fetched = files, branch, prefix, []

    def fetch_text(self, url, timeout=10, max_bytes=262144):
        self.fetched.append(url)
        import urllib.parse as up
        m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)$", url)
        if not m or m.group(3) != self.branch:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        rel = up.unquote(m.group(4).split("?", 1)[0])          # the harvester adds a cache-busting query
        pre = self.prefix + "/" if self.prefix else ""
        if rel.startswith(pre) and rel[len(pre):] in self.files:
            return self.files[rel[len(pre):]]
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)

    def fetch_bytes(self, url, timeout=15, max_bytes=1_500_000):
        txt = self.fetch_text(url, timeout, max_bytes)
        return txt if isinstance(txt, bytes) else txt.encode("utf-8")

    def fetch_json(self, url, timeout=10, max_bytes=2_000_000):
        self.fetched.append(url)
        if url.endswith("?recursive=1"):
            pre = self.prefix + "/" if self.prefix else ""
            return {"tree": [{"path": pre + p, "type": "blob"} for p in self.files]}
        return {"default_branch": self.branch}


def _install(monkeypatch, files, branch="main", prefix=""):
    gh = FakeGitHub(files, branch, prefix)
    monkeypatch.setattr(app.fdharvest, "fetch_text", gh.fetch_text)
    monkeypatch.setattr(app.fdharvest, "fetch_json", gh.fetch_json)
    monkeypatch.setattr(app.fdharvest, "fetch_bytes", gh.fetch_bytes)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return gh


def test_submit_unreadable_repo_is_400(server, monkeypatch):
    _install(monkeypatch, {})
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/o/empty"})
    assert status == 400 and "private, empty" in json.loads(body)["error"]


def test_submit_network_failure_is_502(server, monkeypatch):
    def boom(url, **kw):
        raise OSError("no network")
    monkeypatch.setattr(app.fdharvest, "fetch_text", boom)
    monkeypatch.setattr(app.fdharvest, "fetch_json", boom)
    monkeypatch.setattr(app.fdharvest, "harvest", lambda *a, **k: (_ for _ in ()).throw(OSError("no network")))
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/o/down"})
    assert status == 502 and json.loads(body)["ok"] is False


def test_submit_package_then_duplicate(server, monkeypatch):
    gh = _install(monkeypatch, PACKAGE_FILES, branch="dev", prefix="studies/remote-two")
    url = "https://github.com/someone/repo/tree/dev/studies/remote-two"
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": url})
    assert status == 200, body
    resp = json.loads(body)
    assert resp["ok"] and resp["slug"] == "remote-two" and resp["kind"] == "filedrawer_package"
    assert resp["record_url"].startswith("/records/") and "tagged analyses" in resp["message"]
    assert not any(u.endswith("data/clean.csv") for u in gh.fetched)          # data never fetched

    status, body, headers = _req(server["base"], resp["record_url"])
    assert status == 200 and headers["Cache-Control"] == "max-age=300"
    rec = json.loads(body)
    assert rec["title"] == "Remote study two" and rec["links"]["report"].endswith("/studies/remote-two/report.md")
    assert rec["files"]["n_data"] == 1 and rec["extraction"]["method"] == "study_json"

    status, body, _ = _req(server["base"], "/index.json")
    idx = json.loads(body)
    rows = {s["slug"]: s for s in idx["studies"]}
    assert rows["remote-two"]["source"] == "submitted" and rows["remote-two"]["kind"] == "filedrawer_package"
    assert rows["remote-two"]["repo_url"] == "https://github.com/someone/repo"
    assert rows["remote-two"]["record_url"] == resp["record_url"]
    assert any({e["source"], e["target"]} == {"local-one", "remote-two"} for e in idx["edges"])

    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": url + "/"})
    assert status == 409 and json.loads(body)["id"] == resp["id"]
    files = list((server["data_dir"] / "records").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["slug"] == "remote-two"


def test_plain_archive_is_turned_away(server, monkeypatch):
    """Only self-run filedrawer packages are listed: a plain replication archive is screened out and nothing is stored."""
    _install(monkeypatch, ARCHIVE_FILES, branch="master")
    before = sorted(p.name for p in (server["data_dir"] / "records").glob("*"))
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/other/cues"})
    resp = json.loads(body)
    assert status == 422 and not resp["ok"] and "nothing was stored" in resp["error"]
    assert any(c["name"] == "self-run filedrawer package" and not c["passed"] for c in resp["screening"]["checks"])
    assert sorted(p.name for p in (server["data_dir"] / "records").glob("*")) == before


def test_admin_records_refresh_remove(server, monkeypatch):
    status, _, _ = _req(server["base"], "/admin/records")
    assert status == 403
    status, body, _ = _req(server["base"], "/admin/records?token=s3cret")
    recs = json.loads(body)
    assert status == 200 and len(recs) == 1
    rid = next(r["id"] for r in recs if r["slug"] == "remote-two")
    _install(monkeypatch, dict(PACKAGE_FILES, **{"study.json": json.dumps(dict(json.loads(PACKAGE_FILES["study.json"]), title="Remote study two v2"))}),
             branch="dev", prefix="studies/remote-two")
    status, body, _ = _req(server["base"], f"/admin/refresh?token=s3cret&id={rid}", "POST", {})
    assert status == 200 and json.loads(body)["title"] == "Remote study two v2"
    status, _, _ = _req(server["base"], f"/admin/remove?token=wrong&id={rid}", "POST", {})
    assert status == 403
    status, body, _ = _req(server["base"], f"/admin/remove?token=s3cret&id={rid}", "POST", {})
    assert status == 200 and json.loads(body)["ok"]
    status, _, _ = _req(server["base"], f"/records/{rid}.json")
    assert status == 404
    status, body, _ = _req(server["base"], "/index.json")
    assert "remote-two" not in {s["slug"] for s in json.loads(body)["studies"]}


def test_path_traversal_blocked(server):
    for p in ("/../etc/passwd", "/..%2F..%2Fetc%2Fpasswd", "/%2e%2e/%2e%2e/etc/passwd", "/nope.html"):
        status, _, _ = _req(server["base"], p)
        assert status == 404, p


def test_registry_json_served(server, tmp_path):
    # docs_dir defaults to the repo docs/, so registry.json is served statically
    status, body, headers = _req(server["base"], "/registry.json")
    assert status == 200 and "application/json" in headers["Content-Type"]
    assert "studies" in json.loads(body)


def test_parse_repo_url():
    p = app.parse_repo_url("https://github.com/own/repo")
    assert p["branch"] is None and p["path"] == ""
    p = app.parse_repo_url("https://github.com/own/repo/tree/feat/x/studies/s1/")
    assert p["path"].endswith("studies/s1")
    assert app.parse_repo_url("https://github.com/own") is None
    assert app.parse_repo_url("https://gitlab.com/own/repo") is None


def test_paper_stored_served_and_removed(server, monkeypatch):
    _install(monkeypatch, dict(PACKAGE_FILES, **{"study.json": json.dumps(dict(STUDY, slug="paper-three", title="Paper three"))}),
             branch="main", prefix="studies/paper-three")
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/someone/repo/tree/main/studies/paper-three"})
    assert status == 200, body
    rid = json.loads(body)["id"]
    status, body, headers = _req(server["base"], f"/records/{rid}/paper.md")
    assert status == 200 and headers["Content-Type"].startswith("text/markdown")
    assert body.decode().startswith("# Remote study two")              # read from GitHub on request
    assert not (server["data_dir"] / "records" / f"{rid}.paper.md").exists()   # never stored here
    rec = json.loads(_req(server["base"], f"/records/{rid}.json")[1])
    assert rec["paper_url"] == f"/records/{rid}/paper.md" and "_paper_text" not in rec and rec["paper"]["source"] == "report.md"
    rows = {s["slug"]: s for s in json.loads(_req(server["base"], "/index.json")[1])["studies"]}
    assert rows["paper-three"]["paper_url"] == f"/records/{rid}/paper.md"
    assert rows["paper-three"]["paper_base"].endswith("/studies/paper-three/")
    _req(server["base"], f"/admin/remove?token=s3cret&id={rid}", "POST", {})
    assert _req(server["base"], f"/records/{rid}/paper.md")[0] == 404
    assert not (server["data_dir"] / "records" / f"{rid}.paper.md").exists()


def test_bundled_study_files_served(server):
    """Bundled studies: report and figures are served from the server; provenance and scripts are not."""
    base = server["base"]
    studies = app.SETTINGS["studies_dir"]
    (studies / "local-one" / "figures").mkdir(parents=True, exist_ok=True)
    (studies / "local-one" / "report.md").write_text("# Local one\n\n![f](figures/f.png)\n")
    (studies / "local-one" / "figures" / "f.png").write_bytes(b"\x89PNG")
    (studies / "local-one" / "provenance").mkdir(exist_ok=True)
    (studies / "local-one" / "provenance" / "llm_log.jsonl").write_text("{}")
    app.get_index(force=True)
    status, body, headers = _req(base, "/studies/local-one/report.md")
    assert status == 200 and headers["Content-Type"].startswith("text/markdown") and b"Local one" in body
    assert _req(base, "/studies/local-one/figures/f.png")[0] == 200
    assert _req(base, "/studies/local-one/provenance/llm_log.jsonl")[0] == 404
    assert _req(base, "/studies/local-one/../../etc/passwd")[0] == 404
    rows = {s["slug"]: s for s in json.loads(_req(base, "/index.json")[1])["studies"]}
    assert rows["local-one"]["paper_url"] == "/studies/local-one/report.md" and rows["local-one"]["paper_base"] == "/studies/local-one/"


def test_submit_returns_similar_and_similar_route(server, monkeypatch):
    _install(monkeypatch, dict(PACKAGE_FILES, **{"study.json": json.dumps(dict(STUDY, slug="sim-four", title="Similar four",
                                                                                 constructs=["immigration_attitudes"], keywords=["immigration"]))}),
             branch="main", prefix="studies/sim-four")
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/someone/repo/tree/main/studies/sim-four"})
    assert status == 200, body
    resp = json.loads(body)
    assert resp["paper_url"].endswith("/paper.md")
    sims = {s["slug"]: s for s in resp["similar"]}
    assert "local-one" in sims and sims["local-one"]["weight"] > 0.5 and "construct:immigration_attitudes" in sims["local-one"]["reasons"]
    status, body, _ = _req(server["base"], "/similar/local-one")
    assert status == 200 and "sim-four" in {s["slug"] for s in json.loads(body)["similar"]}



def test_llms_txt_lists_papers_with_citations():
    from server.app import llms_txt
    txt = llms_txt(base="https://example.org")
    assert txt.startswith("# The File Drawer") and "cite that paper" in txt and "## Papers" in txt
