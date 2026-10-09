"""A package's AGENTS.md leads with reanalysis (data, codebook, scripts, rules) and says when data are not public."""
import json

from filedrawer import agents_md


def package(tmp_path, gitignore=""):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "clean.csv").write_text("a\n1\n")
    (tmp_path / "data" / "raw_tidy.csv").write_text("a\n1\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".gitignore").write_text(gitignore)
    (tmp_path / "study.json").write_text(json.dumps({"title": "T", "design": {"n_analysis": 1500, "unit_column": "resp_id", "rows_analysis": 12000}}))
    return tmp_path


def test_reuse_first_then_deposit(tmp_path):
    text = agents_md.write(package(tmp_path)).read_text()
    assert text.index("## Reanalyzing this study") < text.index("## How this package was deposited")
    assert "`data/clean.csv`" in text and "1,500 respondents, several rows each keyed by `resp_id`" in text
    assert "python scripts/03_registered.py" in text and "exploratory relative to this study" in text
    assert "\n# Depositing" not in text


def test_gitignored_data_is_not_offered(tmp_path):
    text = agents_md.write(package(tmp_path, gitignore="data/\n")).read_text()
    assert "Respondent-level data are not in this repository" in text and "`data/clean.csv`:" not in text


def test_acknowledgments_file_is_read_without_its_heading(tmp_path):
    from filedrawer.package import acknowledgments
    assert acknowledgments(tmp_path) == ""
    (tmp_path / "ACKNOWLEDGMENTS.md").write_text("# Acknowledgments\n\nThis study was carried out in collaboration with A. B.\n")
    assert acknowledgments(tmp_path) == "This study was carried out in collaboration with A. B."
