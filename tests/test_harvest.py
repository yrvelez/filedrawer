"""Harvester tests with fake GitHub fetchers (no network)."""
import json
import urllib.error
from pathlib import Path

import pytest

from filedrawer import harvest as H

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "studies" / "demo-immigration-framing"


class FakeGitHub:
    """Serves files from a dict {relpath: text} for one repo/branch, records every URL fetched."""

    def __init__(self, owner, repo, branch, files: dict, default_branch=None, api_ok=True, prefix=""):
        self.owner, self.repo, self.branch, self.files = owner, repo, branch, files
        self.default_branch, self.api_ok, self.prefix = default_branch or branch, api_ok, prefix
        self.fetched: list[str] = []

    def fetch_text(self, url, timeout=10, max_bytes=262144):
        self.fetched.append(url)
        base = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/"
        if not url.startswith(base):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        rel = urllib.parse.unquote(url[len(base):].split("?", 1)[0])       # the harvester adds a cache-busting query
        if self.prefix:
            if not rel.startswith(self.prefix + "/"):
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            rel = rel[len(self.prefix) + 1:]
        if rel not in self.files:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return self.files[rel]

    def fetch_bytes(self, url, timeout=15, max_bytes=1_500_000):
        txt = self.fetch_text(url, timeout, max_bytes)
        return txt if isinstance(txt, bytes) else txt.encode("utf-8")

    def fetch_json(self, url, timeout=10, max_bytes=2_000_000):
        self.fetched.append(url)
        if not self.api_ok:
            raise urllib.error.HTTPError(url, 403, "rate limited", {}, None)
        if url == f"https://api.github.com/repos/{self.owner}/{self.repo}":
            return {"default_branch": self.default_branch}
        if url.startswith(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/"):
            pre = (self.prefix + "/") if self.prefix else ""
            return {"tree": [{"path": pre + p, "type": "blob"} for p in self.files], "truncated": False}
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)


import urllib.parse  # noqa: E402


def package_files(with_study_json=True) -> dict:
    files = {}
    for p in DEMO.rglob("*"):
        if p.is_file() and "provenance/llm_log" not in str(p) and "script_history" not in str(p):
            rel = str(p.relative_to(DEMO))
            if rel == "study.json" and not with_study_json:
                continue
            files[rel] = p.read_text(encoding="utf-8", errors="replace") if p.suffix in (".json", ".md", ".csv", ".py", ".txt", ".qsf") else "<binary>"
    return files


def test_package_with_study_json():
    gh = FakeGitHub("yrvelez", "filedrawer", "main", package_files(True), prefix="studies/demo-immigration-framing")
    rec = H.harvest("https://github.com/yrvelez/filedrawer/tree/main/studies/demo-immigration-framing",
                    fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert rec["kind"] == "filedrawer_package" and rec["extraction"]["method"] == "study_json"
    assert rec["slug"] == "demo-immigration-framing" and rec["synthetic"] is True
    assert rec["constructs"] == ["immigration_attitudes", "framing_effects", "partisanship"]
    assert {h["id"]: h["tag"] for h in rec["hypotheses"]}["H2"] == "deviation"
    assert rec["provenance"]["mode"] == "fully_agentic" and rec["provenance"]["reviewer_pass"] is True
    assert rec["links"]["report"].endswith("/studies/demo-immigration-framing/report.md")
    assert rec["links"]["study_json"].endswith("/study.json")
    assert rec["files"]["n_data"] >= 2 and "Python" in rec["files"]["languages"]
    assert rec["summary"].startswith("In a two-arm")
    # privacy: no data file was ever fetched
    assert not any(u.endswith(("/data/clean.csv", "/data/raw_tidy.csv", "/silicon/responses.csv")) for u in gh.fetched)
    slim = {k: v for k, v in rec.items() if k != '_paper_text'}
    assert len(json.dumps(slim)) < 12000          # the record stays slim; the paper is stored separately


def test_package_without_study_json_uses_report_pap_and_tables():
    gh = FakeGitHub("o", "r", "main", package_files(False), prefix="pkg")
    rec = H.harvest("https://github.com/o/r/tree/main/pkg", fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert rec["kind"] == "filedrawer_package" and rec["extraction"]["method"] == "package_files"
    assert rec["title"].startswith("Economic-contribution framing")
    assert rec["provenance"]["mode"] == "fully_agentic" and rec["synthetic"] is True
    tags = {h["id"]: h["tag"] for h in rec["hypotheses"]}
    assert tags == {"H1": "registered", "H2": "deviation", "E1": "exploratory", "E2": "exploratory", "E3": "exploratory"}
    h1 = next(h for h in rec["hypotheses"] if h["id"] == "H1")
    assert abs(h1["estimate"] - 0.583) < 0.01 and h1["supported"] is True and h1["n"] == 387
    assert rec["design"]["type"] == "survey_experiment" and rec["population"]["sample"] == "online_panel"
    assert "immigration_attitudes" in rec["constructs"]
    assert rec["created"] == "2026-10-01" or rec["created"]


ARCHIVE = {
    "README.md": "# Replication materials for *Partisan cues and support for redistribution*\n\nThis archive contains the Stata and R code and the survey data (YouGov, N = 2,000) used in the paper on party cues, redistribution preferences and polarization.\n\n## Files\n- `data/survey.dta`\n- `code/analysis.do`\n",
    "data/survey.dta": "<binary>", "data/codebook.pdf": "<binary>", "code/analysis.do": "use data/survey.dta", "code/figures.R": "library(ggplot2)",
    "paper.pdf": "<binary>", "LICENSE": "MIT",
}


def test_plain_replication_archive():
    gh = FakeGitHub("someone", "cues-replication", "master", ARCHIVE, default_branch="master")
    rec = H.harvest("https://github.com/someone/cues-replication", fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert rec["kind"] == "replication_archive" and rec["extraction"]["method"] == "readme"
    assert rec["branch"] == "master" and rec["slug"] == "cues-replication"
    assert rec["title"].startswith("Replication materials for Partisan cues")
    assert rec["files"]["data"] == ["data/survey.dta"] and rec["files"]["languages"] == ["R", "Stata"]
    assert rec["files"]["data_formats"] == ["Stata"]
    assert "elite_cues" in rec["constructs"] and "economic_attitudes" in rec["constructs"]
    assert rec["links"]["data"].endswith("/tree/master/data") and rec["links"]["readme"].endswith("/blob/master/README.md")
    assert "YouGov" in rec["readme_excerpt"]
    assert rec["hypotheses"] == [] and rec["provenance"]["mode"] == "unknown"
    assert not any(".dta" in u or ".pdf" in u for u in gh.fetched)


def test_api_failure_falls_back_to_probing():
    gh = FakeGitHub("someone", "cues-replication", "main", ARCHIVE, api_ok=False)
    rec = H.harvest("https://github.com/someone/cues-replication", fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert rec["branch"] == "main" and rec["extraction"]["method"] == "readme"
    assert any("default branch lookup failed" in w for w in rec["extraction"]["warnings"])
    assert any("file listing unavailable" in w for w in rec["extraction"]["warnings"])
    assert rec["files"]["n_files"] == 0 and "YouGov" in rec["readme_excerpt"]


def test_llm_fills_gaps_for_archives():
    gh = FakeGitHub("someone", "cues-replication", "main", ARCHIVE)
    calls = []

    def fake_llm(readme, report, files, vocab):
        calls.append((len(readme), files["n_data"]))
        assert "<binary>" not in readme
        return {"title": "Partisan cues and support for redistribution", "constructs": ["elite_cues", "economic_attitudes", "not_a_construct"],
                "keywords": ["party cues", "redistribution"], "design_type": "survey_experiment", "population": "yougov", "country": "us",
                "summary": "Party cues move redistribution preferences."}
    rec = H.harvest("https://github.com/someone/cues-replication", fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, llm=fake_llm, use_llm=True)
    assert calls == [(len(ARCHIVE["README.md"]), 1)]
    assert rec["extraction"]["llm_used"] is True
    assert rec["title"] == "Partisan cues and support for redistribution"
    assert "not_a_construct" not in rec["constructs"] and "elite_cues" in rec["constructs"]
    assert rec["design"]["type"] == "survey_experiment" and rec["population"] == {"country": "US", "sample": "yougov"}
    assert rec["summary"].startswith("Party cues")


def test_llm_not_called_for_rich_packages():
    gh = FakeGitHub("yrvelez", "filedrawer", "main", package_files(True), prefix="studies/demo-immigration-framing")
    called = []
    rec = H.harvest("https://github.com/yrvelez/filedrawer/tree/main/studies/demo-immigration-framing",
                    fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, llm=lambda *a: called.append(1) or {}, use_llm=True)
    assert called == [] and rec["extraction"]["llm_used"] is False


def test_bad_or_empty_urls():
    with pytest.raises(ValueError):
        H.harvest("https://gitlab.com/a/b", use_llm=False)
    gh = FakeGitHub("o", "r", "main", {})
    with pytest.raises(RuntimeError):
        H.harvest("https://github.com/o/r", fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert H.parse_github_url("https://github.com/o/r/blob/main/studies/x/study.json")["path"] == "studies/x"
    assert H.parse_github_url("https://github.com/o/r.git")["branch"] is None


def test_paper_text_captured():
    gh = FakeGitHub("yrvelez", "filedrawer", "main", package_files(True), prefix="studies/demo-immigration-framing")
    rec = H.harvest("https://github.com/yrvelez/filedrawer/tree/main/studies/demo-immigration-framing",
                    fetch_text_fn=gh.fetch_text, fetch_json_fn=gh.fetch_json, use_llm=False)
    assert rec["paper"]["source"] == "report.md" and rec["paper"]["bytes"] > 5000
    assert rec["paper"]["base_url"].endswith("/studies/demo-immigration-framing/")
    assert rec["_paper_text"].startswith("# Economic-contribution framing")
    gh2 = FakeGitHub("someone", "cues-replication", "main", ARCHIVE)
    rec2 = H.harvest("https://github.com/someone/cues-replication", fetch_text_fn=gh2.fetch_text, fetch_json_fn=gh2.fetch_json, use_llm=False)
    assert rec2["paper"]["source"] == "README.md" and "YouGov" in rec2["_paper_text"]
