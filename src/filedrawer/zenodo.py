"""DOIs from Zenodo: `filedrawer release <study> --doi [--sandbox]`.

The package is archived on Zenodo and gets two DOIs: a version DOI for this exact archive and a concept DOI that
always resolves to the latest version (cite the concept DOI). A later release of the same package becomes a new
version under the same concept DOI.

Order of operations, so the archive carries its own DOI:
  1. create a deposition (or a new version of the last one) and read the DOI Zenodo reserves for it;
  2. write the DOI into zenodo.json, then re-render CITATION.cff, study.json and the report's "How to cite";
  3. zip the package (git-tracked files as they are on disk; inputs/, raw/, hidden files and the model log excluded;
     data/ only with --include-data and only files already committed to git), upload it;
  4. publish. Publishing is irreversible on Zenodo, so a real (non-sandbox) publish asks for confirmation unless --yes.

The sandbox (sandbox.zenodo.org) issues test DOIs that do not resolve; they are recorded in zenodo.json under
"sandbox" and never written into the citation files.

Tokens come from the environment (ZENODO_TOKEN, ZENODO_SANDBOX_TOKEN) or from ~/.zenodo.env (KEY=value lines).
Scopes needed: deposit:write and deposit:actions. Tokens are sent only in the Authorization header and never logged.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE = {"production": "https://zenodo.org", "sandbox": "https://sandbox.zenodo.org"}
RECORD_FILE = "zenodo.json"             # at the package root; author-facing, never cleared by a pipeline rerun
ENV_FILE = Path.home() / ".zenodo.env"
SITE = "https://filedrawer.org"
EXCLUDE_TOP = {"inputs", "raw", ".git", ".github", "__pycache__", ".mplconfig", ".filedrawer"}
DATA_TOP = "data"                       # respondent-level data: archived only with include_data, and only if committed to git
NEVER = ("provenance/llm_log",)         # the model request log stays local


class ZenodoError(RuntimeError):
    pass


def token(sandbox: bool) -> str:
    name = "ZENODO_SANDBOX_TOKEN" if sandbox else "ZENODO_TOKEN"
    val = os.environ.get(name, "").strip()
    if not val and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                val = v.strip().strip('"').strip("'")
    if not val:
        raise SystemExit(f"{name} is not set. Create a personal access token with scopes deposit:write and deposit:actions at "
                         f"{BASE['sandbox' if sandbox else 'production']}/account/settings/applications/tokens/new/ and export it "
                         f"or put {name}=... in {ENV_FILE}.")
    return val


class Client:
    """The Zenodo deposit API (the legacy REST endpoints, still served by Zenodo's InvenioRDM)."""

    def __init__(self, sandbox: bool, tok: str | None = None, timeout: int = 120):
        self.base = BASE["sandbox" if sandbox else "production"]
        self._tok = tok if tok is not None else token(sandbox)
        self.timeout = timeout

    def req(self, method: str, url: str, body=None, raw: bytes | None = None) -> dict:
        if not url.startswith("http"):
            url = self.base + url
        headers = {"Authorization": f"Bearer {self._tok}", "Accept": "application/json"}
        data = None
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
        r = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                txt = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            raise ZenodoError(f"Zenodo {method} {url.replace(self.base, '')}: HTTP {e.code}: {detail}") from None
        return json.loads(txt) if txt.strip() else {}


# ---------------------------------------------------------------- local record
def load_record(study: Path) -> dict:
    p = Path(study) / RECORD_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def save_record(study: Path, rec: dict) -> None:
    (Path(study) / RECORD_FILE).write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def published_doi(study: Path) -> dict | None:
    """The production DOIs of a package, for citations: {"doi", "concept_doi", "url"} or None."""
    p = load_record(study).get("production") or {}
    if p.get("concept_doi") or p.get("doi"):
        return {"doi": p.get("doi"), "concept_doi": p.get("concept_doi") or p.get("doi"), "url": p.get("url"),
                "published": bool(p.get("published"))}
    return None


# ---------------------------------------------------------------- metadata and archive
def _creator(name: str) -> dict:
    parts = name.strip().split()
    return {"name": f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name.strip()}


def metadata(sj: dict, site: str = SITE, community: str | None = None, version: str | None = None) -> dict:
    slug = sj.get("slug") or "study"
    prov = sj.get("provenance") or {}
    reg = sj.get("registration") or {}
    desc = [f"<p>{(sj.get('summary') or sj.get('abstract') or '').strip()}</p>" if (sj.get("summary") or sj.get("abstract")) else "",
            "<p>Unpublished study package filed in <a href=\"" + site + "\">The File Drawer</a>: the report, de-identified data, "
            "analysis scripts, the pre-analysis plan and full provenance. The report and its review were produced by an agentic "
            f"pipeline (filedrawer {prov.get('tool_version') or ''}; provenance: {str(prov.get('mode', 'unknown')).replace('_', ' ')}). "
            "Registered analyses follow the plan; robustness checks and exploratory analyses are labelled as such.</p>"]
    related = [{"identifier": f"{site}/#/paper/{slug}", "relation": "isIdenticalTo", "resource_type": "publication-report"}]
    repo = sj.get("repo_url") or (sj.get("links") or {}).get("folder")
    if repo:
        related.append({"identifier": repo, "relation": "isSupplementedBy", "resource_type": "software"})
    if reg.get("url"):
        related.append({"identifier": reg["url"], "relation": "references", "resource_type": "other"})
    md = {"title": sj.get("title") or slug, "upload_type": "publication", "publication_type": "report",
          "description": "".join(desc), "creators": [_creator(a) for a in (sj.get("authors") or []) if a] or [{"name": "Anonymous"}],
          "access_right": "open", "license": "cc-by-4.0", "keywords": [k for k in (sj.get("keywords") or []) if k][:20],
          "publication_date": dt.date.today().isoformat(), "version": version or dt.date.today().isoformat(),
          "related_identifiers": related,
          "notes": "Generated with filedrawer. Cite the concept DOI for the study; each re-run is archived as a new version."}
    if community:
        md["communities"] = [{"identifier": community}]
    return md


def _git_tracked(study: Path) -> set[str] | None:
    """Files git tracks in the package folder, or None when the folder is not inside a git repository."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=study, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {r for r in out.split("\0") if r}


def archive_files(study: Path, include_data: bool = False) -> list[Path]:
    """The files archived on Zenodo, where a published record can never be deleted.

    Git-tracked files as they are on disk (or every file when the folder is not a git repository), minus inputs/,
    raw/, hidden paths and the model request log. data/ (respondent-level data) is left out unless include_data is
    set, and even then only files already committed to the package's git repository ship: a folder outside git
    never archives data."""
    study = Path(study).resolve()
    tracked = _git_tracked(study)
    rels = tracked if tracked is not None else {p.relative_to(study).as_posix() for p in study.rglob("*") if p.is_file()}
    keep = []
    for r in sorted(set(rels) | {RECORD_FILE, "CITATION.cff", "study.json", "report.md"}):
        parts = Path(r).parts
        if not parts or parts[0] in EXCLUDE_TOP or any(x.startswith(".") for x in parts) or r.startswith(NEVER):
            continue
        if parts[0] == DATA_TOP and not (include_data and tracked is not None and r in tracked):
            continue
        if (study / r).is_file():
            keep.append(study / r)
    return keep


def build_zip(study: Path, include_data: bool = False) -> tuple[str, bytes, int]:
    study = Path(study).resolve()
    sj = json.loads((study / "study.json").read_text(encoding="utf-8"))
    files = archive_files(study, include_data=include_data)
    rels = [f.relative_to(study).as_posix() for f in files]
    leaked = [r for r in rels if r.startswith(NEVER) or Path(r).parts[0] in EXCLUDE_TOP
              or (Path(r).parts[0] == DATA_TOP and not include_data)]
    if leaked:                                   # last line of defence before an irreversible upload
        raise ZenodoError("refusing to archive files that must stay local: " + ", ".join(leaked[:10]))
    buf = io.BytesIO()
    root = sj.get("slug") or study.name
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f"{root}/{f.relative_to(study).as_posix()}")
    return f"{root}.zip", buf.getvalue(), len(files)


def fingerprint(study: Path) -> str:
    """A hash of the package's substance: data, results, scripts, figures (not badges), the plan, extension designs and
    the report text without its provenance line and citation block. Releasing, badges and DOIs do not change it; a new
    pipeline run or an edited report does. Used to avoid minting a new version for an unchanged package."""
    import hashlib
    import re
    study = Path(study).resolve()
    h = hashlib.sha256()
    globs = ("data/**/*", "results/**/*", "scripts/**/*", "figures/**/*", "extensions/*.json", "extensions/*.qsf", "pap.json")
    files = sorted({p for g in globs for p in study.glob(g) if p.is_file() and "badges" not in p.parts
                    and not any(x.startswith(".") or x == "__pycache__" for x in p.relative_to(study).parts)})
    for p in files:
        h.update(p.relative_to(study).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    rep = study / "report.md"
    if rep.exists():
        text = rep.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^> \*\*Provenance:.*$", "", text)
        text = re.sub(r"(?m)^<!-- fd:badges -->\n.*$", "", text)
        text = re.sub(r"(?s)\n### How to cite\n.*?(?=\n### |\Z)", "", text)
        h.update(b"report.md\0" + text.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------- the release
def _rerender(study: Path) -> None:
    """Re-write CITATION.cff, study.json and report.md from the saved package (no model calls)."""
    from .config import load_config
    from .address import load_context
    from .orchestrator import finalize_package
    ctx = load_context(study, load_config(overrides={"provider": "mock"}))
    finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)


def _confirm(msg: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(msg + " [y/N] ").strip().lower() in ("y", "yes")


def mint(study: Path, sandbox: bool = False, community: str | None = None, yes: bool = False, site: str = SITE,
         client: Client | None = None, rerender=_rerender, log=print, new_version: bool = False,
         include_data: bool = False) -> dict:
    """Archive the package on Zenodo and return {"doi", "concept_doi", "url", "sandbox", "published", "files"}.
    A package whose substance is unchanged since its last published version is not archived again unless new_version."""
    study = Path(study).resolve()
    target = "sandbox" if sandbox else "production"
    rec = load_record(study)
    entry = dict(rec.get(target) or {})
    if entry.get("published") and not new_version and entry.get("fingerprint") == fingerprint(study):
        log(f"zenodo: unchanged since {entry.get('doi')}; no new version (pass --new-version to archive anyway)")
        return {"doi": entry.get("doi"), "concept_doi": entry.get("concept_doi"), "url": entry.get("url"), "sandbox": sandbox,
                "published": True, "unchanged": True, "files": 0}
    c = client or Client(sandbox)
    sj = json.loads((study / "study.json").read_text(encoding="utf-8"))

    # 1. a draft: resume an unpublished one, else a new version of the last record, else a new deposition
    draft = None
    if entry.get("draft_id") and not entry.get("published"):
        try:
            draft = c.req("GET", f"/api/deposit/depositions/{entry['draft_id']}")
            log(f"zenodo: resuming draft {entry['draft_id']}")
        except ZenodoError:
            draft = None
    if draft is None and entry.get("record_id") and entry.get("published"):
        nv = c.req("POST", f"/api/deposit/depositions/{entry['record_id']}/actions/newversion")
        draft = c.req("GET", nv["links"]["latest_draft"])
        for f in c.req("GET", f"/api/deposit/depositions/{draft['id']}/files") or []:
            c.req("DELETE", f"/api/deposit/depositions/{draft['id']}/files/{f['id']}")
        log(f"zenodo: new version of record {entry['record_id']} (draft {draft['id']})")
    if draft is None:
        draft = c.req("POST", "/api/deposit/depositions", body={})
        log(f"zenodo: new deposition {draft['id']}")
    did = draft["id"]
    md = metadata(sj, site=site, community=community, version=None)
    md["prereserve_doi"] = True
    draft = c.req("PUT", f"/api/deposit/depositions/{did}", body={"metadata": md})
    doi = ((draft.get("metadata") or {}).get("prereserve_doi") or {}).get("doi")
    if not doi:
        raise ZenodoError("Zenodo did not reserve a DOI for the draft")
    concept = draft.get("conceptrecid")
    concept_doi = entry.get("concept_doi") or (f"{doi.split('/')[0]}/zenodo.{concept}" if concept else doi)
    entry.update({"draft_id": did, "doi": doi, "concept_doi": concept_doi, "published": False,
                  "url": f"{c.base}/records/{did}", "reserved_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
    rec[target] = entry
    save_record(study, rec)

    # 2. the DOI into the citation files (production only: sandbox DOIs do not resolve)
    if not sandbox:
        rerender(study)

    # 3. archive and upload
    name, blob, n = build_zip(study, include_data=include_data)
    bucket = (draft.get("links") or {}).get("bucket")
    if not bucket:
        raise ZenodoError("the draft has no file bucket")
    c.req("PUT", f"{bucket}/{name}", raw=blob)
    log(f"zenodo: uploaded {name} ({n} files, {len(blob) / 1e6:.1f} MB)")

    # 4. publish (irreversible for real DOIs)
    if not sandbox and not yes and not _confirm(f"Publish {doi} on Zenodo? Published records cannot be deleted."):
        log(f"zenodo: draft {did} left unpublished; re-run with --yes to publish it (the reserved DOI stays {doi}).")
        return {"doi": doi, "concept_doi": concept_doi, "url": entry["url"], "sandbox": sandbox, "published": False, "files": n}
    pub = c.req("POST", f"/api/deposit/depositions/{did}/actions/publish")
    entry.update({"record_id": pub.get("id", did), "doi": pub.get("doi") or doi, "concept_doi": pub.get("conceptdoi") or concept_doi,
                  "url": (pub.get("links") or {}).get("html") or (pub.get("links") or {}).get("record_html") or entry["url"],
                  "published": True, "published_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
    entry.pop("draft_id", None)
    entry.setdefault("versions", []).append({"doi": entry["doi"], "date": dt.date.today().isoformat()})
    rec[target] = entry
    save_record(study, rec)
    if not sandbox:
        rerender(study)                     # "published" flips the wording; the archive itself already carries the DOI
    rec = load_record(study)
    rec[target] = {**rec.get(target, {}), "fingerprint": fingerprint(study)}
    save_record(study, rec)
    log(f"zenodo: published {entry['doi']} (concept {entry['concept_doi']}) {entry['url']}")
    return {"doi": entry["doi"], "concept_doi": entry["concept_doi"], "url": entry["url"], "sandbox": sandbox, "published": True, "files": n}
