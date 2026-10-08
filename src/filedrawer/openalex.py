"""Minimal OpenAlex client (free, no key) with an injectable fetcher for tests."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BASE = "https://api.openalex.org/works"


def _abstract(inv: dict | None, max_chars: int = 400) -> str:
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)[:max_chars]


def _work(w: dict) -> dict:
    auths = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])][:4]
    return {"id": w.get("id", ""), "title": w.get("title") or w.get("display_name", ""), "authors": auths,
            "year": w.get("publication_year"), "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
            "cited_by": w.get("cited_by_count", 0), "abstract": _abstract(w.get("abstract_inverted_index"))}


def fetch_url(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "filedrawer/0.1 (mailto in query)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1_000_000).decode("utf-8"))


def search(query: str, mailto: str = "", per_page: int = 5, fetcher=fetch_url) -> list[dict]:
    params = {"search": query, "per-page": per_page, "sort": "cited_by_count:desc",
              "select": "id,title,display_name,authorships,publication_year,doi,primary_location,cited_by_count,abstract_inverted_index"}
    if mailto:
        params["mailto"] = mailto
    data = fetcher(BASE + "?" + urllib.parse.urlencode(params))
    return [_work(w) for w in data.get("results", [])]


def search_many(queries: list[str], mailto: str = "", max_works: int = 10, fetcher=fetch_url, stance: str | None = None,
                seen: set | None = None) -> tuple[list[dict], str | None]:
    """Run several queries, dedupe by DOI/id (across calls when `seen` is shared), cap at max_works. Each work is
    tagged with `stance` (the query set it came from) and its query. Returns (works, error)."""
    seen = seen if seen is not None else set()
    works, err = [], None
    for q in queries[:4]:
        try:
            for w in search(q, mailto=mailto, per_page=max(3, max_works // max(1, len(queries))), fetcher=fetcher):
                key = w["doi"] or w["id"]
                if key and key not in seen:
                    seen.add(key)
                    w["stance"], w["query"] = stance or "prior_findings", q
                    works.append(w)
        except Exception as e:  # network / parse
            err = f"{type(e).__name__}: {e}"[:200]
    return works[:max_works], err


def related(work_id: str, direction: str = "cites", mailto: str = "", per_page: int = 5, fetcher=fetch_url) -> list[dict]:
    """Works citing (`direction="cites"`: works whose references include it) or cited by (`"cited_by"`) an OpenAlex
    work. Citing works are where a finding gets contested or extended."""
    wid = work_id.rsplit("/", 1)[-1]
    params = {"filter": f"{'cites' if direction == 'cites' else 'cited_by'}:{wid}", "per-page": per_page, "sort": "cited_by_count:desc",
              "select": "id,title,display_name,authorships,publication_year,doi,primary_location,cited_by_count,abstract_inverted_index"}
    if mailto:
        params["mailto"] = mailto
    data = fetcher(BASE + "?" + urllib.parse.urlencode(params))
    out = []
    for w in data.get("results", []):
        d = _work(w)
        d["stance"], d["query"] = "citing" if direction == "cites" else "cited", f"{direction}:{wid}"
        out.append(d)
    return out


class MockFetcher:
    """Serves fixture JSON (OpenAlex shape). A plain results object answers every URL; a dict with "default" and
    "by_substring" {text: results} answers URLs containing `text` with that object (e.g. "filter=cites")."""

    def __init__(self, fixture_path):
        self.data = json.loads(open(fixture_path, encoding="utf-8").read())
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: int = 10) -> dict:
        self.calls.append(url)
        if isinstance(self.data, dict) and "by_substring" in self.data:
            for key, val in (self.data.get("by_substring") or {}).items():
                if key in url:
                    return val
            return self.data.get("default") or {"results": []}
        return self.data
