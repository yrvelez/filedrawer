"""Literature with a stance: supporting, contesting and method query sets, citing works for the top results, a
debate hint, and backward compatibility with the plain OpenAlex fixture."""
import json
from pathlib import Path

from filedrawer import openalex
from filedrawer.agents import litreview
from filedrawer.config import load_config
from filedrawer.llm.mock import MockProvider

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "tests" / "fixtures" / "mock"

PAP = {"title": "Demo", "constructs": ["immigration_attitudes"], "keywords": ["immigration", "framing"],
       "implemented": {"hypotheses": [{"id": "H1", "text": "The economic frame raises support."}]}}


def _ctx(fixtures_dir=None) -> dict:
    cfg = load_config(overrides={"provider": "mock"})
    return {"cfg": cfg, "provider": MockProvider(fixtures_dir or MOCK), "pap": PAP, "git_email": ""}


def _stance_fixtures(tmp_path: Path) -> Path:
    fx = tmp_path / "mock" / "litreview"
    fx.mkdir(parents=True)
    (fx / "0.json").write_text(json.dumps({"content": json.dumps({"supporting": ["economic frame immigration support"],
                                                                   "contesting": ["cultural threat immigration attitudes null framing"],
                                                                   "method": ["survey experiment framing"]})}))
    (fx / "1.json").write_text((MOCK / "litreview" / "1.json").read_text())
    return tmp_path / "mock"


def _openalex_fixture(tmp_path: Path) -> Path:
    base = json.loads((MOCK / "openalex.json").read_text())
    citing = {"results": [{"id": "https://openalex.org/W9", "title": "A critique of economic framing effects", "display_name": "A critique",
                           "authorships": [{"author": {"display_name": "Cy Critic"}}], "publication_year": 2023, "doi": "https://doi.org/10.0000/example.9",
                           "primary_location": {"source": {"display_name": "Journal of Replies"}}, "cited_by_count": 3, "abstract_inverted_index": None}]}
    p = tmp_path / "openalex_stance.json"
    p.write_text(json.dumps({"default": base, "by_substring": {"filter=cites": citing}}))
    return p


def test_query_sets_stances_and_citing_works(tmp_path):
    fetcher = openalex.MockFetcher(_openalex_fixture(tmp_path))
    out = litreview.run(_ctx(_stance_fixtures(tmp_path)), fetcher=fetcher)
    assert out["degraded"] is None
    assert set(out["query_sets"]) == {"supporting", "contesting", "method"} and len(out["queries"]) == 3
    stances = {w["stance"] for w in out["works"]}
    assert "supporting" in stances and "citing" in stances
    assert sum(1 for u in fetcher.calls if "filter=cites" in u) == 2          # the two most-cited results
    assert any(w["doi"] == "10.0000/example.9" for w in out["works"])
    assert "debate_hint" in out


def test_plain_fixture_and_legacy_queries_still_work():
    fetcher = openalex.MockFetcher(MOCK / "openalex.json")
    out = litreview.run(_ctx(), fetcher=fetcher)                              # fixture 0 returns the old {"queries": [...]}
    assert out["degraded"] is None and out["works"]
    assert out["query_sets"]["supporting"] and not out["query_sets"]["contesting"]
    assert all(w.get("stance") for w in out["works"])


def test_related_builds_the_cites_filter():
    seen = []
    works = openalex.related("https://openalex.org/W1", "cites", fetcher=lambda url, timeout=10: (seen.append(url), {"results": []})[1])
    assert works == [] and "filter=cites%3AW1" in seen[0]
    openalex.related("W1", "cited_by", fetcher=lambda url, timeout=10: (seen.append(url), {"results": []})[1])
    assert "filter=cited_by%3AW1" in seen[1]
