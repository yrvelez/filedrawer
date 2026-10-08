# filedrawer

A file drawer for AI-generated research reports. Point it at an unpublished survey experiment
(Qualtrics export + QSF schema + pre-analysis plan) and an agentic pipeline, running on your own
OpenRouter token, produces a git-committable **study package**:

- `report.md` with a row of SVG badges (provenance, editor's decision, pre-registration, release, design, data, cost), findings-first prose, a design diagram, a decision letter from the review and a research-potential memo with typed follow-up proposals
- tidied data (`data/raw_tidy.csv` with identifiers removed, `data/clean.csv` with constructed outcomes)
- reproducible scripts (`scripts/01_tidy.py` … `04_exploratory.py`) and tidy result tables
- the pre-analysis plan as `pap.json`, with every analysis **tagged registered / deviation / exploratory**
  by comparing the registered and implemented specifications field by field
- a minimal, findings-focused literature note (OpenAlex; only retrieved works can be cited)
- exploratory analyses, clearly labeled `(EXPLORATORY — not pre-registered)`
- `provenance/` with models, token usage, cost, input hashes and the full LLM request log

A repository site (`docs/`) lists the papers, shows related work for each, and links straight to the
files in git. It runs on GitHub Pages or on a 256 MB Fly machine that scales to zero.

Idea and motivation: keep low-priority or "slop" studies out of strained journals, surface the file
drawer for humans and agents (meta-analysis, power calculations), and create citation incentives for
reusing data and pipelines. Reports are treated like replication packages or R packages: public goods,
not CV items.

## Privacy design

Raw respondent data never leaves your machine and is never sent to a model.

- Models see only the survey **schema** (QSF-derived codebook), column names and types, capped
  summary profiles (value counts, means), the PAP text, and **aggregate** result tables produced by
  scripts executed locally. Tool access to `data/` is refused structurally, not by prompt.
- **First-line PII detection** reads only the header row and the first five data rows, locally, using the
  Qualtrics identifier list (`IPAddress`, `RecordedDate`, `ResponseId`, recipient fields, location, panel ids;
  `StartDate`/`EndDate` are kept)
  plus header/value regexes (email, IP, phone, geo, DOB), QSF free-text questions, and a prose check
  that catches chat transcripts and open answers even without a QSF (columns the QSF marks
  closed-ended are exempt). Recruitment and response ids are caught by their values under any column name:
  Prolific, MTurk worker, CloudResearch Connect, UUIDs, Qualtrics response ids, IPv4 and IPv6. Flagged columns
  are dropped before anything else runs. Bypass with `--allow-pii` (loud warning written to
  provenance; `release` then refuses without `--force`) or whitelist with `--pii-keep COL,...`.
  `filedrawer pii export.csv [--qsf survey.qsf] [--model]` shows what would be dropped and why, without writing anything.
- **Optional local PII model** (`pip install 'filedrawer[pii-model]'`, then `--pii-model` or `pii: {model: true}`):
  GLiNER2-PII (about 200M parameters, CPU, roughly 75 ms per cell) reads a sample of up to 40 cells in the columns
  the rules kept and drops a column when 30% or more contain a name, contact detail, address, date of birth or id.
  It catches what rules cannot, such as a column of names under an innocent header. To avoid its false alarms on
  short labels it reads only text columns whose values are mostly distinct (no numbers, codes, timestamps or
  categories). It runs on your machine; nothing is sent anywhere, and its report names columns, never values.
  On four real studies it flagged nothing; on planted fictional PII it caught names, phone numbers and addresses.
- OpenRouter requests carry `provider: {data_collection: "deny"}` (zero data retention) by default.
- Tests plant a canary string in a free-text column of the synthetic demo and assert it appears in no
  LLM request and nowhere in the package. `filedrawer submit` runs the same kind of canary check on the
  author's installed copy before anything is sent, scans the package, and sends only the link to the
  author's own GitHub repository (see *After a run*).

## Install

```bash
git clone https://github.com/yrvelez/filedrawer && cd filedrawer
pip install -e ".[test]"
pytest -q                      # offline: everything LLM-related runs against a deterministic mock
```

Python 3.11+. Generated analysis scripts are Python (pandas + statsmodels, HC2 robust SEs, cluster
SEs when a cluster variable is registered). `analysis.language: r` is a stub for an R backend.

## Run

```bash
export OPENROUTER_API_KEY=sk-or-...          # bring your own token
# set models.strong / models.fast in config.yaml (defaults are placeholders; check openrouter.ai/models)

filedrawer demo --review                     # synthetic demo end to end (~$0.05-0.20 depending on models)
filedrawer run --csv export.csv --qsf survey.qsf --pap pap.md \
    --slug my-study --title "..." --authors "A, B" --review
```

Flags: `--provider mock` (offline), `--review [MODES]` (see *Review modes*; the Light Pass runs by default, `--no-review` skips it),
`--review-revise` (one revision turn), `--no-lit`, `--no-exploratory`,
`--arm-column` (when the QSF has no randomizer embedded data), `--allow-pii`, `--pii-keep`.
No QSF? Omit `--qsf`: the codebook is inferred from the export's question-text rows (degraded mode).

Token budget: deterministic Python does all parsing, cleaning, standard estimation and tagging; models
are called only to read the PAP, write at most one exploratory script, write the narrative, and optionally
review. Per-agent turn and token budgets live in `config.yaml`; usage and cost are recorded per agent in
`provenance.json`.

## Run it locally and push to GitHub (no API key)

Every agent can run on a model served from your own machine, so no data, prompt or report leaves it. Any
OpenAI-compatible server works: [Ollama](https://ollama.com) (the default), LM Studio, llama.cpp or vLLM.
Qwen is the default family; a 32B model needs about 20 GB of memory, the 8B model about 6 GB.

```bash
ollama pull qwen3:32b && ollama pull qwen3:8b       # once
filedrawer run --local --csv inputs/export.csv --qsf inputs/survey.qsf --pap inputs/pap.md \
  --slug my-study --title "My study" --authors "A. Author" --package-dir .
filedrawer run --local-model qwen3:32b ...          # one model for every agent
lms server start && lms load qwen/qwen3.8-27b       # or LM Studio (the same Qwen 3.8 the hosted runs use)
filedrawer run --local-url http://localhost:1234/v1 --local-model qwen/qwen3.8-27b ...
filedrawer publish . --repo my-study                # scan, commit, create the GitHub repo (private) and push
filedrawer publish . --repo my-study --public --submit   # public, then list it on filedrawer.org
```

`local.models` and `local.base_url` in `config.yaml` set the defaults. A local run records "local models" in its
provenance and badge, and costs nothing. `filedrawer publish` scans `data/` for identifier columns and the text
files for identifiers and contact details before anything is committed, writes a `.gitignore` that keeps raw
exports out, creates the repository with the GitHub CLI (`gh auth login` once) unless one exists, and pushes.

## After a run

```bash
filedrawer reproduce studies/my-study                 # re-runs scripts 02-05, checks tables byte-identical
filedrawer attest studies/my-study --step "Read the report and checked the tags" --by "Your Name"
                                                       # flips the badge to HUMAN REVIEWED
filedrawer release studies/my-study                   # re-scans data/ for identifiers, marks released, mints a DOI
git add -A && git commit -m "Study package" && git push   # the package lives in your own public GitHub repository
filedrawer submit studies/my-study --server https://filedrawer.org --dry-run
                                                       # self-check, scans, checks it is pushed; files nothing
filedrawer submit studies/my-study --server https://filedrawer.org
                                                       # sends only the repository link
```

The File Drawer lists only self-run, self-hosted packages: you run the pipeline on your own machine and
keep the package in your own public GitHub repository. `filedrawer submit <package>` is how it gets listed.
Before anything is sent it shows the code that is running (version, commit, whether the source has local
changes, a hash of every source file) and runs an offline self-check: a synthetic study with a planted
canary sentence and email goes through the real pipeline with a mock model, and the check fails if either
reaches a model request or the package. It scans the package's text files for identifiers (worker and
response ids, IP addresses, SSNs, phone numbers, emails) and for result tables shaped like respondent-level
data, asks the author to confirm any contact details, and scans `data/` for identifier columns. It then
checks the package is committed and pushed to GitHub and sends only the repository link. The server reads a
few small text files from GitHub (no model call), screens and audits the submission, and stores one
metadata record; `GET /audit` lists everything it holds. `filedrawer build-index`
still builds `docs/index.json` for a static GitHub Pages copy.

Studies stay `draft` (hidden from the public index unless `--include-drafts`) until you release them.
Keep the repository private until you are ready; nothing in the tool requires it to be public.

### A DOI for each paper (Zenodo)

```bash
filedrawer release studies/my-study                   # DOI by default; asks before publishing (--yes to skip the question)
filedrawer release studies/my-study --sandbox         # dry run on sandbox.zenodo.org: test DOI, citation files untouched
filedrawer release studies/my-study --community filedrawer   # also submit to a Zenodo community
filedrawer release studies/my-study --no-doi          # release without a DOI
```

The package (git-tracked files, without `inputs/`) is archived on Zenodo. The DOI is reserved first and written into
`CITATION.cff`, `study.json` and the report's "How to cite" before the archive is uploaded, so the archive carries its
own DOI. Citations use the concept DOI, which always resolves to the latest version; releasing the same package again
adds a new version under it, but only when the package's substance changed (data, results, scripts, figures, plan,
extension designs or report text); `--new-version` forces one. Without a Zenodo token the release still goes through
and says how to add the DOI later. The record is kept in `zenodo.json` at the package root (never cleared by a re-run); commit
it with the rest and refresh the listing. A published Zenodo record cannot be deleted.

Tokens: create a personal access token with scopes `deposit:write` and `deposit:actions` on zenodo.org (and one on
sandbox.zenodo.org), then `export ZENODO_TOKEN=...` / `ZENODO_SANDBOX_TOKEN=...` or put those lines in `~/.zenodo.env`.

## Study package layout

```
studies/<slug>/
  study.json        metadata, hypotheses with tags and estimates, provenance, release_status, links
  report.md         the report (badge, registered-vs-implemented table, tagged sections, figures)
  pap.md / pap.json the plan as written and as structured (registered + implemented + ambiguities)
  codebook.md/json  from the QSF (question text, value labels, arms, stimuli)
  survey.qsf        the schema (no responses)
  data/             raw_tidy.csv, clean.csv
  scripts/          01_tidy 02_clean 03_registered 04_exploratory
  results/          tidy CSV tables incl. registered_summary.csv and analysis_tags.csv
  figures/          PNG
  review.md         optional reviewer pass
  provenance/       provenance.json, llm_log.jsonl, literature.json, script_history/
  RUN.md
```

## Answering the reviewer

Every `--review` run writes `review.json`, where each issue is tagged *analytical* (needs another estimate) or
*presentational*. `filedrawer address <study>` turns each analytical issue into a proposed robustness addendum
(a documented change to one hypothesis: covariates, estimator, standard errors, weights, extra exclusions),
asks you to approve it (`--yes` for unattended, `--dry-run` to only propose), re-estimates with the addenda
beside the registered analyses, re-writes the prose, re-runs the reviewer and writes `responses.md`, a
point-by-point memo. Registered analyses are never modified; addenda are tagged `robustness` and kept out of
the headline findings. Approvals are recorded as human review steps in the provenance.

### Review modes

`--review` takes a comma-separated list; issues from every reviewer land in one `review.json`, each tagged with its source.

| Mode | What runs | Issue ids | Cost |
|---|---|---|---|
| `light` (default) | the Light Pass: every reported estimate against the result tables, every analysis against the pre-analysis plan (a deterministic plan match plus one referee call); text corrections only, never new estimates | R1, R2, ... | ~1-2 cents |
| `advanced` | methodology and statistics reviewers, one call each on the fast model; prompts in `src/filedrawer/review/prompts/` (override with `--advanced-prompts DIR`) | A1, ... | ~1 cent |
| `coarse` | the open-source [coarse](https://github.com/Davidvandijcke/coarse) reviewer via `uvx --python 3.12 coarse-ink review report.md` (set `coarse.command`/`coarse.model` in the config); its report is kept at `provenance/coarse_review.md` and structured into issues | C1, ... | ~$1-2, several minutes |
| `refine` | [Refine](https://www.refine.ink) has no API: upload `report.md`, save the review, then `filedrawer review-import <study> refine <file>` (PDF, Markdown or text) | RF1, ... | Refine's pricing |

Advanced, coarse and Refine comments are mapped onto the same shape as the light pass (critical/major → high,
minor → medium, suggestion → low; confidence below 0.3 dropped), so `filedrawer address` answers any of them.
Comments asking for multiple-testing corrections stay presentational: the plan's policy is the authors' choice.
On the second review after `address`, light and advanced re-run; coarse and Refine issues are carried over.
In practice coarse is best run on its own (a review on its default model can take over 30 minutes and ~$2):
`uvx --python 3.12 coarse-ink review report.md`, then `filedrawer review-import <study> coarse report_review.md`.
`--review coarse` runs the same command inside the pipeline and streams its progress to `provenance/coarse.log`.

## Extensions: follow-up studies as survey drafts

`--extensions` on a run, or `filedrawer extensions <study>` on a finished package, proposes three follow-up
experiments: a *mechanism* test (why the effect happened), a *boundary* test (where it holds) and an *alternative*
(a rival explanation or intervention). Each is a complete survey instrument in autoexperiment's schema, written
from the report's results and limitations, reusing the source survey's questions by QSF id and citing only sources
the package knows. Designs are checked by autoexperiment's own validator and saved under `extensions/`; the report
gets a "Proposed extensions" section. `filedrawer build-extension <study> <id>` builds one as an **unpublished**
Qualtrics draft through the Qualtrics MCP server (local; credentials never leave your machine; `--dry-run` only
validates). About $0.20-0.35 per set of three. Setup: `contrib/autoexperiment/README.md`.

## Cost

A full run with the default models costs about $0.10 in OpenRouter calls: typically 70k-150k input tokens and 15k-30k
output tokens over about a dozen calls (plan reading and the reviewer pass on Claude Sonnet 5.5; cleaning, analysis code,
exploratory analyses, literature note and prose on Qwen 3.8 27B with low reasoning effort). `provenance/provenance.json`
records the exact tokens and cost of every run, and the site shows it on the paper's page.

## Depositing a study

`AGENTS.md` is the deposit routine for a coding agent or a person: scaffold a study repository, put the raw
export (gitignored), the QSF and the plan in `inputs/`, strip identifiers the scanner cannot know about, run,
reproduce, verify nothing restricted is staged, and hand over with the repository still private. It ends with a
copy-paste prompt. `filedrawer init-study` writes a copy into every new study repository.

Before the first run the author chooses the setup, in a terminal or through their agent: where the models run
(hosted or local), which models, an outside review to import later (Coarse, Refine, OpenReview or any referee
report; the Light Pass always runs), follow-up designs, the local PII model and the literature search.
`filedrawer configure .` asks and writes `filedrawer.setup.yaml`, which `filedrawer run` reads and `run.sh`
requires; `filedrawer configure --questions` gives an agent the same questions as JSON.

### Methods comparisons (no arms)

Studies that compare measurement or data-generation methods against a benchmark (for example, synthetic
survey responses scored against observed ones) have no randomized arms and no pre-analysis plan for the
pipeline to read. `filedrawer init-study . --design methods_comparison ...` scaffolds a hand-built package
instead: `study.json` (design type `methods_comparison`, `hypotheses: []`, registration `none`), a `report.md`
skeleton with the provenance badge, and a `.gitignore` that keeps licensed source data in `external/`. The
author lists the analysis scripts (Python or R) and the outputs they rebuild under `reproduce` in `study.json`:

```json
"reproduce": {"scripts": ["scripts/02_compare.R"], "outputs": ["results/*.csv"]}
```

`filedrawer reproduce .` re-runs those scripts in a copy and compares the outputs byte for byte; `attest`,
`release` (which scans every CSV under `data/`, recursively) and `submit` work as for pipeline packages.

## The site and hosting

`docs/index.html` is a single static file (no CDN, no build step) styled as an article archive: a masthead,
the latest papers as a table of contents with abstracts, topic sections, a browse view over the whole
database (search, filters, sortable table, `index.json` download), an article page that renders the
paper in place with a "Related papers" list, and a submit page that returns the most similar papers
already filed. Similarity = shared controlled-vocabulary constructs (`src/filedrawer/vocab/constructs.json`),
design type, sample type and keywords; `GET /similar/<slug>` exposes it.

- **GitHub Pages**: Settings → Pages → deploy from `main` `/docs` (needs a public repo or GitHub Pro).
- **Fly.io** (recommended while the repo is private): one shared-cpu-1x / 256 MB machine, scale to zero,
  stdlib-only server (`server/app.py`). It serves the dashboard and accepts one kind of submission:
  `POST /submit` with the link to a self-run filedrawer package in the author's public GitHub repository
  (what `filedrawer submit` sends, or the Submit page). The server reads `study.json`, the report and the
  result tables from GitHub (never the data, never a model call), screens the submission, and stores one
  metadata record (`/records/<id>.json`, a few KB) that links back to GitHub. The report is fetched from
  GitHub when someone opens it (`/records/<id>/paper.md`) and figures load from GitHub; nothing else is
  kept. `GET /audit` lists every file on disk. A paper whose repository goes private stops rendering.
  Nothing else is copied. Packages made with filedrawer get rich records (tags, estimates,
  provenance); ordinary replication archives get a file inventory, README excerpt and vocabulary-matched
  constructs. If the app has an `OPENROUTER_API_KEY` secret, one short zero-retention call per thin
  submission fills in title, constructs, design and a one-sentence summary (`FD_HARVEST_MODEL`, default
  `qwen/qwen3.5-27b`). A `GITHUB_TOKEN` secret raises the GitHub API rate limit (optional).

  ```bash
  fly launch --copy-config --no-deploy      # uses fly.toml
  fly volumes create fd_data --size 1
  fly secrets set ADMIN_TOKEN=$(openssl rand -hex 16)   # plus optionally OPENROUTER_API_KEY, GITHUB_TOKEN
  fly deploy
  filedrawer submit https://github.com/you/your-archive --server https://<app>.fly.dev
  curl "https://<app>.fly.dev/admin/records?token=..."            # list records
  curl -X POST "https://<app>.fly.dev/admin/refresh?token=...&id=<id>"   # re-harvest one
  curl -X POST "https://<app>.fly.dev/admin/remove?token=...&id=<id>"    # moderate
  ```

  `filedrawer harvest <url>` prints the record the server would build, without submitting.

- **Federation**: `docs/registry.json` still lists packages that live in other repositories by their
  `study.json` URL, for the static GitHub Pages build (which has no server to harvest for it).

`filedrawer serve` runs the same server locally.

## What is and is not automated

Everything in a package is produced by the pipeline unless the badge says otherwise. `attest` records
human steps and flips the badge; it does not verify them. Conferences, job talks and reviewers will
still expect an author to stand behind a study; the file drawer is for the work you would otherwise
leave unpublished.

## Licenses

Code: MIT (`LICENSE`). Reports, data and figures under `studies/`: CC BY 4.0 (`LICENSE-CONTENT`)
unless a study's `study.json` says otherwise.
