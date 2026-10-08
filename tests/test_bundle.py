import json

from filedrawer import bundle as B

STUDY = {"slug": "s", "title": "T", "design": {"type": "survey_experiment", "n_analysis": 200}}


def _package(tmp_path):
    root = tmp_path / "pkg"
    for rel, text in {
        "study.json": json.dumps(STUDY), "report.md": "# T\n\n![fig](figures/H1.png)\n", "pap.json": "{}",
        "results/H1.csv": "analysis_id,term,estimate\nH1,C(arm_code, Treatment(reference='T0'))[T.T1],0.5\n",
        "scripts/03_registered.py": "print('ok')\n", "figures/H1.png": "PNG",
        "data/clean.csv": "id,y\n1,2\n", "data/raw_tidy.csv": "id,y\n1,2\n", "inputs/export.csv": "x\n",
        "provenance/provenance.json": "{}", "provenance/llm_log.jsonl": "{}\n", ".filedrawer/submission.json": "{}",
        "notes.txt": "x", "results/../escape.csv": None,
    }.items():
        if text is None:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_collect_uses_the_allowlist(tmp_path):
    files = B.collect(_package(tmp_path))
    assert set(files) == {"study.json", "report.md", "pap.json", "results/H1.csv", "scripts/03_registered.py",
                          "figures/H1.png", "provenance/provenance.json"}


def test_allowlist_rejects_data_logs_and_traversal():
    for rel in ["data/clean.csv", "inputs/export.csv", "provenance/llm_log.jsonl", "../report.md", "results/../data/x.csv",
                "/etc/passwd", "results/x.dta", "figures/a b.png", ".filedrawer/submission.json", "report.html"]:
        assert not B.is_allowed(rel), rel
    for rel in ["report.md", "results/H1_arms.csv", "figures/badges/review.svg", "scripts/04_exploratory.py", "extensions/E1.md"]:
        assert B.is_allowed(rel), rel


def test_clean_package_passes(tmp_path):
    s = B.scan(B.collect(_package(tmp_path)))
    assert s["ok"] and s["problems"] == [] and s["review"] == []


def test_identifiers_block_and_findings_never_echo_values(tmp_path):
    files = B.collect(_package(tmp_path))
    files["report.md"] += b"\nworker A1B2C3D4E5F6G7, response R_1hLk0YtZk6t4U9A, from 10.0.0.12, ssn 123-45-6789\n"
    s = B.scan(files)
    assert not s["ok"]
    assert {p["reason"] for p in s["problems"]} == {"mturk worker id", "qualtrics response id", "ip address", "ssn"}
    dumped = json.dumps(s)
    assert "A1B2C3D4E5F6G7" not in dumped and "10.0.0.12" not in dumped and "R_1hLk" not in dumped


def test_contacts_need_acknowledgment(tmp_path):
    files = B.collect(_package(tmp_path))
    files["codebook.md"] = b"Questions? Call the IRB at 212-555-0147 or write to someone@gmail.com; askirb@columbia.edu\n"
    s = B.scan(files)
    assert not s["ok"] and s["problems"] == []
    keys = [B.review_key(r) for r in s["review"]]
    assert keys == ["codebook.md: phone number", "codebook.md: email"]
    assert any(n["reason"].startswith("institutional contact") for n in s["notes"])
    assert B.scan(files, acknowledged=keys)["ok"]


def test_no_false_alarms_on_dois_and_model_terms(tmp_path):
    files = B.collect(_package(tmp_path))
    files["report.md"] += b"\nhttps://doi.org/10.1038/s41586-021-03819-2 and 10.1111/j.1467-6486.2010.00950.x; ANOVA ACKNOWLEDGEMENTS\n"
    assert B.scan(files)["ok"]


def test_row_level_tables_block(tmp_path):
    files = B.collect(_package(tmp_path))
    files["results/sneaky.csv"] = ("treatment,y\n" + "".join(f"{i % 2},{i}\n" for i in range(150))).encode()
    files["results/ids.csv"] = b"ResponseId,y\nx,1\n"
    files["results/text.csv"] = ("arm,answer\n" + "".join(f"1,I think we should spend a lot more money on trains {i}\n" for i in range(12))).encode()
    reasons = {(p["path"], p["reason"].split(":")[0]) for p in B.scan(files)["problems"]}
    assert ("results/sneaky.csv", "150 rows for 200 respondents") in reasons
    assert ("results/ids.csv", "identifier column") in reasons
    assert ("results/text.csv", "free-text column") in reasons


def test_off_allowlist_files_block(tmp_path):
    files = B.collect(_package(tmp_path))
    files["data/clean.csv"] = b"id,y\n1,2\n"
    assert {"path": "data/clean.csv", "reason": "not on the allowlist", "count": 1} in B.scan(files)["problems"]


def test_manifest_hash_is_stable(tmp_path):
    files = B.collect(_package(tmp_path))
    assert B.manifest_sha256(files) == B.manifest_sha256(dict(reversed(list(files.items()))))
    assert [m["path"] for m in B.manifest(files)] == sorted(files)
