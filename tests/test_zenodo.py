"""`filedrawer release --doi`: Zenodo deposit flow against a fake API (no network)."""
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class FakeZenodo:
    """Just enough of the deposit API: create, metadata (with a reserved DOI), bucket upload, publish, new version."""

    def __init__(self, prefix="10.5281"):
        self.base, self.prefix = "https://zenodo.org", prefix
        self.deps, self.next_id, self.uploads, self.calls = {}, 100, {}, []

    def _new(self, concept=None):
        did = self.next_id
        self.next_id += 1
        self.deps[did] = {"id": did, "conceptrecid": concept or did - 1 if concept else did + 50, "files": [], "metadata": {},
                          "links": {"bucket": f"https://zenodo.org/api/files/b{did}", "latest_draft": f"/api/deposit/depositions/{did}"}}
        self.deps[did]["conceptrecid"] = concept or 900 + did
        return self.deps[did]

    def req(self, method, url, body=None, raw=None):
        url = url.replace(self.base, "")
        self.calls.append((method, url))
        parts = url.strip("/").split("/")
        if method == "POST" and url == "/api/deposit/depositions":
            return dict(self._new())
        if url.startswith("/api/files/"):
            self.uploads[url] = raw
            did = int(parts[2][1:])
            self.deps[did]["files"].append({"id": f"f{len(self.uploads)}", "filename": parts[-1]})
            return {}
        did = int(parts[3])
        d = self.deps[did]
        if method == "GET" and len(parts) == 4:
            return dict(d)
        if method == "GET" and parts[-1] == "files":
            return list(d["files"])
        if method == "DELETE":
            d["files"] = [f for f in d["files"] if f["id"] != parts[-1]]
            return {}
        if method == "PUT":
            d["metadata"] = {**body["metadata"], "prereserve_doi": {"doi": f"{self.prefix}/zenodo.{did}", "recid": did}}
            return dict(d)
        if method == "POST" and parts[-1] == "publish":
            d["published"] = True
            return {"id": did, "doi": f"{self.prefix}/zenodo.{did}", "conceptdoi": f"{self.prefix}/zenodo.{d['conceptrecid']}",
                    "links": {"html": f"https://zenodo.org/records/{did}"}}
        if method == "POST" and parts[-1] == "newversion":
            nd = self._new(concept=d["conceptrecid"])
            nd["files"] = list(d["files"])
            return {"links": {"latest_draft": f"/api/deposit/depositions/{nd['id']}"}}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture(scope="module")
def package(tmp_path_factory, demo_csv):
    from filedrawer.config import load_config
    from filedrawer.orchestrator import run_pipeline
    out = tmp_path_factory.mktemp("zen")
    repo = out / "demo-repo"; repo.mkdir()
    (repo / "inputs").mkdir()
    (repo / "inputs" / "raw.csv").write_text("secret,1\n")
    cfg = load_config(overrides={"provider": "mock"})
    return run_pipeline({"csv": str(demo_csv), "qsf": str(ROOT / "demo" / "demo.qsf"), "pap": str(ROOT / "demo" / "pap.md"),
                         "slug": "demo-immigration-framing", "title": "Demo", "authors": ["Ana B. Author"], "out_dir": str(out),
                         "package_dir": str(repo), "repo_url": "https://github.com/x/demo"}, cfg,
                        {"review": True, "no_lit": True, "no_exploratory": True, "synthetic": True})


def test_metadata_links_back_and_names_creators(package):
    from filedrawer.zenodo import metadata
    sj = json.loads((package / "study.json").read_text())
    md = metadata(sj, site="https://filedrawer.org", community="filedrawer")
    assert md["creators"] == [{"name": "Author, Ana B."}] and md["upload_type"] == "publication" and md["license"] == "cc-by-4.0"
    rel = {r["relation"]: r["identifier"] for r in md["related_identifiers"]}
    assert rel["isIdenticalTo"] == "https://filedrawer.org/#/paper/demo-immigration-framing"
    assert rel["isSupplementedBy"].startswith("https://github.com/x/demo")
    assert md["communities"] == [{"identifier": "filedrawer"}]


def test_sandbox_mint_records_test_doi_without_touching_citations(package):
    from filedrawer import zenodo as Z
    before = (package / "CITATION.cff").read_text()
    fake = FakeZenodo(prefix="10.5072")
    out = Z.mint(package, sandbox=True, client=fake, log=lambda m: None)
    assert out["published"] and out["doi"].startswith("10.5072/zenodo.")
    rec = Z.load_record(package)
    assert rec["sandbox"]["published"] and "production" not in rec
    assert (package / "CITATION.cff").read_text() == before            # sandbox DOIs never reach the citation files
    blob = next(iter(fake.uploads.values()))
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert "demo-immigration-framing/report.md" in names and "demo-immigration-framing/zenodo.json" in names
    assert not any("/inputs/" in n for n in names)                    # raw inputs never archived
    assert not any("/data/" in n for n in names)                      # nor respondent data (this folder is not in git)
    assert not any("llm_log" in n for n in names)


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_data_stays_local_unless_committed_and_asked_for(tmp_path):
    from filedrawer import zenodo as Z
    pkg = tmp_path / "pkg"
    for rel, text in {"study.json": '{"slug": "s", "title": "T"}', "report.md": "# T\n", "results/H1.csv": "a,b\n1,2\n",
                      "data/clean.csv": "id,comment\n1,secret\n", "raw/export.csv": "x\n", "inputs/export.csv": "x\n",
                      "provenance/llm_log.jsonl": "{}\n", "provenance/provenance.json": "{}", ".filedrawer/submission.json": "{}"}.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(text)
    rels = lambda **kw: {f.relative_to(pkg).as_posix() for f in Z.archive_files(pkg, **kw)}
    # not a git repository: data never ships, even when asked for
    assert rels() == rels(include_data=True) == {"study.json", "report.md", "results/H1.csv", "provenance/provenance.json"}
    # a git repository with data/ committed: still left out unless include_data
    _git(pkg, "init", "-q")
    _git(pkg, "add", "-f", "study.json", "report.md", "results/H1.csv", "data/clean.csv", "provenance/provenance.json",
         "provenance/llm_log.jsonl", "raw/export.csv")
    assert "data/clean.csv" not in rels()
    assert "data/clean.csv" in rels(include_data=True)
    assert not {"provenance/llm_log.jsonl", "raw/export.csv"} & rels(include_data=True)
    # data on disk but not committed: not archived even with include_data
    (pkg / "data" / "other.csv").write_text("id\n1\n")
    assert "data/other.csv" not in rels(include_data=True)


def test_build_zip_refuses_local_only_files(tmp_path, monkeypatch):
    import pytest
    from filedrawer import zenodo as Z
    (tmp_path / "study.json").write_text('{"slug": "s", "title": "T"}')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "clean.csv").write_text("id\n1\n")
    monkeypatch.setattr(Z, "archive_files", lambda study, include_data=False: [tmp_path / "study.json", tmp_path / "data" / "clean.csv"])
    with pytest.raises(Z.ZenodoError, match="must stay local"):
        Z.build_zip(tmp_path)


def test_production_mint_writes_doi_into_the_package_then_versions(package, monkeypatch):
    from filedrawer import zenodo as Z
    monkeypatch.setattr(Z, "_confirm", lambda msg: False)
    fake = FakeZenodo()
    # without --yes (and no terminal) the draft is reserved and filled but not published
    out = Z.mint(package, client=fake, log=lambda m: None)
    assert out["published"] is False and Z.load_record(package)["production"]["draft_id"]
    assert not any(c[1].endswith("/publish") for c in fake.calls)
    out = Z.mint(package, yes=True, client=fake, log=lambda m: None)        # resumes the same draft, publishes
    assert out["published"] and out["doi"] == f"10.5281/zenodo.{out['doi'].split('.')[-1]}"
    concept = out["concept_doi"]
    cff = (package / "CITATION.cff").read_text()
    assert f'doi: "{concept}"' in cff and out["doi"] in cff
    sj = json.loads((package / "study.json").read_text())
    assert sj["doi"] == concept and sj["doi_version"] == out["doi"]
    rep = (package / "report.md").read_text()
    assert f"https://doi.org/{concept}" in rep and f"doi = {{{concept}}}" in rep
    # the uploaded archive already carries the DOI it was published under
    blob = list(fake.uploads.values())[-1]
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert concept in z.read("demo-immigration-framing/CITATION.cff").decode()
    # a second release is a new version under the same concept DOI
    out2 = Z.mint(package, yes=True, client=fake, log=lambda m: None, new_version=True)
    assert out2["concept_doi"] == concept and out2["doi"] != out["doi"]
    assert any(c[1].endswith("/newversion") for c in fake.calls)
    assert len(Z.load_record(package)["production"]["versions"]) == 2
    assert json.loads((package / "study.json").read_text())["doi_version"] == out2["doi"]


def test_rerender_keeps_release_status_and_doi(package):
    """A pipeline re-render (extensions, address) must not drop the release flag or the DOI."""
    from filedrawer.release import release
    from filedrawer.zenodo import _rerender
    release(package)
    _rerender(package)
    sj = json.loads((package / "study.json").read_text())
    assert sj["release_status"] == "released" and sj.get("released") and sj["doi"].startswith("10.5281/zenodo.")


def test_token_is_required_and_never_echoed(monkeypatch, tmp_path):
    from filedrawer import zenodo as Z
    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.setattr(Z, "ENV_FILE", tmp_path / "none.env")
    with pytest.raises(SystemExit) as e:
        Z.token(False)
    assert "ZENODO_TOKEN" in str(e.value)
    (tmp_path / "z.env").write_text("ZENODO_TOKEN=abc123secret\n")
    monkeypatch.setattr(Z, "ENV_FILE", tmp_path / "z.env")
    assert Z.token(False) == "abc123secret"


def test_unchanged_package_gets_no_new_version(package):
    """Re-releasing without changes reuses the DOI; a changed result table archives a new version."""
    from filedrawer import zenodo as Z
    (package / "zenodo.json").unlink(missing_ok=True)      # a fresh record on a fresh fake server
    fake = FakeZenodo()
    first = Z.mint(package, yes=True, client=fake, log=lambda m: None)
    calls = len(fake.calls)
    again = Z.mint(package, yes=True, client=fake, log=lambda m: None)
    assert again.get("unchanged") and again["doi"] == first["doi"] and len(fake.calls) == calls
    forced = Z.mint(package, yes=True, client=fake, log=lambda m: None, new_version=True)
    assert forced["doi"] != first["doi"] and not forced.get("unchanged")
    p = next((package / "results").glob("*.csv"))
    p.write_text(p.read_text() + "\n")
    changed = Z.mint(package, yes=True, client=fake, log=lambda m: None)
    assert changed["doi"] != forced["doi"] and changed["concept_doi"] == first["concept_doi"]


def test_doi_badge_links_to_doi_org(package):
    from filedrawer.badges import badges_for
    sj = json.loads((package / "study.json").read_text())
    b = {x["name"]: x for x in badges_for(sj)}
    assert b["doi"]["value"] == sj["doi"] and b["doi"]["href"] == f"https://doi.org/{sj['doi']}"
    names = [x["name"] for x in badges_for(sj)]
    assert names.index("release") + 1 == names.index("doi")
    assert (package / "figures" / "badges" / "doi.svg").exists()
    assert f"](https://doi.org/{sj['doi']})" in (package / "report.md").read_text()
    assert "doi" not in {x["name"] for x in badges_for({**sj, "doi": None})}


def test_release_mints_by_default_and_survives_a_missing_token(package, monkeypatch, capsys):
    from filedrawer import zenodo as Z
    from filedrawer.cli import main
    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.setattr(Z, "ENV_FILE", package / "no.env")
    assert main(["release", str(package)]) == 0
    out = capsys.readouterr().out
    assert "released" in out and "No DOI minted" in out and "ZENODO_TOKEN" in out
    seen = {}
    monkeypatch.setattr(Z, "token", lambda sandbox: "t")
    monkeypatch.setattr(Z, "mint", lambda study, **kw: seen.update(kw) or {"published": True, "unchanged": True, "doi": "x"})
    assert main(["release", str(package), "--yes"]) == 0 and seen["yes"] is True and seen["sandbox"] is False
    seen.clear()
    assert main(["release", str(package), "--no-doi"]) == 0 and not seen


def test_rerender_lists_only_package_files_and_keeps_reviewer_note(package):
    """In a study repository the report lists the package's files, not the author's inputs, original code, notes
    or leftovers; a re-render keeps the reviewer-pass note."""
    from filedrawer.zenodo import _rerender
    (package / "original").mkdir(exist_ok=True)
    (package / "original" / "analysis.R").write_text("# author code\n")
    (package / "results.prev").mkdir(exist_ok=True)
    (package / "results.prev" / "H1.csv").write_text("x\n")
    (package / "address.log").write_text("log\n")
    _rerender(package)
    rep = (package / "report.md").read_text()
    files = rep.split("\n### Files\n", 1)[1]
    assert "- `report.md`" in files and "- `data/clean.csv`" in files
    for stray in ("inputs/", "original/", "results.prev/", "address.log", "ctx_snapshot.json"):
        assert stray not in files, stray
    assert "### Reviewer pass" in rep
