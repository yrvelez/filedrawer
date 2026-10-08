"""File Drawer dashboard server (stdlib only; runs on Fly).

Serves docs/ statically, builds /index.json from local studies + registry + harvested
records, accepts a bare GitHub URL at POST /submit (the server harvests a slim metadata
record itself) and hosts each record at /records/<id>.json. The server stores ONE thing per paper: that metadata
record. Data, code, the report text and figures are never stored; the report is fetched from GitHub when someone asks
for it, and figures load straight from GitHub. GET /audit lists everything on disk so anyone can check.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from filedrawer import index as fdindex  # noqa: E402
from filedrawer import harvest as fdharvest  # noqa: E402

# ------------------------------------------------------------------- settings

SETTINGS = {
    "studies_dir": ROOT / "studies",
    "docs_dir": ROOT / "docs",
    "data_dir": None,  # resolved by configure()
    "registry_path": ROOT / "docs" / "registry.json",
    "admin_token": os.environ.get("ADMIN_TOKEN", ""),
    "repo_url": os.environ.get("FD_REPO_URL", fdindex.DEFAULT_REPO_URL),
    "public_url": os.environ.get("FD_PUBLIC_URL", "").rstrip("/"),
    "include_drafts": os.environ.get("FD_INCLUDE_DRAFTS", "1") not in ("0", "false", "no", ""),
    "rate_limit": 5,
    "rate_window_s": 3600,
}

_STATE = {"index": None, "index_lock": threading.Lock(), "submissions": {}, "sub_lock": threading.Lock()}

GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?"
    r"(?:tree/(?P<branch>[A-Za-z0-9_.\-/]+?)(?:/(?P<path>[A-Za-z0-9_.\-/ %]*?))?/?)?$"
)
MAX_BODY = 4096

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".gif": "image/gif", ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8", ".webmanifest": "application/manifest+json",
}


def _resolve_data_dir(preferred) -> Path:
    cand = Path(preferred) if preferred else Path(os.environ.get("FD_DATA_DIR", "/data"))
    try:
        cand.mkdir(parents=True, exist_ok=True)
        if os.access(cand, os.W_OK):
            return cand
    except OSError:
        pass
    fallback = ROOT / "runs" / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def configure(studies_dir=None, docs_dir=None, data_dir=None, registry_path=None,
              admin_token=None, repo_url=None, include_drafts=None) -> dict:
    """Override module-level settings (used by tests and by main())."""
    if studies_dir is not None:
        SETTINGS["studies_dir"] = Path(studies_dir)
    if docs_dir is not None:
        SETTINGS["docs_dir"] = Path(docs_dir)
    if registry_path is not None:
        SETTINGS["registry_path"] = Path(registry_path)
    if admin_token is not None:
        SETTINGS["admin_token"] = admin_token
    if repo_url is not None:
        SETTINGS["repo_url"] = repo_url
    if include_drafts is not None:
        SETTINGS["include_drafts"] = bool(include_drafts)
    SETTINGS["data_dir"] = _resolve_data_dir(data_dir if data_dir is not None else SETTINGS["data_dir"])
    with _STATE["index_lock"]:
        _STATE["index"] = None
    return dict(SETTINGS)


# ------------------------------------------------------------ record storage


def records_dir() -> Path:
    if SETTINGS["data_dir"] is None:
        configure()
    d = Path(SETTINGS["data_dir"]) / "records"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_records() -> list[dict]:
    out = []
    for f in sorted(records_dir().glob("*.json")):
        if f.name.endswith(".prev.json"):          # the backup a refresh keeps, not a record
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    return out


def load_record(rid: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        return None
    f = records_dir() / f"{rid}.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write(f: Path, text: str) -> None:
    tmp = f.with_name(f.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, f)


RECORD_FILE_SUFFIXES = (".png", ".svg", ".txt")
_REL_OK = re.compile(r"^(figures|extensions)/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")


def record_file_dir(rid: str) -> Path:
    return records_dir() / rid


def _purge_copies(rid: str) -> int:
    """Remove copies kept by older versions of the server (paper text, figures). Returns how many files went."""
    import shutil
    n = 0
    for name in (f"{rid}.paper.md", f"{rid}.paper.prev.md"):
        f = records_dir() / name
        if f.is_file():
            f.unlink()
            n += 1
    d = record_file_dir(rid)
    if d.is_dir():
        n += sum(1 for x in d.rglob("*") if x.is_file())
        shutil.rmtree(d, ignore_errors=True)
    return n


def save_record(rec: dict) -> Path:
    """Write the metadata record and nothing else (the harvest's in-memory paper text and figures are dropped)."""
    rec = {k: v for k, v in rec.items() if not k.startswith("_")}
    if isinstance(rec.get("paper"), dict):
        paper = dict(rec["paper"])
        if str(paper.get("base_url", "")).startswith("/records/") and paper.get("github_base_url"):
            paper["base_url"] = paper["github_base_url"]          # legacy: figures were served from this server
        paper.pop("github_base_url", None)
        rec["paper"] = paper
        rec["paper_url"] = f"/records/{rec['id']}/paper.md"      # fetched from GitHub on request, never stored
    rec.pop("figures", None) if isinstance(rec.get("figures"), dict) and rec["figures"].get("stored") else None
    f = records_dir() / f"{rec['id']}.json"
    _atomic_write(f, json.dumps(rec, indent=1, ensure_ascii=False))
    _purge_copies(rec["id"])
    return f


def sweep_storage() -> list[str]:
    """Remove everything that is not a metadata record (<id>.json, <id>.prev.json): stored paper copies, figure folders
    and edit tokens left by older versions of the server. Run at start-up, so /audit stays true."""
    import shutil
    root = records_dir()
    if not root.is_dir():
        return []
    gone = []
    for p in sorted(root.iterdir()):
        if p.is_file() and re.fullmatch(r"[0-9a-f]{12}(\.prev)?\.json", p.name):
            continue
        gone.append(p.name)
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
    return gone


def storage_audit() -> dict:
    """Everything this server keeps on disk for submitted papers: file names and sizes, nothing else."""
    root = records_dir()
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
    rows = [{"file": p.relative_to(root).as_posix(), "bytes": p.stat().st_size} for p in files]
    other = [r for r in rows if not r["file"].endswith(".json")]
    return {"statement": ("This server stores one metadata record (JSON) per paper, plus the previous version of a record "
                          "after a refresh. It does not store data, code, report text or figures: reports are fetched from "
                          "GitHub on request and figures load from GitHub."),
            "records": sum(1 for r in rows if r["file"].endswith(".json") and not r["file"].endswith(".prev.json")),
            "files": rows, "total_bytes": sum(r["bytes"] for r in rows), "non_metadata_files": other,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def load_record_file(rid: str, rel: str) -> Path | None:
    """A stored figure of a record: /records/<id>/figures/x.png -> records/<id>/figures/x.png, with the path checked."""
    if not re.fullmatch(r"[0-9a-f]{12}", rid or "") or not _REL_OK.match(rel or "") or not rel.lower().endswith(RECORD_FILE_SUFFIXES):
        return None
    root = record_file_dir(rid).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


def backup_record(rid: str) -> None:
    """Keep the previous record and paper beside the live ones (<id>.prev.json, <id>.paper.prev.md) before a refresh."""
    for src, dst in ((f"{rid}.json", f"{rid}.prev.json"), (f"{rid}.paper.md", f"{rid}.paper.prev.md")):
        p = records_dir() / src
        if p.is_file():
            _atomic_write(records_dir() / dst, p.read_text(encoding="utf-8"))


def restore_record(rid: str) -> bool:
    """Put the previous record and paper back (the inverse of a refresh that went wrong)."""
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        return False
    prev = records_dir() / f"{rid}.prev.json"
    if not prev.is_file():
        return False
    _atomic_write(records_dir() / f"{rid}.json", prev.read_text(encoding="utf-8"))
    pp = records_dir() / f"{rid}.paper.prev.md"
    if pp.is_file():
        _atomic_write(records_dir() / f"{rid}.paper.md", pp.read_text(encoding="utf-8"))
    return True


def paper_source_url(rec: dict) -> str | None:
    paper = rec.get("paper") if isinstance(rec.get("paper"), dict) else None
    if not paper or not paper.get("source"):
        return None
    base = paper.get("github_base_url") or paper.get("base_url") or ""
    return base + paper["source"] if base.startswith("https://raw.githubusercontent.com/") else None


def load_paper(rid: str) -> str | None:
    """The report text, read from GitHub now (not stored here)."""
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        return None
    rec = load_record(rid)
    url = paper_source_url(rec or {})
    if not url:
        return None
    try:
        return fdharvest.fetch_text(url, max_bytes=fdharvest.MAX_FILE_BYTES)
    except Exception:  # noqa: BLE001 - private, moved or deleted repository
        return None


def delete_record(rid: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{12}", rid or ""):
        return False
    f = records_dir() / f"{rid}.json"
    ok = False
    if f.is_file():
        f.unlink()
        ok = True
    for name in (f"{rid}.paper.md", f"{rid}.prev.json", f"{rid}.paper.prev.md", f"{rid}.token"):
        pf = records_dir() / name
        if pf.is_file():
            pf.unlink()
    import shutil
    shutil.rmtree(record_file_dir(rid), ignore_errors=True)
    return ok


# --------------------------------------------------------------------- index


def build_index() -> dict:
    extra = []
    for rec in load_records():
        rec = dict(rec)
        rec["source"] = "submitted"
        rec["record_url"] = f"/records/{rec['id']}.json"
        rec["study_json_url"] = (rec.get("links") or {}).get("study_json") or rec["record_url"]
        extra.append(rec)
    idx = fdindex.build_index(SETTINGS["studies_dir"], SETTINGS["registry_path"],
                              include_drafts=SETTINGS["include_drafts"],
                              repo_url=SETTINGS["repo_url"], extra_entries=extra)
    for row in idx["studies"]:           # bundled studies: serve the paper and figures from this server
        if row.get("source") == "local" and (Path(SETTINGS["studies_dir"]) / row["slug"] / "report.md").is_file():
            row["paper_url"] = f"/studies/{row['slug']}/report.md"
            row["paper_base"] = f"/studies/{row['slug']}/"
    return idx


def _apa_name(n: str) -> str:
    t = n.strip().split()
    return n if len(t) < 2 else f"{t[-1]}, " + " ".join(g[0] + "." for g in t[:-1])


def citation_line(s: dict, base: str) -> str:
    yr = (s.get("created") or s.get("harvested_at") or "")[:4] or "n.d."
    au = [a for a in (s.get("authors") or []) if a] or ["Anonymous"]
    names = [_apa_name(a) for a in au]
    a = ", ".join(names[:-1]) + (", & " if len(names) > 1 else "") + names[-1]
    url = s.get("folder_url") or f"{base}/#/paper/{s.get('slug')}"
    return f"{a} ({yr}). {s.get('title') or s.get('slug')} [Unpublished study package]. The File Drawer. {url}"


def llms_txt(base: str = "") -> str:
    """A plain-text index for automated readers: what the site is, how to cite each paper, where the files are."""
    base = base or SETTINGS.get("public_url") or ""
    studies = get_index().get("studies", [])
    L = ["# The File Drawer", "",
         "> An online repository of unpublished studies and explorations that would otherwise never see the light of day. Each paper is a public GitHub repository with code, result tables and an AI-generated report whose provenance is recorded per paper; most also include de-identified respondent-level data (see \"Respondent data\" under each paper).",
         "", "Rules for automated readers and AI agents:",
         "- If you use a finding, number, dataset or script from a paper here, cite that paper with the citation line below it.",
         "- Reports are generated by a pipeline; the badge on each page says whether a human has reviewed it. Say so when you cite.",
         "- Analyses are tagged registered, deviation or exploratory; do not present an exploratory estimate as a registered one.",
         "- Each repository ships a CITATION.cff with the same metadata.",
         "- This site is a JavaScript app; read this file, /index.json and the GitHub repositories rather than the rendered pages.",
         "", "## Reanalyzing a paper", "",
         "1. Clone the repository listed under \"Data and code\".",
         "2. If respondent data are included, `data/clean.csv` is the analysis file (one row per respondent, or per profile/wave with a respondent id); `data/raw_tidy.csv` is the de-identified file before exclusions. Column meanings and value labels are in `codebook.md`; the analysis plan is `pap.md` / `pap.json`.",
         "3. `scripts/02_clean.py` builds `data/clean.csv`; `scripts/03_registered.py` and `scripts/04_exploratory.py` reproduce every table in `results/` (Python with pandas and statsmodels; `RUN.md` lists the steps).",
         "4. New analyses are exploratory relative to the plan: label them so, and do not report them as the study's registered results.",
         "5. Where respondent data are not included, only the result tables in `results/` are public; ask the authors (the contact in the README) for the data.",
         "", "## Papers", ""]
    for s in studies:
        L.append(f"- {s.get('title') or s.get('slug')}")
        L.append(f"  - Cite: {citation_line(s, base)}")
        if s.get("paper_url"):
            L.append(f"  - Report (markdown): {base}{s['paper_url']}" if str(s['paper_url']).startswith('/') else f"  - Report (markdown): {s['paper_url']}")
        if s.get("folder_url"):
            L.append(f"  - Data and code: {s['folder_url']}")
        data = next((b.get("value") for b in s.get("badges") or [] if isinstance(b, dict) and b.get("name") == "data"), None)
        if data:
            L.append("  - Respondent data: " + {"open data": "included (data/clean.csv)", "report only": "not public; result tables and code only",
                                                "synthetic": "synthetic respondents, included", "simulated demo": "simulated, included"}.get(data, data))
        if s.get("registration_url"):
            L.append(f"  - Pre-registration: {s['registration_url']}")
        L.append(f"  - Provenance: {s.get('provenance_mode', 'unknown').replace('_', ' ')}"
                 + ("; automated reviewer pass" if s.get("reviewer_pass") else "")
                 + (f"; model calls cost ${s['cost_usd']:.2f}" if s.get("cost_usd") is not None else ""))
    L += ["", f"Machine-readable index: {base}/index.json", ""]
    return "\n".join(L)


def get_index(force: bool = False) -> dict:
    with _STATE["index_lock"]:
        if force or _STATE["index"] is None:
            _STATE["index"] = build_index()
        return _STATE["index"]


# -------------------------------------------------------------- submissions


def parse_repo_url(url: str) -> dict | None:
    """Parse a GitHub repo/folder URL. Branch is None when the URL does not name one."""
    parsed = fdharvest.parse_github_url(url)
    if not parsed:
        return None
    parsed["folder_key"] = f"{parsed['owner']}/{parsed['repo']}/{parsed['branch'] or '*'}/{parsed['path']}".lower()
    return parsed


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _STATE["sub_lock"]:
        hist = [t for t in _STATE["submissions"].get(ip, []) if now - t < SETTINGS["rate_window_s"]]
        if len(hist) >= SETTINGS["rate_limit"]:
            _STATE["submissions"][ip] = hist
            return True
        hist.append(now)
        _STATE["submissions"][ip] = hist
    return False


def _find_existing(parsed: dict) -> dict | None:
    for rec in load_records():
        same_repo = rec.get("repo_url", "").lower() == f"https://github.com/{parsed['owner']}/{parsed['repo']}".lower()
        same_path = (rec.get("path") or "") == parsed["path"]
        same_branch = parsed["branch"] is None or rec.get("branch") == parsed["branch"]
        if same_repo and same_path and same_branch:
            return rec
    return None


def _in_local_index(parsed: dict) -> bool:
    folder = f"https://github.com/{parsed['owner']}/{parsed['repo']}/tree/{parsed['branch'] or 'main'}" + (f"/{parsed['path']}" if parsed["path"] else "")
    return any(s.get("source") != "submitted" and s.get("folder_url", "").rstrip("/").lower() == folder.lower()
               for s in get_index().get("studies", []))


def _harvest(url: str) -> dict:
    # the server never calls a model: a package carries its own metadata from the author's run
    return fdharvest.harvest(url, fetch_text_fn=fdharvest.fetch_text, fetch_json_fn=fdharvest.fetch_json, use_llm=False)


def submission_audit(parsed: dict, rec: dict, stored: bool) -> list[dict]:
    """What the submission did, step by step, for the Submit page and the record itself."""
    ex = rec.get("extraction") or {}
    read = ex.get("files_read") or []
    kb = lambda n: f"{n / 1024:.1f} KB"
    meta = {k: v for k, v in rec.items() if not k.startswith("_")}
    size = len(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    files = rec.get("files") or {}
    sc = rec.get("screening") or {}
    return [
        {"step": "Located the repository", "ok": True,
         "detail": f"github.com/{parsed.get('owner')}/{parsed.get('repo')}" + (f"/{parsed.get('path')}" if parsed.get("path") else "")
                   + " (public; the file list comes from GitHub's API, file names only)"},
        {"step": "Read small text files only", "ok": True,
         "detail": f"{len(read)} file(s), {kb(ex.get('bytes_read') or 0)} in all: "
                   + ", ".join(f"{r['path']} ({kb(r['bytes'])})" for r in read[:12]) + (" …" if len(read) > 12 else "")
                   + f". Not opened: {files.get('n_data', 0)} data file(s) and {files.get('n_scripts', 0)} script(s), counted by name."},
        {"step": "Screened for a research project", "ok": bool(sc.get("ok")),
         "detail": "; ".join(f"{'✓' if c['passed'] else ('✗' if c['required'] else '–')} {c['name']}: {c['detail']}" for c in sc.get("checks") or [])},
        {"step": "Extracted metadata", "ok": True,
         "detail": f"title, authors, topics, design, sample and links, by {ex.get('method') or 'readme'}"
                   + (" (a zero-retention model filled gaps)" if ex.get("llm_used") else " (no model call)")},
        {"step": "Stored" if stored else "Would store", "ok": True,
         "detail": f"one metadata record, {kb(size)}" + (f", at /records/{rec['id']}.json" if stored else "")
                   + ". Never stored: data, code, the report text and figures. They stay on GitHub and are read from there when someone opens the paper."},
    ]


def handle_submission(body: dict, ip: str) -> tuple[int, dict]:
    repo_url = body.get("repo_url") if isinstance(body, dict) else None
    if not isinstance(repo_url, str) or not repo_url.strip():
        return 400, {"ok": False, "error": "repo_url is required"}
    parsed = parse_repo_url(repo_url)
    if not parsed:
        return 400, {"ok": False, "error": "repo_url must look like https://github.com/<owner>/<repo>[/tree/<branch>/<folder>]"}
    existing = _find_existing(parsed)
    if existing:
        return 409, {"ok": False, "error": "already listed", "id": existing["id"], "record_url": f"/records/{existing['id']}.json",
                     "title": existing.get("title")}
    if _in_local_index(parsed):
        return 409, {"ok": False, "error": "already in the index (bundled study)"}
    if _rate_limited(ip):
        return 429, {"ok": False, "error": "too many submissions from this address; try again later"}
    try:
        rec = _harvest(repo_url.strip())
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)[:300]}
    except RuntimeError as exc:
        return 400, {"ok": False, "error": str(exc)[:300]}
    except Exception as exc:  # noqa: BLE001 - network or GitHub errors
        return 502, {"ok": False, "error": f"could not read the repository: {type(exc).__name__}: {exc}"[:400]}
    from filedrawer.screen import screen
    rec["screening"] = screen(rec)
    dry = bool(body.get("dry_run"))
    audit = submission_audit(parsed, rec, stored=False)
    if not rec["screening"]["ok"]:
        return 422, {"ok": False, "error": "This does not look like a research project, so nothing was stored. "
                     + " ".join(rec["screening"]["reasons"]), "screening": rec["screening"], "audit": audit}
    if dry:
        return 200, {"ok": True, "dry_run": True, "title": rec["title"], "kind": rec["kind"], "screening": rec["screening"],
                     "audit": audit, "message": "Checked. Nothing has been stored yet."}
    rec["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec["submitted_url"] = repo_url.strip()
    rec["audit"] = submission_audit(parsed, rec, stored=True)
    with _STATE["sub_lock"]:
        if load_record(rec["id"]):
            return 409, {"ok": False, "error": "already listed", "id": rec["id"], "record_url": f"/records/{rec['id']}.json"}
        save_record(rec)
    get_index(force=True)
    found = []
    if rec["kind"] == "filedrawer_package":
        found.append("filedrawer package")
    if rec["hypotheses"]:
        found.append(f"{len(rec['hypotheses'])} tagged analyses")
    f = rec["files"]
    if f.get("n_data"):
        found.append(f"{f['n_data']} data file(s) ({', '.join(f.get('data_formats', []))})")
    if f.get("languages"):
        found.append("scripts: " + ", ".join(f["languages"]))
    if rec["extraction"].get("llm_used"):
        found.append("metadata completed by a zero-retention model")
    similar = fdindex.similar_to(rec["slug"], get_index().get("studies", []), k=6)
    return 200, {"ok": True, "id": rec["id"], "slug": rec["slug"], "title": rec["title"], "kind": rec["kind"],
                 "record_url": f"/records/{rec['id']}.json", "paper_url": f"/records/{rec['id']}/paper.md" if rec.get("paper") else None,
                 "extraction": rec["extraction"], "similar": similar, "screening": rec["screening"], "audit": rec["audit"],
                 "message": f"Listed '{rec['title']}' ({rec['kind'].replace('_', ' ')}). Found: " + ("; ".join(found) or "README only") + "."}


def handle_refresh(rid: str) -> tuple[int, dict]:
    old = load_record(rid)
    if not old:
        return 404, {"ok": False, "error": "no such record"}
    try:
        rec = _harvest(old.get("submitted_url") or old["folder_url"])
    except Exception as exc:  # noqa: BLE001
        return 502, {"ok": False, "error": f"re-harvest failed: {type(exc).__name__}: {exc}"[:400]}
    rec["submitted_at"] = old.get("submitted_at")
    rec["submitted_url"] = old.get("submitted_url")
    rec["refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    from filedrawer.screen import screen
    rec["screening"] = screen(rec)
    parsed = parse_repo_url(rec["submitted_url"] or old["folder_url"]) or {}
    rec["audit"] = submission_audit(parsed, rec, stored=True)
    backup_record(rid)
    if rec["id"] != rid:
        delete_record(rid)
    save_record(rec)
    get_index(force=True)
    return 200, {"ok": True, "id": rec["id"], "title": rec["title"], "paper_source": (rec.get("paper") or {}).get("source"),
                 "screening_ok": rec["screening"]["ok"], "warnings": rec["extraction"].get("warnings", [])}


def handle_restore(rid: str) -> tuple[int, dict]:
    ok = restore_record(rid)
    if ok:
        get_index(force=True)
    return (200, {"ok": True, "id": rid}) if ok else (404, {"ok": False, "error": "no previous version of that record"})


# ------------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "filedrawer/" + fdindex.TOOL_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # one line per request to stdout
        sys.stdout.write("%s %s - %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.client_address[0], fmt % args))
        sys.stdout.flush()

    # -- helpers
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None, head_only=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, status: int, obj, extra=None, head_only=False):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", extra, head_only)

    def _static(self, path: str, head_only=False):
        docs = Path(SETTINGS["docs_dir"]).resolve()
        rel = unquote(path).lstrip("/") or "index.html"
        if "\x00" in rel or any(part in ("..", "") for part in rel.split("/")):
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        target = (docs / rel).resolve()
        if target.is_dir():
            target = target / "index.html"
        try:
            target.relative_to(docs)
        except ValueError:
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        if not target.is_file():
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "max-age=60"}, head_only)

    def _admin_ok(self, query: str) -> bool:
        token = parse_qs(query).get("token", [""])[0]
        admin = SETTINGS["admin_token"]
        return bool(admin) and token == admin

    def _qsf(self, rest: str, head_only=False):
        """/qsf/<record id>/<extension id>.qsf: stream the extension's survey file from the study's public repo as a download."""
        parts = rest.split("/")
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{12}", parts[0]) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", parts[1]):
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        rec = load_record(parts[0])
        loc = fdharvest.parse_github_url((rec or {}).get("folder_url") or "")
        if not rec or not loc:
            return self._json(404, {"ok": False, "error": "no such record"}, head_only=head_only)
        branch = loc.get("branch") or "main"
        prefix = (loc.get("path") + "/") if loc.get("path") else ""
        url = f"https://raw.githubusercontent.com/{loc['owner']}/{loc['repo']}/{branch}/{prefix}extensions/{parts[1]}.qsf"
        try:
            txt = fdharvest.fetch_text(url, timeout=15, max_bytes=4 * 1024 * 1024)
            body = json.loads(txt)
            if set(body) != {"SurveyEntry", "SurveyElements"}:
                raise ValueError("not a QSF")
        except Exception:
            return self._json(404, {"ok": False, "error": "survey file not found in the repository"}, head_only=head_only)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", (rec.get("slug") or parts[0]))[:60]
        return self._send(200, txt.encode("utf-8"), "application/octet-stream",
                          {"Content-Disposition": f'attachment; filename="{slug}-{parts[1]}.qsf"', "Cache-Control": "no-cache"}, head_only)

    def _study_file(self, rel: str, head_only=False):
        """Serve report.md, figures and tables of a bundled study (never provenance/ or scripts)."""
        root = Path(SETTINGS["studies_dir"]).resolve()
        rel = unquote(rel)
        parts = rel.split("/")
        if "\x00" in rel or len(parts) < 2 or any(p in ("..", "") for p in parts) or parts[1] in ("provenance", "scripts", "silicon"):
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        if not target.is_file() or target.suffix.lower() not in (".md", ".png", ".csv", ".json", ".svg", ".txt"):
            return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "max-age=300"}, head_only)

    def _client_ip(self) -> str:
        fwd = self.headers.get("Fly-Client-IP") or self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else "") or self.client_address[0]

    # -- routes
    def _route_get(self, head_only=False):
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8", head_only=head_only)
        if path == "/version":
            return self._json(200, {"sha": os.environ.get("FD_GIT_SHA", "unknown"), "built": os.environ.get("FD_BUILT", "")},
                              {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}, head_only)
        if path == "/llms.txt":
            return self._send(200, llms_txt().encode("utf-8"), "text/plain; charset=utf-8",
                              {"Cache-Control": "max-age=300", "Access-Control-Allow-Origin": "*"}, head_only)
        if path == "/index.json":
            body = json.dumps(get_index()).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8",
                              {"Cache-Control": "max-age=60", "Access-Control-Allow-Origin": "*"}, head_only)
        if path == "/records.json":
            recs = [{"id": r["id"], "title": r.get("title"), "kind": r.get("kind"), "folder_url": r.get("folder_url"),
                     "record_url": f"/records/{r['id']}.json", "submitted_at": r.get("submitted_at")} for r in load_records()]
            return self._json(200, {"records": recs}, {"Cache-Control": "max-age=60"}, head_only)
        if path.startswith("/similar/"):
            slug = unquote(path[len("/similar/"):]).strip("/")
            return self._json(200, {"slug": slug, "similar": fdindex.similar_to(slug, get_index().get("studies", []), k=8)},
                              {"Cache-Control": "max-age=60"}, head_only)
        if path.startswith("/qsf/") and path.endswith(".qsf"):
            return self._qsf(path[len("/qsf/"):-len(".qsf")], head_only)
        if path == "/audit":
            return self._json(200, storage_audit(), {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}, head_only)
        if path.startswith("/records/") and path.endswith("/paper.md"):
            txt = load_paper(path[len("/records/"):-len("/paper.md")])
            if txt is None:
                return self._json(404, {"ok": False, "error": "the report could not be read from GitHub (private, moved or deleted)"},
                                  head_only=head_only)
            return self._send(200, txt.encode("utf-8"), "text/markdown; charset=utf-8",
                              {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}, head_only)
        if path.startswith("/records/") and ("/figures/" in path or "/extensions/" in path):
            rid, _, rel = path[len("/records/"):].partition("/")          # old links: figures now live on GitHub only
            rec = load_record(rid) if re.fullmatch(r"[0-9a-f]{12}", rid or "") else None
            base = ((rec or {}).get("paper") or {}).get("base_url") or ""
            if not base.startswith("https://raw.githubusercontent.com/") or not _REL_OK.match(unquote(rel)):
                return self._json(404, {"ok": False, "error": "not found"}, head_only=head_only)
            return self._send(302, b"", "text/plain", {"Location": base + unquote(rel)}, head_only)
        if path.startswith("/studies/"):
            return self._study_file(path[len("/studies/"):], head_only)
        if path.startswith("/records/") and path.endswith(".json"):
            rec = load_record(path[len("/records/"):-len(".json")])
            if not rec:
                return self._json(404, {"ok": False, "error": "no such record"}, head_only=head_only)
            return self._json(200, rec, {"Cache-Control": "max-age=300", "Access-Control-Allow-Origin": "*"}, head_only)
        if path == "/admin/records":
            if not self._admin_ok(parts.query):
                return self._json(403, {"ok": False, "error": "forbidden"}, head_only=head_only)
            return self._json(200, load_records(), {"Cache-Control": "no-store"}, head_only)
        if path == "/submit":
            return self._send(204, b"", "text/plain", {"Allow": "POST, OPTIONS, HEAD"}, head_only=True)
        return self._static(path, head_only)

    def do_GET(self):
        self._route_get()

    def do_HEAD(self):
        self._route_get(head_only=True)

    def do_OPTIONS(self):
        if urlsplit(self.path).path == "/submit":
            return self._send(204, b"", "text/plain", {"Allow": "POST, OPTIONS, HEAD"}, head_only=True)
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path in ("/admin/refresh", "/admin/remove", "/admin/restore"):
            if not self._admin_ok(parts.query):
                return self._json(403, {"ok": False, "error": "forbidden"})
            rid = parse_qs(parts.query).get("id", [""])[0]
            if parts.path == "/admin/restore":
                status, obj = handle_restore(rid)
                return self._json(status, obj, {"Cache-Control": "no-store"})
            if parts.path == "/admin/remove":
                ok = delete_record(rid)
                if ok:
                    get_index(force=True)
                return self._json(200 if ok else 404, {"ok": ok, "id": rid}, {"Cache-Control": "no-store"})
            status, obj = handle_refresh(rid)
            return self._json(status, obj, {"Cache-Control": "no-store"})
        if parts.path != "/submit":
            return self._json(404, {"ok": False, "error": "not found"})
        cap = MAX_BODY
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > cap:
            return self._json(413 if length > cap else 400, {"ok": False, "error": f"body must be JSON up to {cap} bytes"})
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return self._json(400, {"ok": False, "error": "invalid JSON body"})
        status, obj = handle_submission(body, self._client_ip())
        self._json(status, obj, {"Cache-Control": "no-store"})


# ---------------------------------------------------------------------- main


def make_server(host: str = "0.0.0.0", port: int = 0) -> ThreadingHTTPServer:
    if SETTINGS["data_dir"] is None:
        configure()
    gone = sweep_storage()
    if gone:
        print(f"storage sweep: removed {len(gone)} non-metadata item(s): {', '.join(gone[:20])}", flush=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main() -> int:
    configure(data_dir=os.environ.get("FD_DATA_DIR", "/data"))
    port = int(os.environ.get("PORT", "8080"))
    srv = make_server("0.0.0.0", port)
    try:
        idx = get_index(force=True)
        print(f"index built: {len(idx['studies'])} studies, {len(idx['edges'])} edges", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"index build failed at startup: {exc!r}", flush=True)
    print(f"filedrawer server listening on {port}; data dir {SETTINGS['data_dir']}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
