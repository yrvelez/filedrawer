"""Extensions: follow-up study designs proposed from a finished package, buildable as unpublished Qualtrics drafts.

Each proposal uses exactly autoexperiment's ExtensionProposal schema (vendored as vocab/extension_proposal.schema.json),
so the same design can be validated by autoexperiment's `designErrors` and built by its `buildSurvey`. filedrawer adds
two checks that need the study itself: reused questions must cite a real QSF question id, and evidence must come from
sources the package already knows (its own repository, its pre-registration, the literature note).

Files: extensions/<id>.json (one proposal each) and extensions/index.json (one row per proposal, plus build status).
Building is local only: `filedrawer build-extension <study> <id>` shells out to autoexperiment, which talks to the
Qualtrics MCP server with credentials that never leave the author's machine.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import PKG_ROOT

SCHEMA = json.loads((PKG_ROOT / "vocab" / "extension_proposal.schema.json").read_text(encoding="utf-8"))
KINDS = ("mechanism", "boundary", "alternative")
# How each proposal is labelled in the report and on the site: the research-potential brief types first, the legacy
# kinds for packages made without a memo, and the non-survey follow-ups that carry a plan instead of a QSF.
KIND_LABEL = {"advance_design": "Design advance", "theoretical_debate": "Theoretical debate", "generalizability_conditional": "Generalizability",
              "data_to_collect": "Data to collect", "natural_experiment": "Natural experiment",
              "mechanism": "Mechanism", "boundary": "Boundary condition", "alternative": "Alternative explanation",
              "observational": "Observational follow-up"}
# keys a proposal carries that are not part of autoexperiment's schema (kept out of validation, restored on save)
EXTRA_KEYS = ("addresses", "brief_id", "mode", "debate", "why", "power_target")
PLAN_FIELDS = ("data_sources", "units", "exposure", "outcome", "identification", "threats", "analysis_plan")


def kind_label(row_or_proposal: dict) -> str:
    return KIND_LABEL.get(row_or_proposal.get("brief_id") or "", "") or KIND_LABEL.get(row_or_proposal.get("kind") or "", str(row_or_proposal.get("kind") or ""))


def plan_errors(p: dict) -> list[str]:
    """Checks for a non-survey follow-up (kind "observational"): a plan, not an instrument."""
    errs = []
    if not isinstance(p, dict):
        return ["proposal is not an object"]
    for k in ("id", "title", "rationale", "design"):
        if not p.get(k):
            errs.append(f"missing {k}")
    if not ID_RE.match(str(p.get("id", ""))):
        errs.append("id must start with a letter and contain only letters, digits and underscores")
    d = p.get("design") or {}
    for k in PLAN_FIELDS:
        if not d.get(k):
            errs.append(f"design.{k} is missing or empty")
    return errs
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
BRIDGE = "scripts/filedrawer-extension.ts"


# ---------------------------------------------------------------- autoexperiment bridge
def autoexperiment_dir(cfg: dict | None = None) -> Path | None:
    cands = [os.environ.get("FD_AUTOEXPERIMENT_DIR", ""), ((cfg or {}).get("extensions") or {}).get("autoexperiment_dir", ""),
             str(Path.home() / "Projects" / "autoexperiment")]
    for c in cands:
        if c and (Path(c) / BRIDGE).exists():
            return Path(c)
    return None


def _bridge(cfg: dict | None, *args: str, timeout: int = 600) -> dict | None:
    d = autoexperiment_dir(cfg)
    node = shutil.which("node")
    if d is None or node is None:
        return None
    r = subprocess.run([node, "--import", "tsx", BRIDGE, *args], cwd=d, capture_output=True, text=True, timeout=timeout)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except ValueError:
        return {"ok": False, "errors": [f"bridge failed (exit {r.returncode}): {(r.stderr or r.stdout)[-600:]}"]}


# ---------------------------------------------------------------- checks
def _defs() -> dict:
    return SCHEMA.get("definitions", {}).get("ExtensionProposal", SCHEMA)


def _safe_id(x, prefix: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(x).strip()) or prefix
    return s if ID_RE.match(s) else f"{prefix}{s}"


def normalize(p: dict) -> dict:
    """Mechanical repairs of common model slips before validation, so a design is not lost (or a paid repair turn spent)
    over shape: matrix rows given as strings, choice/row ids that are not identifiers (e.g. "1"), missing empty lists,
    evidence without an origin. Choice ids renamed here are renamed in branch conditions too. Mutates and returns p."""
    des = p.get("design") if isinstance(p.get("design"), dict) else {}
    renamed: dict[tuple[str, str], str] = {}
    for b in des.get("blocks") or []:
        for q in (b.get("questions") or []) if isinstance(b, dict) else []:
            if not isinstance(q, dict):
                continue
            for key in ("choices", "rows"):
                items = q.get(key)
                if items is None:
                    q[key] = []
                    continue
                fixed = []
                for n, it in enumerate(items, 1):
                    if isinstance(it, str):
                        it = {"id": f"{key[0]}{n}", "text": it}
                        if key == "choices":
                            it["recode"] = str(n)
                    if isinstance(it, dict):
                        old = it.get("id", "")
                        new = _safe_id(old or f"{key[0]}{n}", key[0])
                        if new != old:
                            it["id"] = new
                            if key == "choices":
                                renamed[(q.get("id", ""), str(old))] = new
                        if key == "choices" and "recode" in it and not isinstance(it["recode"], str):
                            it["recode"] = str(it["recode"])
                    fixed.append(it)
                q[key] = fixed
    for node in des.get("flow") or []:
        if isinstance(node, dict) and (node.get("questionId", ""), str(node.get("choiceId", ""))) in renamed:
            node["choiceId"] = renamed[(node["questionId"], str(node["choiceId"]))]
    for e in des.get("evidence") or []:
        if isinstance(e, dict) and e.get("origin") not in ("web", "supplied"):
            e["origin"] = "web"
    return p


def structural_errors(p: dict) -> list[str]:
    """A light, dependency-free version of the schema checks (the full check is autoexperiment's)."""
    errs = []
    if not isinstance(p, dict):
        return ["proposal is not an object"]
    for k in _defs().get("required", []):
        if k not in p:
            errs.append(f"missing {k}")
    if errs:
        return errs
    if p.get("kind") not in KINDS:
        errs.append(f"kind must be one of {', '.join(KINDS)}")
    if not ID_RE.match(str(p.get("id", ""))):
        errs.append("id must start with a letter and contain only letters, digits and underscores")
    d = p.get("design") or {}
    for k in ("title", "overview", "hypothesis", "primaryOutcome", "comparison", "conditions", "blocks", "flow", "roots"):
        if not d.get(k):
            errs.append(f"design.{k} is missing or empty")
    if len(d.get("conditions") or []) < 2:
        errs.append("design.conditions needs at least two conditions")
    return errs


def source_errors(p: dict, qsf_ids: set[str], allowed_urls: set[str]) -> list[str]:
    errs = []
    for b in (p.get("design") or {}).get("blocks") or []:
        for q in b.get("questions") or []:
            sq = str(q.get("sourceQuestionId") or "").strip()
            if sq and sq not in qsf_ids:
                errs.append(f"{q.get('id')}: sourceQuestionId {sq!r} is not a question in the source survey")
    for e in (p.get("design") or {}).get("evidence") or []:
        if e.get("url") not in allowed_urls:
            errs.append(f"evidence {e.get('id')!r} cites {e.get('url')!r}, which is not one of the allowed sources")
    return errs


def validate(p: dict, qsf_ids: set[str], allowed_urls: set[str], cfg: dict | None = None, tmp_dir: Path | None = None) -> tuple[list[str], str]:
    """Returns (errors, validator) where validator is 'autoexperiment' when the full check ran, else 'structural'."""
    normalize(p)
    errs = structural_errors(p)
    if not errs:
        errs = source_errors(p, qsf_ids, allowed_urls)
    if errs:
        return errs, "structural"
    if autoexperiment_dir(cfg) and shutil.which("node"):
        tmp = (tmp_dir or Path.cwd()) / f".ext_check_{p.get('id', 'x')}.json"
        tmp.write_text(json.dumps(p), encoding="utf-8")
        try:
            r = _bridge(cfg, "validate", str(tmp.resolve()), timeout=120) or {}
        finally:
            tmp.unlink(missing_ok=True)
        return list(r.get("errors") or []), "autoexperiment"
    return [], "structural"


# ---------------------------------------------------------------- source material from a package
def source_questions(study: Path) -> list[dict]:
    cb_path = study / "codebook.json"
    if not cb_path.exists():
        return []
    cb = json.loads(cb_path.read_text(encoding="utf-8"))
    out = []
    for q in cb.get("questions", []):
        if q.get("free_text"):
            text_type = "TE"
        else:
            text_type = q.get("type", "")
        out.append({"id": q.get("qid"), "type": text_type, "tag": q.get("tag"), "block": q.get("block"),
                    "text": (q.get("text") or "")[:400],
                    "choices": [c.get("label") for c in (q.get("choices") or [])][:12],
                    "rows": [r.get("label") for r in (q.get("rows") or [])][:12]})
    return out


def allowed_sources(ctx: dict) -> list[dict]:
    meta, pap = ctx["meta"], ctx["pap"]
    src = []
    if meta.get("repo_url"):
        src.append({"url": meta["repo_url"], "title": f"{meta['title']} (study package: data, code and report)",
                    "claim": "the source study this extension builds on"})
    reg = (pap.get("registration") or {}).get("url")
    if reg:
        src.append({"url": reg, "title": "Pre-registration of the source study", "claim": "the registered design and outcomes"})
    for w in (ctx.get("lit") or {}).get("works", []) or []:
        url = f"https://doi.org/{w['doi']}" if w.get("doi") else w.get("id")
        if url and str(url).startswith("http"):
            src.append({"url": url, "title": w.get("title", ""), "claim": f"{', '.join(w.get('authors', [])[:3])} ({w.get('year')})"})
    return src


# ---------------------------------------------------------------- storage
def ext_dir(study: Path) -> Path:
    return study / "extensions"


def load_index(study: Path) -> list[dict]:
    p = ext_dir(study) / "index.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def write_qsfs(study: Path, proposals: list[dict], source_title: str = "") -> dict:
    """Write extensions/<id>.qsf for each survey proposal (no Qualtrics account needed). Returns {id: {"qsf", "errors", "media"}}.
    Non-survey follow-ups (kind "observational") get no QSF."""
    from . import qsfwrite as W
    out = {}
    for p in proposals:
        if p.get("kind") == "observational":
            out[p["id"]] = {"qsf": None, "qsf_errors": [], "media": []}
            continue
        path = ext_dir(study) / f"{p['id']}.qsf"
        errs, notes = W.write_qsf(p, path, source_title)
        out[p["id"]] = {"qsf": f"extensions/{p['id']}.qsf" if not errs else None, "qsf_errors": errs, "media": notes}
    return out


def save(study: Path, proposals: list[dict], validator: str, source_title: str = "") -> list[dict]:
    d = ext_dir(study)
    old = {r["id"]: r for r in load_index(study)}
    if d.exists():
        for f in list(d.glob("*.json")) + list(d.glob("*.qsf")) + list(d.glob("*.svg")) + list(d.glob("*.txt")):
            if f.name != "index.json":
                f.unlink()
    d.mkdir(exist_ok=True)
    extras = {p["id"]: {k: p.pop(k) for k in EXTRA_KEYS if k in p} for p in proposals}     # not part of the proposal schema
    for p in proposals:
        (d / f"{p['id']}.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    qsfs = write_qsfs(study, proposals, source_title)
    rows = []
    for p in proposals:
        des = p["design"]
        prev = old.get(p["id"], {})
        same = prev.get("title") == p["title"]
        ex = extras.get(p["id"], {})
        addresses = ex.get("addresses") if ex.get("addresses") is not None else prev.get("addresses", [])
        row = {**qsfs.get(p["id"], {}), "id": p["id"], "kind": p["kind"], "title": p["title"],
               "brief_id": ex.get("brief_id") or prev.get("brief_id"), "mode": ex.get("mode") or prev.get("mode"),
               "debate": ex.get("debate") or prev.get("debate"), "why": ex.get("why") or prev.get("why", ""),
               "power_target": ex.get("power_target") or prev.get("power_target"),
               "rationale": p.get("rationale", ""), "contribution": p.get("contribution", ""), "addresses": addresses,
               "validated_by": validator, "status": prev.get("status", "proposed") if same else "proposed",
               "built_at": prev.get("built_at") if same else None, "survey_id": prev.get("survey_id") if same else None,
               "survey_url": prev.get("survey_url") if same else None}
        if p.get("kind") == "observational":
            row.update({"hypothesis": des.get("hypothesis") or des.get("outcome", ""), "primary_outcome": des.get("outcome", ""),
                        "comparison": des.get("exposure", ""), "conditions": [], "unresolved": des.get("threats", []),
                        "plan": {k: des.get(k) for k in PLAN_FIELDS}})
        else:
            row.update({"hypothesis": des.get("hypothesis", ""), "primary_outcome": des.get("primaryOutcome", ""),
                        "comparison": des.get("comparison", ""), "conditions": [c.get("title") for c in des.get("conditions", [])],
                        "unresolved": des.get("unresolved", [])})
        row["label"] = kind_label(row)
        rows.append(row)
    (d / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return rows


def replace(study: Path, proposal: dict, validator: str, request: str, source_title: str = "") -> list[dict]:
    """Swap one proposal for its revision; keeps the others, resets the revised one's build status, logs the request."""
    d = ext_dir(study)
    (d / f"{proposal['id']}.json").write_text(json.dumps(proposal, indent=1, ensure_ascii=False), encoding="utf-8")
    current = [json.loads((d / f"{r['id']}.json").read_text(encoding="utf-8")) for r in load_index(study)
               if (d / f"{r['id']}.json").exists()]
    rows = save(study, current, validator, source_title)
    for r in rows:
        if r["id"] == proposal["id"]:
            r.update(status="proposed", built_at=None, survey_id=None, survey_url=None)
            r.setdefault("revisions", []).append({"date": dt.date.today().isoformat(), "request": request})
    old = {r["id"]: r for r in rows}
    (d / "index.json").write_text(json.dumps(list(old.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    return rows


def build(study: Path, ext_id: str, cfg: dict | None = None, dry_run: bool = False) -> dict:
    """Validate (and unless dry_run, build) one proposal as an unpublished Qualtrics draft through autoexperiment."""
    study = Path(study).resolve()
    f = ext_dir(study) / f"{ext_id}.json"
    if not f.exists():
        raise SystemExit(f"no extension {ext_id!r} in {study}/extensions (have: {', '.join(r['id'] for r in load_index(study)) or 'none'})")
    if autoexperiment_dir(cfg) is None or shutil.which("node") is None:
        raise SystemExit("building needs node and autoexperiment (set FD_AUTOEXPERIMENT_DIR or extensions.autoexperiment_dir in config.yaml).")
    if dry_run:
        r = _bridge(cfg, "validate", str(f), timeout=120) or {}
        return {"ok": bool(r.get("ok")), "errors": r.get("errors", []), "built": False}
    rec_dir = study / ".filedrawer" / "builds"
    rec_dir.mkdir(parents=True, exist_ok=True)
    gi = study / ".gitignore"
    if gi.exists() and ".filedrawer/" not in gi.read_text(encoding="utf-8"):
        with open(gi, "a", encoding="utf-8") as fh:
            fh.write("\n# local build records for Qualtrics drafts (survey ids, operation log)\n.filedrawer/\n")
    r = _bridge(cfg, "build", str(f), str(rec_dir / f"{ext_id}.json"), timeout=900) or {"ok": False, "errors": ["bridge unavailable"]}
    if r.get("ok"):
        rows = load_index(study)
        for row in rows:
            if row["id"] == ext_id:
                row.update(status="built", built_at=dt.date.today().isoformat(), survey_id=r.get("surveyId"), survey_url=r.get("surveyUrl"))
        (ext_dir(study) / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(r, built=bool(r.get("ok")))
