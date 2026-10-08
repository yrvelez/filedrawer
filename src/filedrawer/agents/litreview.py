"""Minimal, findings-focused literature note: 2-4 OpenAlex queries, ≤10 works, 1-2 paragraphs
citing only retrieved works. Degrades gracefully when the network is unavailable."""
from __future__ import annotations

import json
import re

from .base import make_agent, extract_json
from .. import openalex

SYSTEM_Q = """You write literature-search queries for a scholarly search engine. Given a study's constructs and hypotheses, return JSON with three query sets, each of short keyword queries: {"supporting": [2 queries for prior findings in the direction the hypotheses expect], "contesting": [2 queries for findings, critiques or theories that would predict a null or the opposite], "method": [1 query for the design or measurement approach]}. No prose."""

SYSTEM_W = """You write a short related-work note for an unpublished study report. You get the study's hypotheses and a list of retrieved works (title, authors, year, venue, DOI, abstract snippet), each tagged with the search set it came from: supporting, contesting, method, or citing (a work that cites one of the top results). Write two short paragraphs. First: what prior FINDINGS say about the study's hypotheses. Second: where the retrieved works disagree with each other or with the study's expectations, name the disagreement plainly, and say which side the study's design can speak to. Cite works as (FirstAuthorSurname et al., Year) or (Surname, Year). Cite ONLY works from the list; never add works from memory. Some retrieved works may be off-topic (another field that merely shares a keyword): never describe or cite them, and list their ids in "off_topic". If few or none are on-topic, write one sentence saying the search found little closely related work, and stop. Write in the third person about the literature only: never describe your own process or what a snippet or abstract lacks, and never mention, characterise or explain away a work you do not cite. Return JSON {"text": "...", "cited": ["doi or id", ...], "off_topic": ["doi or id", ...], "debate_hint": "one sentence naming the live disagreement the study bears on, or empty"}. No prose outside the JSON."""


PROCESS = re.compile(r"(?<![\w'])(I|I'm|I've|my|we|We)(?![\w'])|\bsnippets?\b|\babstracts? (?:was|were|is|are) (?:not )?(?:retrieved|available|missing)|"
                     r"\bno abstract\b|\b(?:off-topic|unrelated|irrelevant) (?:works?|results?|papers?)\b|\bdo(?:es)? not address\b|\bthe retrieved (?:works|list|set) (?:also )?(?:include|contain)")


def scrub(text: str) -> str:
    """Drop sentences that narrate the agent's own process or comment on works it is not using."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text.strip())
    kept = [s for s in parts if not PROCESS.search(s)]
    return " ".join(kept) if kept else ""


def _surnames(w: dict) -> set[str]:
    return {a.split()[-1].lower() for a in w.get("authors", []) if a}


def run(ctx: dict, fetcher=None) -> dict:
    cfg = ctx["cfg"]
    pap = ctx["pap"]
    if not cfg["litreview"].get("enabled", True):
        return {"text": "", "works": [], "queries": [], "degraded": "disabled"}
    hyps = "\n".join(f"- {h['id']}: {h['text']}" for h in pap["implemented"]["hypotheses"])
    ag = make_agent("litreview", ctx, SYSTEM_Q, None)
    ag.max_turns = 1
    r1 = ag.run(f"Title: {pap.get('title','')}\nConstructs: {pap.get('constructs')}\nKeywords: {pap.get('keywords')}\nHypotheses:\n{hyps}")
    q = extract_json(r1.content) or {}
    fallback = [" ".join(pap.get("keywords", [])[:4])]
    sets = {"supporting": [str(x) for x in (q.get("supporting") or q.get("queries") or [])][:2] or fallback,
            "contesting": [str(x) for x in (q.get("contesting") or [])][:2],
            "method": [str(x) for x in (q.get("method") or [])][:1]}
    queries = [x for qs in sets.values() for x in qs]
    mailto = cfg["litreview"].get("mailto") or ctx.get("git_email", "")
    max_works = cfg["litreview"].get("max_works", 14)
    fetch = fetcher or openalex.fetch_url
    works, err, seen = [], None, set()
    for stance, qs in sets.items():
        if not qs:
            continue
        got, e = openalex.search_many(qs, mailto=mailto, max_works=max(3, max_works // 3), fetcher=fetch, stance=stance, seen=seen)
        works += got
        err = err or e
    # works that cite the two most-cited results: where the finding is contested or extended
    for top in sorted(works, key=lambda w: -(w.get("cited_by") or 0))[:2]:
        try:
            for w in openalex.related(top["id"], "cites", mailto=mailto, per_page=3, fetcher=fetch):
                key = w["doi"] or w["id"]
                if key and key not in seen:
                    seen.add(key)
                    works.append(w)
        except Exception as e:                       # the citing lookup is a bonus; never lose the note
            err = err or f"{type(e).__name__}: {e}"[:200]
    works = works[:max_works]
    if not works:
        return {"text": "", "works": [], "queries": queries, "query_sets": sets, "degraded": f"literature search unavailable ({err or 'no results'})"}
    listing = "\n".join(f"[{i+1}] ({w.get('stance', 'prior_findings')}) {w['title']} — {', '.join(w['authors'])} ({w['year']}), {w['venue']}. "
                        f"DOI: {w['doi'] or w['id']}. {w['abstract'][:300]}" for i, w in enumerate(works))
    ag2 = make_agent("litreview", ctx, SYSTEM_W, None)
    ag2.max_turns = 1
    r2 = ag2.run(f"Hypotheses:\n{hyps}\n\nRetrieved works:\n{listing}")
    out = extract_json(r2.content)
    out = out if isinstance(out, dict) else {}      # a list or nothing: keep only the prose
    text = str(out.get("text") or "").strip()
    if not text:                                    # prose outside the JSON: keep the prose, never the JSON
        text = re.sub(r"```.*?```", "", r2.content or "", flags=re.S)
        cut = text.find('{"')
        text = (text[:cut] if cut >= 0 else text).strip()
    text = scrub(text)
    off = {str(x).lower() for x in (out.get("off_topic") or []) if x}
    if off:                                         # off-topic works never reach the report, the memo or the debates
        works = [w for w in works if str(w.get("doi") or "").lower() not in off and str(w.get("id") or "").lower() not in off] or works
    debate_hint = str(out.get("debate_hint") or "").strip()
    # strip citations whose surname does not belong to any retrieved work
    known = set().union(*[_surnames(w) for w in works]) if works else set()

    def _check(m):
        name = m.group(1).split()[0].lower().strip(",")
        return m.group(0) if name in known else "[citation removed: not in retrieved set]"
    text = re.sub(r"\(([A-Z][\w'-]+)(?: (?:&|and) [A-Z][\w'-]+)?(?: et al\.)?,? \d{4}[a-z]?\)", _check, text)
    text = re.sub(r"10\.\d{4,9}/[^\s)\]]+", lambda m: m.group(0) if any(m.group(0).lower().startswith(w['doi'].lower()) for w in works if w['doi']) else "[doi removed]", text)
    return {"text": text, "works": works, "queries": queries, "query_sets": sets, "debate_hint": debate_hint, "degraded": None}
