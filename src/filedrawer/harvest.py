"""Harvest a slim study record from a public GitHub repo or folder URL.

Stdlib only (runs inside the Fly container). The harvester reads a fixed allowlist of small
text files (study.json, report.md, pap.json, README, two result CSVs, provenance.json) and
inventories everything else by name only. Data files are never downloaded. The record it
returns is what the dashboard stores and serves; the data stays on GitHub.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RECORD_VERSION = 1
USER_AGENT = "filedrawer/0.1 (+https://github.com/yrvelez/filedrawer)"
MAX_FILE_BYTES = 262144
MAX_TOTAL_BYTES = 1_500_000
MAX_TREE_ENTRIES = 5000

ALLOWLIST = ["study.json", "report.md", "pap.json", "review.json", "responses.md", "extensions/index.json", "provenance/provenance.json",
             "results/analysis_tags.csv", "results/registered_summary.csv"]
README_NAMES = ["README.md", "readme.md", "README.MD", "README", "README.txt", "README.rst", "Readme.md"]

DATA_EXT = {".csv": "csv", ".tsv": "tsv", ".dta": "Stata", ".sav": "SPSS", ".rds": "R", ".rda": "R", ".rdata": "R",
            ".parquet": "parquet", ".xlsx": "Excel", ".xls": "Excel", ".feather": "feather", ".json": "json", ".qsf": "Qualtrics schema"}
SCRIPT_EXT = {".r": "R", ".rmd": "R", ".qmd": "R", ".py": "Python", ".ipynb": "Python", ".do": "Stata", ".ado": "Stata",
              ".sas": "SAS", ".jl": "Julia", ".m": "MATLAB", ".sh": "shell", ".sql": "SQL"}
DOC_EXT = {".md", ".pdf", ".docx", ".tex", ".txt", ".html", ".rst"}
DATA_DIR_HINT = re.compile(r"(^|/)(data|raw|clean|replication|dataverse|input|inputs)(/|$)", re.I)

GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?"
    r"(?:(?:tree|blob)/(?P<branch>[A-Za-z0-9_.\-/]+?)(?:/(?P<path>[A-Za-z0-9_.\-/ %]*?))?/?)?$"
)

_VOCAB_PATH = Path(__file__).resolve().parent / "vocab" / "constructs.json"


def load_vocab() -> dict:
    try:
        return json.loads(_VOCAB_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {"constructs": {}, "designs": [], "populations": []}


# ------------------------------------------------------------------ fetching


def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": USER_AGENT}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    h.update(extra or {})
    return h


def fetch_text(url: str, timeout: float = 10, max_bytes: int = MAX_FILE_BYTES) -> str:
    """GET a small text file. Raises urllib errors / ValueError. Patchable in tests."""
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"{url} larger than {max_bytes} bytes")
    return raw.decode("utf-8", "replace")


COMPARE_KEYS = ("title", "kind", "authors", "release_status", "review_claims")


def compare_records(live: dict, fresh: dict) -> dict:
    """What a refresh would change: a key-by-key diff of the fields that matter, for `filedrawer harvest --compare`."""
    def g(d, *path):
        for p in path:
            d = (d or {}).get(p) if isinstance(d, dict) else None
        return d
    fields = {k: (live.get(k), fresh.get(k)) for k in COMPARE_KEYS}
    fields.update({"n_analysis": (g(live, "design", "n_analysis"), g(fresh, "design", "n_analysis")),
                   "design_type": (g(live, "design", "type"), g(fresh, "design", "type")),
                   "analyses": (len(live.get("hypotheses") or []), len(fresh.get("hypotheses") or [])),
                   "provenance_mode": (g(live, "provenance", "mode"), g(fresh, "provenance", "mode")),
                   "paper_source": (g(live, "paper", "source"), g(fresh, "paper", "source")),
                   "paper_sha256": (g(live, "paper", "sha256"), g(fresh, "paper", "sha256")),
                   "extensions": (len(live.get("extensions") or []), len(fresh.get("extensions") or [])),
                   "badges": (len(live.get("badges") or []), len(fresh.get("badges") or [])),
                   "figures_captured": (g(live, "figures", "n"), g(fresh, "figures", "n"))})
    return {"same": {k: v[0] for k, v in fields.items() if v[0] == v[1]},
            "changed": {k: {"live": v[0], "new": v[1]} for k, v in fields.items() if v[0] != v[1]},
            "warnings": fresh.get("extraction", {}).get("warnings", [])}


def fetch_bytes(url: str, timeout: float = 15, max_bytes: int = 1_500_000) -> bytes:
    """GET a small binary file (a figure). Raises urllib errors / ValueError. Patchable in tests."""
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"{url} larger than {max_bytes} bytes")
    return raw


FIGURE_SUFFIXES = (".png", ".svg", ".txt")      # .txt: the plain-text design descriptions beside each diagram
FIGURE_MAX_FILE = 1_500_000
FIGURE_MAX_TOTAL = 12_000_000
FIGURE_MAX_COUNT = 40
# embedded images and plain links: extension cards link their diagram and description ("Files: [diagram](extensions/x.svg)")
_IMG_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def capture_figures(repo, text: str, fetch=None) -> tuple[dict[str, bytes], dict]:
    """Download the figures a paper links to (PNG/SVG under figures/ or extensions/), within caps, so the dashboard can
    show them after the repository goes private. Returns ({relative path: bytes}, {"n", "bytes", "missing", "skipped"})."""
    fetch = fetch or getattr(repo, "fetch_bytes", None) or fetch_bytes
    rels = []
    for m in _IMG_LINK.finditer(text or ""):
        rel = m.group(1).split("#", 1)[0]
        if rel.startswith(("http://", "https://", "/", "data:")) or ".." in rel.split("/"):
            continue
        if not rel.lower().endswith(FIGURE_SUFFIXES) or not rel.startswith(("figures/", "extensions/")):
            continue
        if rel not in rels:
            rels.append(rel)
    out, info = {}, {"n": 0, "bytes": 0, "missing": [], "skipped": []}
    for rel in rels:
        if len(out) >= FIGURE_MAX_COUNT or info["bytes"] >= FIGURE_MAX_TOTAL:
            info["skipped"].append(rel)
            continue
        try:
            data = fetch(repo.raw_url(rel), max_bytes=FIGURE_MAX_FILE)
        except Exception as exc:  # noqa: BLE001
            info["missing"].append(rel)
            if not isinstance(exc, urllib.error.HTTPError) or exc.code != 404:
                repo.warnings.append(f"figure {rel}: {type(exc).__name__}")
            continue
        out[rel] = data
        info["n"] += 1
        info["bytes"] += len(data)
    if info["skipped"]:
        repo.warnings.append(f"{len(info['skipped'])} figure(s) not captured: over the size or count cap")
    return out, info


def fetch_json(url: str, timeout: float = 10, max_bytes: int = 2_000_000):
    req = urllib.request.Request(url, headers=_headers({"Accept": "application/vnd.github+json"}))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("response too large")
    return json.loads(raw.decode("utf-8"))


# ------------------------------------------------------------------ parsing


def parse_github_url(url: str) -> dict | None:
    m = GITHUB_RE.match((url or "").strip())
    if not m:
        return None
    owner, repo = m.group("owner"), m.group("repo")
    path = (m.group("path") or "").strip("/")
    if owner.startswith(".") or repo.startswith(".") or ".." in path.split("/"):
        return None
    if path.endswith((".json", ".md", ".csv")):      # a blob URL to a file: use its folder
        path = path.rsplit("/", 1)[0] if "/" in path else ""
    return {"owner": owner, "repo": repo, "branch": m.group("branch"), "path": path}


def record_id(owner: str, repo: str, branch: str, path: str) -> str:
    return hashlib.sha1(f"{owner}/{repo}/{branch}/{path}".lower().encode()).hexdigest()[:12]


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^[#>*\-|\s]+", "", text, flags=re.M)
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def first_heading(md: str) -> str | None:
    for line in md.splitlines():
        if line.startswith("# "):
            return strip_markdown(line[2:]).strip()
    return None


def _parse_csv(text: str) -> list[dict]:
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return []


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def match_vocab(text: str, vocab: dict, max_hits: int = 6) -> list[str]:
    """Constructs whose id or synonyms occur in the text (case-insensitive, word-boundary)."""
    t = text.lower()
    hits = []
    for cid, syns in vocab.get("constructs", {}).items():
        terms = [cid.replace("_", " ")] + list(syns or [])
        score = sum(len(re.findall(r"\b" + re.escape(s.lower()) + r"\b", t)) for s in terms)
        if score:
            hits.append((score, cid))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in hits[:max_hits]]


def classify_files(paths: list[str]) -> dict:
    data, scripts, docs, langs = [], [], [], set()
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        name = os.path.basename(p)
        if ext in SCRIPT_EXT:
            scripts.append(p)
            langs.add(SCRIPT_EXT[ext])
        elif ext in DATA_EXT and (ext not in (".json", ".csv") or DATA_DIR_HINT.search(p) or ext == ".csv"):
            if name == "study.json" or name == "pap.json" or name == "codebook.json" or p.startswith("results/") or "/results/" in p or name == "index.json":
                continue
            data.append(p)
        elif ext in DOC_EXT:
            docs.append(p)
    fmt = sorted({DATA_EXT.get(os.path.splitext(p)[1].lower(), "") for p in data} - {""})
    return {"data": data[:50], "scripts": scripts[:50], "docs": docs[:30], "n_files": len(paths),
            "n_data": len(data), "n_scripts": len(scripts), "data_formats": fmt, "languages": sorted(langs)}


# ------------------------------------------------------------------ GitHub access


class Repo:
    """Minimal read-only view of a public repo folder."""

    def __init__(self, owner: str, repo: str, branch: str | None, path: str, fetch_text_fn=None, fetch_json_fn=None, fetch_bytes_fn=None):
        self.owner, self.repo, self.path = owner, repo, path
        self.fetch_text = fetch_text_fn or fetch_text
        self.fetch_json = fetch_json_fn or fetch_json
        if fetch_bytes_fn is None and fetch_text_fn is not None:        # an injected text fetcher (tests) also serves figures
            fetch_bytes_fn = lambda url, timeout=15, max_bytes=FIGURE_MAX_FILE: (lambda t: t if isinstance(t, bytes) else t.encode("utf-8"))(
                fetch_text_fn(url, timeout, max_bytes))
        self.fetch_bytes = fetch_bytes_fn or fetch_bytes
        self.warnings: list[str] = []
        self.bytes_read = 0
        self.read_log: list[dict] = []          # every file actually read: the submission audit lists them
        self._bust = int(time.time())
        self.branch = branch or self._default_branch()
        self.tree: list[str] | None = None

    def _default_branch(self) -> str:
        try:
            meta = self.fetch_json(f"https://api.github.com/repos/{self.owner}/{self.repo}")
            if isinstance(meta, dict) and meta.get("default_branch"):
                return str(meta["default_branch"])
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"default branch lookup failed ({type(exc).__name__}); trying main then master")
        for b in ("main", "master"):
            try:
                self.fetch_text(self.raw_url("README.md", b), max_bytes=4096)
                return b
            except Exception:  # noqa: BLE001
                continue
        return "main"

    def raw_url(self, rel: str, branch: str | None = None, bust: bool = True) -> str:
        """raw.githubusercontent.com caches a missing file (404) for minutes, so a file fetched right after a push or a
        visibility change may come back stale; a per-harvest query string sidesteps the cache. The bare base URL (rel "")
        is kept clean because it is stored as the paper's figure base."""
        prefix = (self.path + "/") if self.path else ""
        url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{branch or self.branch}/{urllib.parse.quote(prefix + rel)}"
        return url + f"?fd={self._bust}" if rel and bust else url

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def folder_url(self) -> str:
        return f"{self.repo_url}/tree/{self.branch}" + (f"/{self.path}" if self.path else "")

    def blob_url(self, rel: str) -> str:
        prefix = (self.path + "/") if self.path else ""
        return f"{self.repo_url}/blob/{self.branch}/{prefix}{rel}"

    def list_files(self) -> list[str]:
        if self.tree is not None:
            return self.tree
        try:
            data = self.fetch_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{urllib.parse.quote(self.branch)}?recursive=1")
            entries = data.get("tree", []) if isinstance(data, dict) else []
            if isinstance(data, dict) and data.get("truncated"):
                self.warnings.append("file listing truncated by GitHub (very large repository)")
            prefix = (self.path + "/") if self.path else ""
            files = [e["path"][len(prefix):] for e in entries if e.get("type") == "blob" and str(e.get("path", "")).startswith(prefix)]
            self.tree = files[:MAX_TREE_ENTRIES]
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"file listing unavailable ({type(exc).__name__}); probing known files only")
            self.tree = []
        return self.tree

    def read(self, rel: str, max_bytes: int = MAX_FILE_BYTES) -> str | None:
        if self.bytes_read > MAX_TOTAL_BYTES:
            self.warnings.append("read budget exhausted")
            return None
        if self.tree and rel not in self.tree:
            return None
        try:
            txt = self.fetch_text(self.raw_url(rel), max_bytes=max_bytes)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                self.warnings.append(f"{rel}: HTTP {exc.code}")
            return None
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"{rel}: {type(exc).__name__}")
            return None
        n = len(txt.encode("utf-8", "replace"))
        self.bytes_read += n
        self.read_log.append({"path": rel, "bytes": n})
        return txt

    def read_json(self, rel: str):
        txt = self.read(rel)
        if txt is None:
            return None
        try:
            return json.loads(txt)
        except ValueError:
            self.warnings.append(f"{rel}: invalid JSON")
            return None

    def readme(self) -> tuple[str | None, str | None]:
        names = [n for n in README_NAMES if not self.tree or n in self.tree] or README_NAMES[:1]
        for n in names:
            txt = self.read(n)
            if txt is not None:
                return n, txt
        return None, None


# ------------------------------------------------------------------ optional LLM


def llm_extract(readme: str, report_head: str, files: dict, vocab: dict, model: str | None = None,
                api_key: str | None = None, timeout: float = 30) -> dict | None:
    """One cheap zero-data-retention call to fill title/constructs/keywords/design. Never sees data."""
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    model = model or os.environ.get("FD_HARVEST_MODEL", "qwen/qwen3.5-27b")
    sys_prompt = ("You extract metadata for a research-replication archive index. Return ONLY JSON: "
                  '{"title": str, "constructs": [ids from ALLOWED], "keywords": [3-8 short strings], '
                  '"design_type": one of DESIGNS, "population": one of POPULATIONS or "unknown", "country": ISO2 or "unknown", '
                  '"summary": <=60 words stating what the study found or did}. Do not invent findings not in the text.')
    user = (f"ALLOWED: {', '.join(vocab.get('constructs', {}).keys())}\nDESIGNS: {vocab.get('designs')}\nPOPULATIONS: {vocab.get('populations')}\n\n"
            f"FILES: data={files.get('n_data')} ({', '.join(files.get('data_formats', []))}), scripts={files.get('languages')}\n\n"
            f"README:\n{readme[:3000]}\n\nREPORT (head):\n{report_head[:2000]}")
    body = {"model": model, "max_tokens": 400, "temperature": 0,
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}],
            "provider": {"data_collection": "deny"}}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                          "HTTP-Referer": "https://github.com/yrvelez/filedrawer", "X-Title": "filedrawer"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read(200_000).decode("utf-8"))
    content = data["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0)) if m else None


# ------------------------------------------------------------------ harvest


def harvest(url: str, *, fetch_text_fn=None, fetch_json_fn=None, fetch_bytes_fn=None, llm=None, use_llm: bool | None = None,
            keep_figures: bool = False) -> dict:
    """Return a slim record for a public GitHub repo/folder URL. Raises ValueError on a bad URL,
    RuntimeError when nothing readable is found (private or missing repo)."""
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("URL must look like https://github.com/<owner>/<repo>[/tree/<branch>/<folder>]")
    repo = Repo(parsed["owner"], parsed["repo"], parsed["branch"], parsed["path"], fetch_text_fn, fetch_json_fn, fetch_bytes_fn)
    return build_record(repo, record_id(repo.owner, repo.repo, repo.branch, repo.path), llm=llm, use_llm=use_llm,
                        keep_figures=keep_figures)


def build_record(repo: Repo, rid: str, *, llm=None, use_llm: bool | None = None, keep_figures: bool = False) -> dict:
    """The slim record for a GitHub repository or folder (see harvest)."""
    vocab = load_vocab()
    files = repo.list_files()
    inv = classify_files(files)
    rec: dict = {
        "record_version": RECORD_VERSION, "id": rid,
        "slug": (repo.path.rstrip("/").split("/")[-1] if repo.path else repo.repo).lower(),
        "title": None, "authors": [], "repo_url": repo.repo_url, "folder_url": repo.folder_url,
        "branch": repo.branch, "path": repo.path, "kind": "replication_archive", "source": "submitted",
        "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "links": {"folder": repo.folder_url, "report": None, "data": None, "readme": None, "study_json": None},
        "design": {"type": "unknown", "n_raw": None, "n_analysis": None}, "population": {"country": "unknown", "sample": "unknown"},
        "constructs": [], "keywords": [], "hypotheses": [], "release_status": "released", "synthetic": False,
        "provenance": {"mode": "unknown", "reviewer_pass": False, "tool_version": None, "models": {}},
        "files": inv, "readme_excerpt": "", "summary": "", "created": None,
        "paper": None,          # {"source": "report.md"|"README.md", "bytes": n, "sha256": ...}; text in _paper_text
        "extraction": {"method": None, "llm_used": False, "warnings": []},
    }
    found_any = False
    # 1) study.json
    sj = repo.read_json("study.json")
    if isinstance(sj, dict) and sj.get("title"):
        found_any = True
        rec["kind"] = "filedrawer_package"
        rec["extraction"]["method"] = "study_json"
        for k in ("slug", "title", "authors", "created", "synthetic", "constructs", "keywords", "hypotheses", "release_status", "registration", "extensions",
                  "badges", "potential", "review_claims", "review_round", "review_rounds"):
            if sj.get(k) is not None:
                rec[k] = sj[k]
        d = sj.get("design") or {}
        rec["design"] = {"type": d.get("type", "unknown"), "n_raw": d.get("n_raw"), "n_analysis": d.get("n_analysis"),
                         "sample_kind": d.get("sample_kind") or "human", "causal": d.get("causal"),
                         "diagram": d.get("diagram"), "diagram_text": d.get("diagram_text"), "features": d.get("features")}
        rec["population"] = sj.get("population") or rec["population"]
        p = sj.get("provenance") or {}
        rec["provenance"] = {"mode": p.get("mode", "unknown"), "reviewer_pass": bool(p.get("reviewer_pass")),
                             "tool_version": p.get("tool_version"), "models": p.get("models", {})}
        rec["links"]["study_json"] = repo.blob_url("study.json")
    # 2) package files (also fills gaps for study.json-less packages)
    report = repo.read("report.md")
    pap = repo.read_json("pap.json")
    if report is not None or isinstance(pap, dict):
        found_any = True
        if rec["extraction"]["method"] is None:
            rec["kind"] = "filedrawer_package"
            rec["extraction"]["method"] = "package_files"
    if report is not None:
        rec["links"]["report"] = repo.blob_url("report.md")
        if not rec["title"]:
            rec["title"] = first_heading(report)
        m = re.search(r"\*\*Provenance: (FULLY AGENTIC|HUMAN REVIEWED)", report)
        if m and rec["provenance"]["mode"] == "unknown":
            rec["provenance"]["mode"] = "fully_agentic" if m.group(1).startswith("FULLY") else "human_reviewed"
        if "Reviewer pass: yes" in report:
            rec["provenance"]["reviewer_pass"] = True
        if "SIMULATED DEMO DATA" in report or "SYNTHETIC DATA" in report:    # the demo banner (old and new wording)
            rec["synthetic"] = True
        if not rec["summary"]:
            sm = re.search(r"## (?:Abstract|Summary)\s+(.+?)\n## ", report, re.S)
            if sm:
                rec["summary"] = strip_markdown(sm.group(1))[:600]
    if isinstance(pap, dict):
        if not rec["constructs"]:
            rec["constructs"] = [c for c in pap.get("constructs", []) if isinstance(c, str)]
        if not rec["keywords"]:
            rec["keywords"] = [k for k in pap.get("keywords", []) if isinstance(k, str)]
        d = pap.get("design") or {}
        if rec["design"]["type"] == "unknown" and d.get("type"):
            rec["design"]["type"] = d["type"]
        if rec["population"]["sample"] == "unknown" and isinstance(d.get("population"), dict):
            rec["population"] = {"country": d["population"].get("country", "unknown"), "sample": d["population"].get("sample", "unknown")}
        if not rec["title"] and pap.get("title"):
            rec["title"] = pap["title"]
        if not rec["hypotheses"]:
            tags = {r.get("analysis_id"): r.get("tag") for r in _parse_csv(repo.read("results/analysis_tags.csv") or "")}
            summ = {r.get("analysis_id"): r for r in _parse_csv(repo.read("results/registered_summary.csv") or "") if not r.get("term")}
            for h in (pap.get("implemented") or {}).get("hypotheses", []):
                s = summ.get(h.get("id"), {})
                rec["hypotheses"].append({"id": h.get("id"), "text": h.get("text", ""), "tag": tags.get(h.get("id"), "unknown"),
                                          "estimate": _num(s.get("estimate")), "se": _num(s.get("std_error")), "p": _num(s.get("p_value")),
                                          "n": int(_num(s.get("n")) or 0) or None, "supported": (s.get("supported") == "True") if s else None})
            for aid, tag in tags.items():
                if tag == "exploratory" and aid not in {h["id"] for h in rec["hypotheses"]}:
                    rec["hypotheses"].append({"id": aid, "text": "", "tag": "exploratory", "estimate": None, "se": None, "p": None, "n": None, "supported": None})
    prov = repo.read_json("provenance/provenance.json") if rec["kind"] == "filedrawer_package" else None
    if isinstance(prov, dict):
        rec["provenance"].update({k: prov[k] for k in ("mode", "reviewer_pass", "tool_version", "models", "cost_usd", "tokens") if k in prov})
        rec["created"] = rec["created"] or (prov.get("created") or "")[:10] or None
    # 3) README
    rname, readme = repo.readme()
    if readme is not None:
        found_any = True
        rec["links"]["readme"] = repo.blob_url(rname)
        rec["readme_excerpt"] = strip_markdown(readme)[:600]
        if not rec["title"]:
            rec["title"] = first_heading(readme)
        if rec["extraction"]["method"] is None:
            rec["extraction"]["method"] = "readme"
    if not found_any and not files:
        raise RuntimeError("nothing readable at that URL: the repository may be private, empty, or the branch/folder may not exist")
    if not rec["title"]:
        rec["title"] = (repo.path.split("/")[-1] if repo.path else repo.repo).replace("-", " ").replace("_", " ")
    if rec["links"]["data"] is None:
        data_dirs = sorted({p.split("/")[0] for p in inv["data"] if "/" in p})
        rec["links"]["data"] = repo.blob_url(data_dirs[0]).replace("/blob/", "/tree/") if data_dirs else repo.folder_url
    rec["links"]["report"] = rec["links"]["report"] or rec["links"]["readme"] or repo.folder_url
    # 4) vocabulary matching on text
    text_for_vocab = " ".join(filter(None, [rec["title"], rec["readme_excerpt"], rec["summary"], " ".join(rec["keywords"])]))
    if not rec["constructs"]:
        rec["constructs"] = match_vocab(text_for_vocab, vocab)
    if not rec["keywords"]:
        rec["keywords"] = [c.replace("_", " ") for c in rec["constructs"]][:6]
    # 5) optional LLM gap-filling
    want_llm = use_llm if use_llm is not None else bool(os.environ.get("OPENROUTER_API_KEY"))
    thin = rec["kind"] == "replication_archive" or not rec["constructs"] or rec["design"]["type"] == "unknown"
    if want_llm and thin and (readme or report):
        try:
            fn = llm or llm_extract
            out = fn(readme or "", report or "", inv, vocab)
            if isinstance(out, dict):
                rec["extraction"]["llm_used"] = True
                allowed = set(vocab.get("constructs", {}))
                if out.get("constructs"):
                    rec["constructs"] = sorted(set(rec["constructs"]) | {c for c in out["constructs"] if c in allowed})
                if out.get("keywords") and not (isinstance(pap, dict) and pap.get("keywords")):
                    rec["keywords"] = [str(k) for k in out["keywords"]][:8]
                if rec["design"]["type"] == "unknown" and out.get("design_type") in vocab.get("designs", []):
                    rec["design"]["type"] = out["design_type"]
                if rec["population"]["sample"] == "unknown" and out.get("population") in vocab.get("populations", []):
                    rec["population"]["sample"] = out["population"]
                if rec["population"]["country"] == "unknown" and out.get("country"):
                    rec["population"]["country"] = str(out["country"])[:2].upper()
                if not rec["summary"] and out.get("summary"):
                    rec["summary"] = str(out["summary"])[:500]
                if out.get("title") and rec["extraction"]["method"] == "readme" and len(str(out["title"])) > 3:
                    rec["title"] = str(out["title"])[:200]
        except Exception as exc:  # noqa: BLE001
            repo.warnings.append(f"llm extraction failed ({type(exc).__name__})")
    if isinstance(sj, dict) and sj.get("title") and report is None:
        # a filedrawer package whose report could not be read: filing its README as the paper would publish the wrong
        # text (this happened when GitHub's CDN served a cached 404 for report.md right after a visibility change)
        raise RuntimeError("study.json was read but report.md was not: refusing to file the README as the paper. "
                           "If the repository was just made public or pushed, wait a few minutes and try again.")
    paper_src, paper_txt = ("report.md", report) if report is not None else ((rname, readme) if readme is not None else (None, None))
    if paper_txt:
        rec["paper"] = {"source": paper_src, "bytes": len(paper_txt.encode("utf-8", "replace")),
                        "sha256": hashlib.sha256(paper_txt.encode("utf-8", "replace")).hexdigest(),
                        "base_url": repo.raw_url("")}          # for resolving relative figure links
        rec["_paper_text"] = paper_txt          # in memory only for a GitHub submission: the server never stores it
        if keep_figures:                        # GitHub submissions: off, figures load straight from GitHub
            figs, info = capture_figures(repo, paper_txt)
            if figs:                               # base64 so the record stays JSON-serializable
                rec["_figures"] = {rel: base64.b64encode(data).decode("ascii") for rel, data in figs.items()}
            rec["figures"] = info
    rec["extraction"]["warnings"] = repo.warnings[:10]
    rec["extraction"]["bytes_read"] = repo.bytes_read
    rec["extraction"]["files_read"] = repo.read_log[:40]
    rec["title"] = str(rec["title"])[:300]
    return rec
