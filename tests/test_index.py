import json

import pytest

from filedrawer import index as fdindex


def _study(slug, status, constructs, keywords, design="survey_experiment", sample="online_panel", n=300):
    return {
        "schema_version": 1, "slug": slug, "title": f"Study {slug}", "authors": ["A"],
        "created": "2026-10-01", "synthetic": True,
        "design": {"type": design, "arms": ["c", "t"], "n_raw": n + 10, "n_analysis": n, "outcome_type": "likert7"},
        "population": {"country": "US", "sample": sample},
        "constructs": constructs, "keywords": keywords,
        "hypotheses": [
            {"id": "H1", "text": "x", "tag": "registered", "estimate": 0.5, "se": 0.1, "p": 0.001, "n": n, "supported": True},
            {"id": "H2", "text": "y", "tag": "deviation", "estimate": 0.1, "se": 0.1, "p": 0.3, "n": n, "supported": False},
            {"id": "E1", "text": "z", "tag": "exploratory", "estimate": 0.2, "se": 0.1, "p": 0.04, "n": n, "supported": True},
        ],
        "release_status": status,
        "provenance": {"mode": "fully_agentic", "reviewer_pass": False},
    }


@pytest.fixture
def world(tmp_path):
    studies = tmp_path / "studies"
    released = _study("alpha-study", "released", ["immigration_attitudes", "framing_effects"],
                      ["Immigration", "framing"])
    draft = _study("beta-draft", "draft", ["immigration_attitudes", "partisanship"],
                   ["immigration ", "partisanship"])
    for s in (released, draft):
        d = studies / s["slug"]
        d.mkdir(parents=True)
        (d / "study.json").write_text(json.dumps(s))
    # a draft folder without study.json must be ignored
    (studies / "junk").mkdir()

    remote = _study("gamma-remote", "released", ["framing_effects", "trust"], ["framing", "trust"],
                    design="observational", sample="students")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"studies": [
        {"repo_url": "https://github.com/x/y", "study_json_url": "https://raw.githubusercontent.com/x/y/main/study.json"},
        {"repo_url": "https://github.com/x/broken", "study_json_url": "https://raw.githubusercontent.com/x/broken/main/studies/broken-one/study.json"},
    ]}))

    calls = []

    def fetcher(url):
        calls.append(url)
        if "broken" in url:
            raise OSError("connection refused")
        return remote

    return {"studies": studies, "registry": registry, "fetcher": fetcher, "calls": calls}


def test_drafts_skipped_unless_included(world):
    idx = fdindex.build_index(world["studies"], world["registry"], fetcher=world["fetcher"])
    slugs = {s["slug"] for s in idx["studies"]}
    assert "alpha-study" in slugs and "gamma-remote" in slugs
    assert "beta-draft" not in slugs
    idx2 = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    assert "beta-draft" in {s["slug"] for s in idx2["studies"]}
    assert set(world["calls"]) == {
        "https://raw.githubusercontent.com/x/y/main/study.json",
        "https://raw.githubusercontent.com/x/broken/main/studies/broken-one/study.json",
    }


def test_rows_and_links(world):
    idx = fdindex.build_index(world["studies"], world["registry"], include_drafts=True,
                              fetcher=world["fetcher"], repo_url="https://github.com/me/fd", branch="dev")
    rows = {s["slug"]: s for s in idx["studies"]}
    a = rows["alpha-study"]
    assert a["source"] == "local"
    assert a["folder_url"] == "https://github.com/me/fd/tree/dev/studies/alpha-study"
    assert a["report_url"] == "https://github.com/me/fd/blob/dev/studies/alpha-study/report.md"
    assert a["study_json_url"] == "https://raw.githubusercontent.com/me/fd/dev/studies/alpha-study/study.json"
    assert a["n"] == 300 and a["design_type"] == "survey_experiment" and a["population"] == "online_panel"
    assert a["hypotheses"] == {"registered": 1, "unregistered": 0, "deviation": 1, "robustness": 0, "exploratory": 1, "supported": 2}
    assert a["provenance_mode"] == "fully_agentic" and a["reviewer_pass"] is False and a["synthetic"] is True
    g = rows["gamma-remote"]
    assert g["source"] == "registry" and g["repo_url"] == "https://github.com/x/y"
    assert g["study_json_url"] == "https://raw.githubusercontent.com/x/y/main/study.json"
    assert idx["tool_version"] == "0.1.0" and idx["built"].endswith("Z")


def test_edge_weight_and_reasons(world):
    idx = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    edges = {(e["source"], e["target"]): e for e in idx["edges"]}
    # alpha vs beta: constructs J = 1/3, same design, same population,
    # keywords (case/whitespace-normalised) {immigration, framing} vs {immigration, partisanship} J = 1/3
    expected = round(0.5 * (1 / 3) + 0.2 + 0.1 + 0.2 * (1 / 3), 3)  # 0.533
    e = edges[("alpha-study", "beta-draft")]
    assert e["weight"] == expected == 0.533
    assert e["reasons"] == ["construct:immigration_attitudes", "design:survey_experiment",
                            "population:online_panel", "keyword:immigration"]
    # alpha vs gamma: constructs J = 1/3, different design and population, keywords J = 1/3
    e2 = edges[("alpha-study", "gamma-remote")]
    assert e2["weight"] == round(0.5 / 3 + 0.2 / 3, 3) == 0.233
    assert e2["reasons"] == ["construct:framing_effects", "keyword:framing"]
    # beta vs gamma: no overlap at all -> below 0.15, no edge
    assert ("beta-draft", "gamma-remote") not in edges
    for src, tgt in edges:
        assert src < tgt


def test_fetch_error_entry_present_but_isolated(world):
    idx = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    broken = [s for s in idx["studies"] if s.get("fetch_error")]
    assert len(broken) == 1
    b = broken[0]
    assert b["slug"] == "broken-one" and b["source"] == "registry"
    assert "OSError" in b["fetch_error"]
    assert b["constructs"] == [] and b["release_status"] == "unknown"
    assert all("broken-one" not in (e["source"], e["target"]) for e in idx["edges"])


def test_extra_entries_submitted(world):
    extra = _study("delta-sub", "released", ["immigration_attitudes"], ["immigration"])
    extra["source"] = "submitted"
    extra["study_json_url"] = "https://raw.githubusercontent.com/q/r/main/study.json"
    idx = fdindex.build_index(world["studies"], None, fetcher=world["fetcher"], extra_entries=[extra])
    rows = {s["slug"]: s for s in idx["studies"]}
    assert rows["delta-sub"]["source"] == "submitted"
    assert rows["delta-sub"]["study_json_url"] == extra["study_json_url"]
    assert any({e["source"], e["target"]} == {"alpha-study", "delta-sub"} for e in idx["edges"])


def test_determinism(world):
    a = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    b = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    assert a["studies"] == b["studies"]
    assert a["edges"] == b["edges"]


def test_tolerant_of_missing_fields():
    row = fdindex.normalize_study({"slug": "bare"}, "local")
    assert row["title"] == "bare" and row["design_type"] == "unknown" and row["population"] == "unknown"
    assert row["constructs"] == [] and row["keywords"] == [] and row["authors"] == []
    assert row["n"] is None and row["release_status"] == "unknown" and row["provenance_mode"] == "unknown"
    assert row["hypotheses"] == {"registered": 0, "unregistered": 0, "deviation": 0, "robustness": 0, "exploratory": 0, "supported": 0}


def test_write_index_and_cli(world, tmp_path):
    idx = fdindex.build_index(world["studies"], world["registry"], include_drafts=True, fetcher=world["fetcher"])
    out_file = tmp_path / "out" / "index.json"
    fdindex.write_index(idx, out_file)
    assert json.loads(out_file.read_text())["studies"] == idx["studies"]
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    fdindex.write_index(idx, out_dir)
    assert json.loads((out_dir / "index.json").read_text())["edges"] == idx["edges"]

    cli_out = tmp_path / "cli"
    cli_out.mkdir()
    rc = fdindex.main(["--studies", str(world["studies"]), "--registry", str(tmp_path / "missing.json"),
                       "--out", str(cli_out), "--include-drafts"])
    assert rc == 0
    data = json.loads((cli_out / "index.json").read_text())
    assert {s["slug"] for s in data["studies"]} == {"alpha-study", "beta-draft"}


def test_similar_to_orders_by_weight():
    rows = [
        {"slug": "a", "title": "A", "constructs": ["x", "y"], "keywords": ["k1"], "design_type": "survey_experiment", "population": "mturk"},
        {"slug": "b", "title": "B", "constructs": ["x"], "keywords": ["k1"], "design_type": "survey_experiment", "population": "mturk"},
        {"slug": "c", "title": "C", "constructs": ["z"], "keywords": [], "design_type": "panel", "population": "unknown"},
        {"slug": "d", "title": "D", "constructs": ["y"], "keywords": ["k2"], "design_type": "survey_experiment", "population": "yougov"},
    ]
    from filedrawer.index import similar_to
    sim = similar_to("a", rows, k=5)
    assert [s["slug"] for s in sim] == ["b", "d"]        # c shares nothing
    assert sim[0]["weight"] == round(0.5 * 0.5 + 0.2 + 0.1 + 0.2 * 1.0, 3)
    assert "construct:x" in sim[0]["reasons"] and "design:survey_experiment" in sim[1]["reasons"]
    assert similar_to("nope", rows) == []


def test_tests_rows_for_the_results_ledger():
    hyps = [{"id": "H1:3", "text": "Each arm raises accuracy. — Automated Flagging", "outcome": "total_score", "tag": "registered",
             "arm": "3", "arm_label": "Automated Flagging", "estimate": 0.0439423, "se": 0.0150531, "p": 0.00351, "supported": True},
            {"id": "H1:pooled", "text": "Each arm raises accuracy. — pooled", "tag": "registered", "arm": "pooled",
             "estimate": 0.0105, "se": 0.00697, "p": 0.132, "supported": False},
            {"id": "E1", "text": "", "tag": "exploratory", "estimate": None}]          # nothing estimated: left out
    row = fdindex.normalize_study({"slug": "s", "hypotheses": hyps}, "local")
    assert row["hypotheses"]["registered"] == 2
    a, b = row["tests"]
    assert a == {"id": "H1:3", "h": "H1", "text": "Each arm raises accuracy.", "arm": "Automated Flagging", "outcome": "total_score",
                 "estimate": 0.04394, "se": 0.01505, "p": 0.00351, "supported": True, "tag": "registered"}
    assert b["arm"] == "pooled" and b["supported"] is False and len(row["tests"]) == 2
