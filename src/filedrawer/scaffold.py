"""Scaffold a standalone study repository that the pipeline fills in."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import __version__

DESIGNS_BY_HAND = ("methods_comparison",)    # design types whose packages the author builds, not the pipeline

README = """# {title}

Study repository in the [filedrawer](https://github.com/yrvelez/filedrawer) layout. The package
(report, tidy data, scripts, results, provenance) is generated at the root of this repository by
the pipeline; the inputs live in `inputs/`.

Depositing with an AI coding agent, or by hand: follow `AGENTS.md` in this repository. It says what
must never be committed (the raw export), how to strip identifiers, and what the models see.

## Inputs (`inputs/`)

- `export.csv`  Qualtrics export (raw; **gitignored**, it contains identifiers)
- `survey.qsf`  survey schema
- `pap.md`      pre-analysis plan as text

## Run

```bash
pip install "filedrawer @ git+https://github.com/yrvelez/filedrawer"
filedrawer configure .   # choose where models run, which models, outside review, follow-ups, PII model, literature
export OPENROUTER_API_KEY=sk-or-...   # hosted models only
./run.sh            # or: filedrawer run --csv inputs/export.csv --qsf inputs/survey.qsf --pap inputs/pap.md \\
                    #        --slug {slug} --title "{title}" --authors "{authors}" --package-dir . --review
# outside reviews are added afterwards: filedrawer review-import . coarse|refine|openreview FILE
filedrawer reproduce .
git add -A && git commit -m "Study package" && git push
```

Then file it: paste `{repo_url}` on the Submit page at https://filedrawer.org (or `filedrawer submit {repo_url} --server https://filedrawer.org`).
After reading the report: `filedrawer attest . --step "..." --by "..."`, and `filedrawer release .` before making the repository public.
"""

RUN_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f filedrawer.setup.yaml ]; then
  echo "Choose the models, review and options first: filedrawer configure ." >&2
  exit 1
fi
if ! grep -q '^provider: local' filedrawer.setup.yaml; then
  : "${{OPENROUTER_API_KEY:?set OPENROUTER_API_KEY (bring your own token)}}"
fi
filedrawer run --csv inputs/export.csv --qsf inputs/survey.qsf --pap inputs/pap.md \\
  --slug "{slug}" --title "{title}" --authors "{authors}" --repo-url "{repo_url}" \\
  --package-dir . "$@"
"""

GITIGNORE = """# raw exports carry identifiers and free text: never commit them
inputs/**/*.csv
inputs/**/*.sav
inputs/**/*.dta
inputs/**/*.xlsx
!inputs/replication_data.csv
raw_export.csv
.mplconfig/
__pycache__/
"""


METHODS_README = """# {title}

Study package in the [filedrawer](https://github.com/yrvelez/filedrawer) layout, design type
`methods_comparison`: a comparison of measurement or data-generation methods against a benchmark,
with no randomized arms. The pipeline does not generate this package; the author writes `report.md`,
fills in `study.json`, and ships the scripts, outputs and data the report rests on.

## Layout

- `report.md`   findings first; cite prior work in the text and list it under References
- `study.json`  metadata the File Drawer reads; `reproduce` names the scripts and the outputs they rebuild
- `scripts/`    analysis code (Python or R)
- `results/`    tables the report cites, rebuilt by the declared scripts
- `data/`       derived data that may be redistributed (`filedrawer release` scans every CSV under it)
- `external/`   licensed or restricted source data (**gitignored**; say here where to obtain it)

## Check and file

```bash
pip install "filedrawer @ git+https://github.com/yrvelez/filedrawer"
filedrawer reproduce .          # re-runs study.json's reproduce.scripts, compares reproduce.outputs byte for byte
git add -A && git commit -m "Study package" && git push
```

After reading the report: `filedrawer attest . --step "..." --by "..."`, and `filedrawer release .` before making
the repository public. Then paste `{repo_url}` on the Submit page at https://filedrawer.org (or `filedrawer submit {repo_url} --server https://filedrawer.org`).
"""

METHODS_REPORT = """# {title}

> **Provenance: FULLY AGENTIC — no human review recorded.** Hand-built `methods_comparison` package, filedrawer {version}, {created}. Human steps recorded: 0. Release status: **draft**.

## Summary

(What was compared, against which benchmark, and what was found, in a few sentences.)

## Design

- Methods compared:
- Benchmark:
- Units and sample:
- Metrics:

## Results

## Limitations

## References
"""

METHODS_GITIGNORE = """# licensed or restricted source data: say in README.md where to obtain it, never commit it
external/
inputs/**/*.csv
inputs/**/*.sav
inputs/**/*.dta
inputs/**/*.xlsx
!inputs/replication_data.csv
.mplconfig/
__pycache__/
.Rhistory
"""


def methods_study_json(slug: str, title: str, authors: str, repo_url: str, branch: str = "main") -> dict:
    created = dt.date.today().isoformat()
    base = f"{repo_url}/tree/{branch}"
    return {
        "schema_version": 1, "slug": slug, "title": title,
        "authors": [a.strip() for a in authors.split(",") if a.strip()],
        "summary": "", "created": created, "synthetic": False,
        "design": {"type": "methods_comparison", "methods": [], "benchmark": "", "metrics": [],
                   "n_raw": None, "n_analysis": None},
        "population": {"country": "unknown", "sample": "unknown"},
        "constructs": [], "keywords": [],
        "registration": {"status": "none"},
        "hypotheses": [], "release_status": "draft",
        "license": {"code": "MIT", "content": "CC-BY-4.0"},
        "provenance": {"mode": "fully_agentic", "human_steps": [], "reviewer_pass": False,
                       "tool_version": __version__, "created": created},
        "links": {"folder": base, "report": f"{repo_url}/blob/{branch}/report.md", "data": base + "/data"},
        "reproduce": {"scripts": [], "outputs": ["results/*.csv"]},
        "references": [],
        "related": [],
    }


def init_methods_study(d: Path, slug: str, title: str, authors: str = "", repo_url: str = "") -> str:
    d = Path(d)
    for sub in ("scripts", "results", "data"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    repo_url = repo_url or f"https://github.com/yrvelez/{slug}"
    sj = methods_study_json(slug, title, authors, repo_url)
    files = {
        "README.md": METHODS_README.format(title=title, repo_url=repo_url),
        "report.md": METHODS_REPORT.format(title=title, version=__version__, created=sj["created"]),
        "study.json": json.dumps(sj, indent=1, ensure_ascii=False) + "\n",
        ".gitignore": METHODS_GITIGNORE,
    }
    written = []
    for name, content in files.items():
        p = d / name
        if p.exists():
            continue
        p.write_text(content, encoding="utf-8")
        written.append(name)
    return (f"scaffolded {d} (methods_comparison): " + (", ".join(written) or "nothing new") +
            "\nFill in study.json (design, reproduce.scripts, references) and report.md, then: filedrawer reproduce .")


def init_study(d: Path, slug: str, title: str, authors: str = "", repo_url: str = "",
               design: str = "survey_experiment") -> str:
    if design in DESIGNS_BY_HAND:
        return init_methods_study(d, slug, title, authors, repo_url)
    d = Path(d)
    (d / "inputs").mkdir(parents=True, exist_ok=True)
    repo_url = repo_url or f"https://github.com/yrvelez/{slug}"
    agents_md = Path(__file__).resolve().parent / "AGENTS.md"
    files = {
        "README.md": README.format(title=title, slug=slug, authors=authors, repo_url=repo_url),
        "AGENTS.md": agents_md.read_text(encoding="utf-8") if agents_md.exists() else
                     "See https://github.com/yrvelez/filedrawer/blob/main/AGENTS.md for the deposit routine.\n",
        "run.sh": RUN_SH.format(slug=slug, title=title, authors=authors, repo_url=repo_url),
        ".gitignore": GITIGNORE,
        "inputs/pap.md": f"# Pre-analysis plan: {title}\n\n(paste the registered plan here)\n",
    }
    written = []
    for name, content in files.items():
        p = d / name
        if p.exists() and name != ".gitignore":
            continue
        if name == ".gitignore" and p.exists():
            existing = p.read_text(encoding="utf-8")
            content = existing.rstrip("\n") + "\n" + "\n".join(l for l in GITIGNORE.splitlines() if l and l not in existing) + "\n"
        p.write_text(content, encoding="utf-8")
        written.append(name)
    (d / "run.sh").chmod(0o755)
    return f"scaffolded {d}: " + ", ".join(written) + "\nPut export.csv, survey.qsf and pap.md in inputs/, then ./run.sh"
