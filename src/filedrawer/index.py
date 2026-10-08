"""Build the dashboard index (index.json) from local study packages and a registry.

Stdlib only: this module is also imported by server/app.py inside the Fly
container, which does not pip-install anything.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

TOOL_VERSION = "0.1.0"
DEFAULT_REPO_URL = "https://github.com/yrvelez/filedrawer"
DEFAULT_BRANCH = "main"
MIN_EDGE_WEIGHT = 0.15

# --------------------------------------------------------------------------- IO


def load_local_studies(studies_dir) -> list[dict]:
    """Read every ``<studies_dir>/*/study.json`` (sorted by folder name)."""
    root = Path(studies_dir)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        sj = folder / "study.json"
        if not sj.is_file():
            continue
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data.setdefault("slug", folder.name)
        data["_folder"] = str(folder)
        out.append(data)
    return out


def load_registry(path) -> list[dict]:
    """Read ``docs/registry.json``; returns the ``studies`` list (tolerant)."""
    if path is None:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        entries = data.get("studies", [])
    elif isinstance(data, list):
        entries = data
    else:
        entries = []
    return [e for e in entries if isinstance(e, dict)]


def fetch_json(url: str, timeout: float = 10, max_bytes: int = 262144) -> dict:
    """GET ``url`` and parse JSON (size-capped). Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": f"filedrawer/{TOOL_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"response larger than {max_bytes} bytes")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("study.json is not a JSON object")
    return data


# ------------------------------------------------------------------ normalising


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [x for x in v if x is not None]
    return [v]


def _str_list(v) -> list[str]:
    return [str(x).strip() for x in _as_list(v) if str(x).strip()]


def _norm_keyword(k: str) -> str:
    return k.strip().lower()


def _dget(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _derive_links(repo_url: str, branch: str, slug: str) -> dict:
    repo_url = (repo_url or DEFAULT_REPO_URL).rstrip("/")
    folder = f"{repo_url}/tree/{branch}/studies/{slug}"
    report = f"{repo_url}/blob/{branch}/studies/{slug}/report.md"
    data = f"{repo_url}/tree/{branch}/studies/{slug}/data"
    study_json = f"{repo_url}/blob/{branch}/studies/{slug}/study.json"
    if repo_url.startswith("https://github.com/"):
        tail = repo_url[len("https://github.com/"):]
        study_json = f"https://raw.githubusercontent.com/{tail}/{branch}/studies/{slug}/study.json"
    return {"folder": folder, "report": report, "data": data, "study_json": study_json}


def _hyp_counts(hyps) -> dict:
    counts = {"registered": 0, "unregistered": 0, "deviation": 0, "robustness": 0, "exploratory": 0, "supported": 0}
    for h in _as_list(hyps):
        if not isinstance(h, dict):
            continue
        tag = str(h.get("tag", "exploratory") or "exploratory").lower()
        if tag not in counts:
            tag = "exploratory"
        counts[tag] += 1
        if h.get("supported") is True:
            counts["supported"] += 1
    return counts


def _num(v, digits: int = 4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return float(f"{f:.{digits}g}") if f == f else None


def _tests(hyps) -> list[dict]:
    """One compact row per estimated test, for the cross-study results ledger: the hypothesis text (without
    the per-arm suffix), arm, outcome, estimate, SE, p, verdict and tag."""
    out = []
    for h in _as_list(hyps):
        if not isinstance(h, dict) or _num(h.get("estimate")) is None:
            continue
        hid = str(h.get("id") or "")
        out.append({"id": hid, "h": hid.split(":", 1)[0], "text": str(h.get("text") or "").split(" \u2014 ", 1)[0][:200],
                    "arm": str(h.get("arm_label") or (h.get("arm") if h.get("arm") not in (None, "pooled") else "") or
                               ("pooled" if h.get("arm") == "pooled" else "")),
                    "outcome": str(h.get("outcome") or ""), "estimate": _num(h.get("estimate")), "se": _num(h.get("se")),
                    "p": _num(h.get("p")), "supported": h.get("supported") if isinstance(h.get("supported"), bool) else None,
                    "tag": str(h.get("tag") or "exploratory").lower()})
    return out


def normalize_study(study: dict, source: str, repo_url: str = DEFAULT_REPO_URL,
                    branch: str = DEFAULT_BRANCH, study_json_url: str | None = None,
                    entry_repo_url: str | None = None) -> dict:
    """Flatten a study.json dict into one index row (tolerant of missing fields)."""
    study = study if isinstance(study, dict) else {}
    slug = str(study.get("slug") or "unknown")
    links = study.get("links") if isinstance(study.get("links"), dict) else {}
    derived = _derive_links(repo_url, branch, slug)
    design = study.get("design") if isinstance(study.get("design"), dict) else {}
    pop = study.get("population") if isinstance(study.get("population"), dict) else {}
    prov = study.get("provenance") if isinstance(study.get("provenance"), dict) else {}
    n = design.get("n_analysis")
    if n is None:
        n = design.get("n_raw")
    try:
        n = int(n) if n is not None else None
    except (TypeError, ValueError):
        n = None
    row = {
        "slug": slug,
        "title": str(study.get("title") or slug),
        "source": source,
        "repo_url": entry_repo_url or repo_url,
        "folder_url": links.get("folder") or derived["folder"],
        "report_url": links.get("report") or derived["report"],
        "data_url": links.get("data") or derived["data"],
        "study_json_url": study_json_url or links.get("study_json") or derived["study_json"],
        "release_status": str(study.get("release_status") or "unknown"),
        "registration_status": str(((study.get("registration") or {}).get("status")) or "unknown"),
        "extensions": [{k: e.get(k) for k in ("id", "kind", "label", "brief_id", "mode", "title", "hypothesis", "why", "debate", "status", "qsf", "svg", "text", "addresses")}
                       for e in (study.get("extensions") or []) if isinstance(e, dict)],
        "sample_kind": str(design.get("sample_kind") or "human"),
        "causal": design.get("causal"),
        "design_diagram": design.get("diagram"),
        "design_diagram_text": design.get("diagram_text"),
        "review_claims": (study.get("review_claims") if isinstance(study.get("review_claims"), dict) else None),
        "doi": study.get("doi") or None, "doi_version": study.get("doi_version") or None,
        "review_rounds": study.get("review_rounds"),
        "potential": (study.get("potential") if isinstance(study.get("potential"), dict) else None),
        "registration_url": ((study.get("registration") or {}).get("url")) or None,
        "provenance_mode": str(prov.get("mode") or "unknown"),
        "reviewer_pass": bool(prov.get("reviewer_pass", False)),
        "cost_usd": (round(float(prov["cost_usd"]), 3) if prov.get("cost_usd") not in (None, "") else None),
        "tokens_in": int((prov.get("tokens") or {}).get("in") or 0) or None,
        "tokens_out": int((prov.get("tokens") or {}).get("out") or 0) or None,
        "design_type": str(design.get("type") or "unknown"),
        "population": str(pop.get("sample") or "unknown"),
        "country": str(pop.get("country") or "unknown"),
        "constructs": sorted(set(_str_list(study.get("constructs")))),
        "keywords": _str_list(study.get("keywords")),
        "n": n,
        "created": str(study.get("created") or "unknown"),
        "hypotheses": _hyp_counts(study.get("hypotheses")),
        "tests": _tests(study.get("hypotheses")),
        "synthetic": bool(study.get("synthetic", False)),
        "authors": _str_list(study.get("authors")),
        "kind": str(study.get("kind") or "filedrawer_package"),
    }
    for k in ("files", "readme_excerpt", "summary", "extraction", "record_url", "harvested_at", "id", "paper_url", "badges"):
        if study.get(k) not in (None, "", [], {}):
            row[k] = study[k]
    if "badges" not in row:                      # packages and archives filed before badges existed, and thin archives
        from .badges import badges_for
        row["badges"] = badges_for({**study, **{k: row[k] for k in ("provenance_mode", "reviewer_pass", "registration_status", "registration_url",
                                                                     "release_status", "design_type", "sample_kind", "cost_usd", "synthetic") if k in row}})
    paper = study.get("paper") if isinstance(study.get("paper"), dict) else None
    if paper and paper.get("base_url"):
        row["paper_base"] = paper["base_url"]
        row["paper_source"] = paper.get("source")
    if "paper_url" not in row and source == "local":
        raw = row["study_json_url"].rsplit("/", 1)[0] + "/" if row["study_json_url"].startswith("https://raw.githubusercontent.com/") else None
        if raw:
            row["paper_url"] = raw + "report.md"
            row["paper_base"] = raw
    if isinstance(links.get("readme"), str):
        row["readme_url"] = links["readme"]
    if source == "uploaded":
        # filed from the author's machine: no GitHub folder exists, so none is derived, and no data was sent
        row.update({"repo_url": None, "folder_url": None, "data_url": None,
                    "report_url": links.get("report"), "study_json_url": links.get("study_json") or row.get("record_url")})
        if isinstance(links.get("files"), str):
            row["files_url"] = links["files"]
        sub = study.get("submission") if isinstance(study.get("submission"), dict) else {}
        client = sub.get("client") if isinstance(sub.get("client"), dict) else {}
        row["submission"] = {"method": sub.get("method"), "n_files": sub.get("n_files"), "server_processing": sub.get("server_processing"),
                             "acknowledged": sub.get("acknowledged") or [], "tool_version": client.get("tool_version"),
                             "commit": client.get("commit"), "modified": client.get("modified"), "source_url": client.get("source_url"),
                             "selfcheck_passed": (client.get("selfcheck") or {}).get("passed")}
        row["badges"] = [({**b, "value": "report only", "color": "slate"} if isinstance(b, dict) and b.get("name") == "data" else b)
                         for b in row.get("badges") or []]
    return row


# ----------------------------------------------------------------------- edges


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def compute_edges(rows: list[dict]) -> list[dict]:
    edges: list[dict] = []
    eligible = sorted((r for r in rows if not r.get("fetch_error")), key=lambda r: r["slug"])
    for a, b in itertools.combinations(eligible, 2):
        weight, reasons = pair_weight(a, b)
        if weight < MIN_EDGE_WEIGHT:
            continue
        edges.append({"source": a["slug"], "target": b["slug"], "weight": weight, "reasons": reasons})
    return edges


def pair_weight(a: dict, b: dict) -> tuple[float, list[str]]:
    ca, cb = set(a.get("constructs", [])), set(b.get("constructs", []))
    ka = {_norm_keyword(k) for k in a.get("keywords", [])}
    kb = {_norm_keyword(k) for k in b.get("keywords", [])}
    same_design = a.get("design_type") == b.get("design_type") and a.get("design_type") not in (None, "unknown")
    same_pop = a.get("population") == b.get("population") and a.get("population") not in (None, "unknown")
    weight = round(0.5 * _jaccard(ca, cb) + 0.2 * (1 if same_design else 0) + 0.1 * (1 if same_pop else 0) + 0.2 * _jaccard(ka, kb), 3)
    reasons = [f"construct:{c}" for c in sorted(ca & cb)]
    if same_design:
        reasons.append(f"design:{a['design_type']}")
    if same_pop:
        reasons.append(f"population:{a['population']}")
    reasons += [f"keyword:{k}" for k in sorted(ka & kb)]
    return weight, reasons


def similar_to(slug: str, rows: list[dict], k: int = 5, min_weight: float = 0.05) -> list[dict]:
    """Top-k most similar rows to the row with `slug` (no threshold beyond min_weight)."""
    me = next((r for r in rows if r.get("slug") == slug), None)
    if me is None:
        return []
    out = []
    for r in rows:
        if r is me or r.get("fetch_error"):
            continue
        w, reasons = pair_weight(me, r)
        if w >= min_weight:
            out.append({"slug": r["slug"], "title": r.get("title"), "weight": w, "reasons": reasons,
                        "kind": r.get("kind"), "authors": r.get("authors", []), "created": r.get("created"),
                        "paper_url": r.get("paper_url"), "folder_url": r.get("folder_url"), "n": r.get("n")})
    out.sort(key=lambda x: (-x["weight"], x["slug"]))
    return out[:k]


# ----------------------------------------------------------------------- build


def build_index(studies_dir, registry_path=None, include_drafts: bool = False,
                repo_url: str = DEFAULT_REPO_URL, branch: str = DEFAULT_BRANCH,
                extra_entries: list[dict] | None = None,
                fetcher: Callable[[str], dict] = fetch_json) -> dict:
    rows: list[dict] = []

    def keep(row: dict) -> bool:
        return include_drafts or row["release_status"] == "released"

    for study in load_local_studies(studies_dir):
        row = normalize_study(study, "local", repo_url, branch)
        if keep(row):
            rows.append(row)

    for entry in load_registry(registry_path):
        url = entry.get("study_json_url")
        entry_repo = entry.get("repo_url")
        if not url:
            continue
        try:
            data = fetcher(url)
            if not isinstance(data, dict):
                raise ValueError("study.json is not an object")
        except Exception as exc:  # noqa: BLE001 - any failure is reported, not fatal
            parts = [x for x in url.split("/") if x]
            slug = entry.get("slug") or (parts[-2] if len(parts) >= 2 else url)
            row = normalize_study({"slug": slug, "title": entry.get("title") or slug},
                                  "registry", repo_url, branch, study_json_url=url,
                                  entry_repo_url=entry_repo)
            row["fetch_error"] = f"{type(exc).__name__}: {exc}"[:300]
            rows.append(row)
            continue
        row = normalize_study(data, "registry", repo_url, branch, study_json_url=url,
                              entry_repo_url=entry_repo)
        if keep(row):
            rows.append(row)

    for extra in extra_entries or []:
        if not isinstance(extra, dict):
            continue
        source = str(extra.get("source") or "submitted")
        row = normalize_study(extra, source, repo_url, branch,
                              study_json_url=extra.get("study_json_url"),
                              entry_repo_url=extra.get("repo_url"))
        if extra.get("fetch_error"):
            row["fetch_error"] = str(extra["fetch_error"])[:300]
        if keep(row) or row.get("fetch_error"):
            rows.append(row)

    rows.sort(key=lambda r: (r["slug"], r["source"]))
    return {
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool_version": TOOL_VERSION,
        "studies": rows,
        "edges": compute_edges(rows),
    }


def write_index(index: dict, out_path) -> Path:
    p = Path(out_path)
    if p.is_dir() or str(out_path).endswith(os.sep):
        p.mkdir(parents=True, exist_ok=True)
        p = p / "index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


# ------------------------------------------------------------------------- CLI


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="filedrawer index",
                                 description="Build docs/index.json for the dashboard.")
    ap.add_argument("--studies", default="studies", help="directory of study packages")
    ap.add_argument("--registry", default="docs/registry.json", help="registry of external studies")
    ap.add_argument("--out", default="docs", help="output file or directory (-> index.json)")
    ap.add_argument("--include-drafts", action="store_true", help="include non-released studies")
    ap.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    args = ap.parse_args(argv)
    index = build_index(args.studies, args.registry, include_drafts=args.include_drafts,
                        repo_url=args.repo_url, branch=args.branch)
    out = write_index(index, args.out)
    print(f"wrote {out}: {len(index['studies'])} studies, {len(index['edges'])} edges")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
