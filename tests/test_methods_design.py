"""Hand-built methods_comparison packages: scaffold, declared reproduce, recursive release scan."""
import json
import shutil
from pathlib import Path

import pytest

from filedrawer.index import normalize_study
from filedrawer.release import attest, release, reproduce
from filedrawer.scaffold import init_study

WRITE_RESULT = (
    "from pathlib import Path\n"
    "rows = Path('data/derived.csv').read_text().splitlines()[1:]\n"
    "Path('results').mkdir(exist_ok=True)\n"
    "Path('results/summary.csv').write_text('n\\n%d\\n' % len(rows))\n"
)


def _scaffold(tmp_path) -> Path:
    d = tmp_path / "study"
    init_study(d, "llm-thermometers", "LLM thermometers against ANES", "A. Author, B. Author",
               "https://github.com/someone/llm-thermometers", design="methods_comparison")
    return d


def _declare(d: Path, scripts, outputs=("results/*.csv",)) -> None:
    sj = json.loads((d / "study.json").read_text())
    sj["reproduce"] = {"scripts": list(scripts), "outputs": list(outputs)}
    (d / "study.json").write_text(json.dumps(sj))


def test_scaffold_writes_methods_package(tmp_path):
    d = _scaffold(tmp_path)
    sj = json.loads((d / "study.json").read_text())
    assert sj["design"]["type"] == "methods_comparison"
    assert sj["authors"] == ["A. Author", "B. Author"]
    assert sj["hypotheses"] == [] and sj["release_status"] == "draft"
    assert sj["registration"] == {"status": "none"}
    assert "external/" in (d / ".gitignore").read_text()
    assert not (d / "run.sh").exists() and not (d / "inputs" / "pap.md").exists()
    row = normalize_study(sj, "local")
    assert row["design_type"] == "methods_comparison" and row["hypotheses"]["registered"] == 0


def test_methods_design_in_vocab():
    vocab = json.loads((Path(__file__).resolve().parent.parent / "src/filedrawer/vocab/constructs.json").read_text())
    assert "methods_comparison" in vocab["designs"]


def test_declared_python_script_reproduces(tmp_path):
    d = _scaffold(tmp_path)
    (d / "data" / "derived.csv").write_text("x\n1\n2\n3\n")
    (d / "scripts" / "summarise.py").write_text(WRITE_RESULT)
    (d / "results" / "summary.csv").write_text("n\n3\n")
    _declare(d, ["scripts/summarise.py"])
    out = reproduce(d)
    assert out["ok"], out
    assert out["identical"] == ["results/summary.csv"]

    (d / "results" / "summary.csv").write_text("n\n4\n")      # shipped table no longer matches the code
    out = reproduce(d)
    assert not out["ok"] and out["different"] == ["results/summary.csv"]


def test_declared_glob_matching_nothing_fails(tmp_path):
    d = _scaffold(tmp_path)
    (d / "scripts" / "noop.py").write_text("pass\n")
    _declare(d, ["scripts/noop.py"], ["results/*.csv"])
    out = reproduce(d)
    assert not out["ok"] and out["missing"] == ["results/*.csv"]


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not installed")
def test_declared_r_script_reproduces(tmp_path):
    d = _scaffold(tmp_path)
    (d / "scripts" / "summarise.R").write_text(
        'dir.create("results", showWarnings = FALSE)\n'
        'writeLines(c("n", "3"), "results/summary.csv")\n')
    (d / "results" / "summary.csv").write_text("n\n3\n")
    _declare(d, ["scripts/summarise.R"])
    out = reproduce(d)
    assert out["ok"], out


def test_release_scans_nested_data(tmp_path):
    d = _scaffold(tmp_path)
    (d / "data" / "raw").mkdir()
    (d / "data" / "raw" / "outputs.csv").write_text("respondent_email,score\nperson@example.org,3\n")
    with pytest.raises(SystemExit, match="raw/outputs.csv"):
        release(d)
    (d / "data" / "raw" / "outputs.csv").write_text("score\n3\n")
    assert release(d)["release_status"] == "released"


def test_attest_and_release_update_scaffolded_badge(tmp_path):
    d = _scaffold(tmp_path)
    attest(d, "read the report", "A. Author")
    release(d)
    badge = next(l for l in (d / "report.md").read_text().splitlines() if l.startswith("> **Provenance:"))
    assert "HUMAN REVIEWED" in badge and "Human steps recorded: 1" in badge and "Release status: **released**" in badge
