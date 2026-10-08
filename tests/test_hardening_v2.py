"""Harvester and server hardening: figure capture within caps, no README-as-paper for packages, cache-busting URLs,
record backups and restore, the figures route, /version, and the two compare commands."""
import csv
import json
import urllib.error
from pathlib import Path

import pytest

from filedrawer import harvest as H
from filedrawer.analysis.compare import compare_results
from tests.test_harvest import FakeGitHub, package_files
from tests.test_server import PACKAGE_FILES, STUDY, _install, _req, server  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def _repo(gh, prefix=""):
    return H.Repo(gh.owner, gh.repo, gh.branch, prefix, gh.fetch_text, gh.fetch_json, gh.fetch_bytes)


def test_raw_url_cache_busting_keeps_the_base_clean():
    gh = FakeGitHub("o", "r", "main", {"README.md": "# x"})
    repo = _repo(gh)
    assert "?fd=" in repo.raw_url("report.md") and repo.raw_url("") == "https://raw.githubusercontent.com/o/r/main/"
    assert "?fd=" not in repo.raw_url("report.md", bust=False)


def test_capture_figures_filters_and_caps(monkeypatch):
    files = {"figures/a.png": "PNGDATA", "figures/b.svg": "<svg/>", "extensions/x.svg": "<svg/>", "data/clean.csv": "x",
             "figures/huge.png": "P" * 50}
    gh = FakeGitHub("o", "r", "main", files)
    text = ("![a](figures/a.png) ![b](figures/b.svg) ![gone](figures/missing.png) ![x](extensions/x.svg) ![data](data/clean.csv) "
            "![ext](https://x/y.png) ![up](../figures/a.png) ![huge](figures/huge.png)")
    monkeypatch.setattr(H, "FIGURE_MAX_TOTAL", 15)          # a, b and x (19 bytes) fill the cap before huge is reached
    figs, info = H.capture_figures(_repo(gh), text)
    assert set(figs) == {"figures/a.png", "figures/b.svg", "extensions/x.svg"} and figs["figures/a.png"] == b"PNGDATA"
    assert info["missing"] == ["figures/missing.png"] and info["skipped"] == ["figures/huge.png"] and info["n"] == 3


def test_capture_figures_follows_plain_links():
    files = {"extensions/x.svg": "<svg/>", "extensions/x.txt": "words", "extensions/x.qsf": "{}", "figures/design.txt": "d"}
    gh = FakeGitHub("o", "r", "main", files)
    text = ("Files: [diagram](extensions/x.svg) · [plain-text description](extensions/x.txt) · [`extensions/x.qsf`](extensions/x.qsf)\n"
            "[design in words](figures/design.txt)")
    figs, info = H.capture_figures(_repo(gh), text)
    assert set(figs) == {"extensions/x.svg", "extensions/x.txt", "figures/design.txt"}      # the QSF stays on GitHub


def test_package_without_readable_report_is_refused():
    files = package_files(True)
    files.pop("report.md")
    gh = FakeGitHub("yrvelez", "filedrawer", "main", files, prefix="studies/demo-immigration-framing")
    with pytest.raises(RuntimeError, match="refusing to file the README"):
        H.harvest("https://github.com/yrvelez/filedrawer/tree/main/studies/demo-immigration-framing",
                  fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)


def test_harvest_records_figures_and_badges():
    files = package_files(True)
    files["figures/registered_effects.png"] = "PNG"
    gh = FakeGitHub("yrvelez", "filedrawer", "main", files, prefix="studies/demo-immigration-framing")
    rec = H.harvest("https://github.com/yrvelez/filedrawer/tree/main/studies/demo-immigration-framing",
                    fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False, keep_figures=True)
    assert rec["figures"]["n"] >= 1 and "figures/registered_effects.png" in rec["_figures"]
    json.dumps(rec)                                              # the record stays serializable (figures are base64)
    assert rec["design"]["sample_kind"] == "human"
    diff = H.compare_records({"title": "old", "paper": {"source": "README.md"}}, rec)
    assert diff["changed"]["title"]["new"] == rec["title"] and diff["changed"]["paper_source"]["new"] == "report.md"


def test_compare_results(tmp_path):
    def write(d, rows, tags):
        d.mkdir()
        with open(d / "registered_summary.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["analysis_id", "term", "estimate", "std_error"])
            w.writeheader()
            w.writerows(rows)
        with open(d / "analysis_tags.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["analysis_id", "tag"])
            w.writeheader()
            w.writerows(tags)
    old = [{"analysis_id": "H1", "term": "", "estimate": "0.1", "std_error": "0.02"}, {"analysis_id": "E1", "term": "", "estimate": "0.5", "std_error": "0.1"}]
    new = [{"analysis_id": "H1", "term": "", "estimate": "0.1000000001", "std_error": "0.02"}, {"analysis_id": "X1", "term": "", "estimate": "0.3", "std_error": "0.1"}]
    write(tmp_path / "old", old, [{"analysis_id": "H1", "tag": "registered"}, {"analysis_id": "E1", "tag": "exploratory"}])
    write(tmp_path / "new", new, [{"analysis_id": "H1", "tag": "registered"}, {"analysis_id": "X1", "tag": "exploratory"}])
    out = compare_results(tmp_path / "old", tmp_path / "new")
    assert out["ok"] and out["registered_compared"] == 1 and out["added"] == ["X1"] and out["exploratory_new"] == ["X1"]
    new[0]["estimate"] = "0.2"
    write(tmp_path / "new2", new, [{"analysis_id": "H1", "tag": "registered"}])
    out = compare_results(tmp_path / "old", tmp_path / "new2")
    assert not out["ok"] and out["changed_registered"][0]["analysis_id"] == "H1"


def test_version_route(server):
    status, body, _ = _req(server["base"], "/version")
    assert status == 200 and "sha" in json.loads(body)


def test_nothing_but_metadata_is_stored(server, monkeypatch):
    """The server keeps one metadata record per paper: the report is read from GitHub on request, figures redirect to
    GitHub, and /audit lists every file on disk (only JSON)."""
    files = dict(PACKAGE_FILES, **{"study.json": json.dumps(dict(STUDY, slug="fig-study", title="Figures one")),
                                   "report.md": "# Figures one\n\n## Abstract\n\nText. ![fig](figures/f1.png)\n", "figures/f1.png": "PNG1"})
    gh = _install(monkeypatch, files, branch="main", prefix="studies/fig-study")
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/o/r/tree/main/studies/fig-study"})
    assert status == 200, body
    resp = json.loads(body)
    rid = resp["id"]
    steps = {s["step"]: s for s in resp["audit"]}
    assert "Never stored: data, code, the report text and figures" in steps["Stored"]["detail"]
    assert "report.md" in steps["Read small text files only"]["detail"]
    rec = json.loads(_req(server["base"], f"/records/{rid}.json")[1])
    assert rec["paper"]["base_url"].startswith("https://raw.githubusercontent.com/o/r/main/studies/fig-study/")
    assert "_figures" not in rec and "_paper_text" not in rec and rec["screening"]["ok"] and rec["audit"]
    import http.client
    from urllib.parse import urlsplit
    u = urlsplit(server["base"])
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)      # do not follow the redirect
    conn.request("GET", f"/records/{rid}/figures/f1.png")
    r = conn.getresponse()
    assert r.status == 302 and r.getheader("Location").endswith("/studies/fig-study/figures/f1.png")
    conn.close()
    assert _req(server["base"], f"/records/{rid}/figures/../{rid}.json")[0] == 404
    on_disk = [p for p in (server["data_dir"] / "records").rglob("*") if p.is_file()]
    assert on_disk and all(p.suffix == ".json" for p in on_disk)
    audit = json.loads(_req(server["base"], "/audit")[1])
    assert audit["non_metadata_files"] == [] and audit["records"] >= 1 and f"{rid}.json" in {f["file"] for f in audit["files"]}
    # the report follows GitHub: a refresh keeps the previous metadata; restore brings the old record back
    files["report.md"] = "# Figures one v2\n\n## Abstract\n\nNew text.\n"
    status, body, _ = _req(server["base"], f"/admin/refresh?token=s3cret&id={rid}", "POST", {})
    assert status == 200 and json.loads(body)["paper_source"] == "report.md"
    assert (server["data_dir"] / "records" / f"{rid}.prev.json").exists()
    assert "New text" in _req(server["base"], f"/records/{rid}/paper.md")[1].decode()
    assert _req(server["base"], f"/admin/restore?token=s3cret&id={rid}", "POST", {})[0] == 200
    assert _req(server["base"], f"/admin/restore?token=wrong&id={rid}", "POST", {})[0] == 403
    assert _req(server["base"], f"/admin/remove?token=s3cret&id={rid}", "POST", {})[0] == 200
    assert not (server["data_dir"] / "records" / f"{rid}.prev.json").exists()
    assert _req(server["base"], f"/records/{rid}/figures/f1.png")[0] == 404


def test_screening_turns_away_non_research(server, monkeypatch):
    poems = {"README.md": "# Autumn poems\n\nA collection of poetry and short fiction I wrote this year.\n", "poems/leaves.md": "leaves fall"}
    _install(monkeypatch, poems, branch="main")
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/someone/poems", "dry_run": True})
    resp = json.loads(body)
    assert status == 422 and not resp["ok"] and "nothing was stored" in resp["error"]
    assert any(not c["passed"] and c["name"] == "data or analysis code" for c in resp["screening"]["checks"])
    assert json.loads(_req(server["base"], "/audit")[1])["records"] == len(json.loads(_req(server["base"], "/records.json")[1])["records"])


def test_dry_run_stores_nothing(server, monkeypatch):
    files = dict(PACKAGE_FILES, **{"study.json": json.dumps(dict(STUDY, slug="dry-study", title="Dry one"))})
    _install(monkeypatch, files, branch="main", prefix="studies/dry-study")
    before = json.loads(_req(server["base"], "/audit")[1])["files"]
    status, body, _ = _req(server["base"], "/submit", "POST", {"repo_url": "https://github.com/o/r/tree/main/studies/dry-study", "dry_run": True})
    resp = json.loads(body)
    assert status == 200 and resp["dry_run"] and resp["screening"]["ok"] and "Nothing has been stored" in resp["message"]
    assert [s["step"] for s in resp["audit"]][-1] == "Would store"
    assert json.loads(_req(server["base"], "/audit")[1])["files"] == before



def test_startup_sweep_leaves_only_metadata_records(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT))
    import server.app as app
    app.configure(studies_dir=tmp_path / "studies", data_dir=tmp_path / "data")
    root = app.records_dir()
    for name in ("0123456789ab.json", "0123456789ab.prev.json", "0123456789ab.paper.md", "0123456789ab.token", "fedcba987654.token"):
        (root / name).write_text("x")
    (root / "0123456789ab" / "figures").mkdir(parents=True)
    (root / "0123456789ab" / "figures" / "f.png").write_text("png")
    gone = app.sweep_storage()
    assert sorted(p.name for p in root.iterdir()) == ["0123456789ab.json", "0123456789ab.prev.json"]
    assert "0123456789ab" in gone and "fedcba987654.token" in gone
    assert app.storage_audit()["non_metadata_files"] == []
