"""AGENTS.md for a finished study package: reuse first, deposit routine second.

A coding agent that opens a study repository is usually there to reanalyze it, so the file starts with what that
agent needs (where the data are, what the columns mean, how to reproduce every table, how to label new work). The
deposit routine the authors followed comes after it, marked as not applying to reanalysis.
"""
from __future__ import annotations

import json
from pathlib import Path

DEPOSIT_GUIDE = Path(__file__).resolve().parent / "AGENTS.md"
DATA_FILES = (("data/clean.csv", "the analysis file, after the planned exclusions"),
              ("data/raw_tidy.csv", "the de-identified file before exclusions"),
              ("inputs/replication_data.csv", "the authors' own de-identified replication file"))


def _ignored(study: Path) -> set[str]:
    gi = study / ".gitignore"
    return {l.strip().rstrip("/") for l in gi.read_text(encoding="utf-8").splitlines()} if gi.exists() else set()


def _shipped(study: Path, rel: str, ignored: set[str]) -> bool:
    """On disk and not gitignored by its own name or its folder (a simple check of the patterns packages use)."""
    return (study / rel).exists() and not ({rel, rel.split("/")[0]} & ignored)


def reuse_section(study: Path) -> str:
    study = Path(study)
    sj = json.loads((study / "study.json").read_text(encoding="utf-8")) if (study / "study.json").exists() else {}
    d = sj.get("design") or {}
    title = sj.get("title") or study.name
    ignored = _ignored(study)
    data = [(rel, what) for rel, what in DATA_FILES if _shipped(study, rel, ignored)]
    unit = (f"{d['n_analysis']:,} respondents, several rows each keyed by `{d['unit_column']}` ({d.get('rows_analysis', 0):,} rows)"
            if d.get("unit_column") and d.get("n_analysis") else (f"{d['n_analysis']:,} respondents, one row each" if d.get("n_analysis") else ""))
    L = [f"# AGENTS.md: {title}", "",
         "This repository is a study package from [The File Drawer](https://filedrawer.org). If you are an AI coding agent, "
         "you are most likely here to reproduce or reanalyze the study: start with the next section. The deposit routine "
         "the authors followed is at the end and does not apply to reanalysis.", "",
         "## Reanalyzing this study", ""]
    if data:
        L.append("**Data (public, de-identified; read and analyze them directly):**")
        for rel, what in data:
            L.append(f"- `{rel}`: {what}" + (f"; {unit}" if rel == "data/clean.csv" and unit else "") + ".")
    else:
        L.append("**Respondent-level data are not in this repository.** Only the result tables in `results/`, the scripts and "
                 "the codebook are public; ask the authors (see README) for the data.")
    L += ["",
          "**What the columns mean:** `codebook.md` (labels and value labels; `codebook.json` for code). Identifier and free-text "
          "columns were removed before release.", "",
          "**The analysis plan:** `pap.md` (prose) and `pap.json` (every hypothesis with its outcome, comparison, estimator, "
          "covariates and exclusions). Whether the plan was pre-registered is stated in `pap.md` and in the report's badge row.", ""]
    if (study / "scripts").is_dir():
        L += ["**Reproduce every table** (Python with pandas, numpy, statsmodels, scipy, matplotlib; see `RUN.md`):", "",
              "```bash", "python scripts/02_clean.py        # data/raw_tidy.csv -> data/clean.csv",
              "python scripts/03_registered.py   # planned analyses -> results/",
              "python scripts/04_exploratory.py  # exploratory analyses -> results/E*.csv", "```", ""]
    if (study / "original").is_dir():
        L += ["**The authors' original analysis code** is in `original/`.", ""]
    L += ["**Results:** `results/registered_summary.csv` holds every planned estimate; `results/H*.csv` and `results/E*.csv` the "
          "per-analysis tables; `report.md` the write-up, with each analysis tagged registered, deviation or exploratory.", "",
          "**Rules for new work:**",
          "1. Analyses beyond `pap.json` are exploratory relative to this study. Label them so and never present them as its "
          "registered results.",
          "2. Report the specification you ran (outcome, comparison, estimator, standard errors, sample) next to every estimate.",
          "3. Do not try to re-identify respondents or link these rows to other data.",
          "4. Cite the study with `CITATION.cff` when you use its data, code or numbers.", ""]
    return "\n".join(L)


def write(study: Path) -> Path:
    """Write AGENTS.md: the reuse section, then the deposit routine."""
    study = Path(study)
    guide = DEPOSIT_GUIDE.read_text(encoding="utf-8") if DEPOSIT_GUIDE.exists() else ""
    guide = guide.replace("# Depositing a study in the File Drawer", "# How this package was deposited (for authors)", 1)
    lines, fence = [], False
    for line in guide.splitlines():                   # one heading level down, so it sits under the reuse section
        fence = fence != line.startswith("```")
        lines.append("#" + line if line.startswith("#") and not fence else line)
    guide = "\n".join(lines) + "\n"
    p = study / "AGENTS.md"
    p.write_text(reuse_section(study) + ("\n---\n\n" + guide if guide else ""), encoding="utf-8")
    return p
