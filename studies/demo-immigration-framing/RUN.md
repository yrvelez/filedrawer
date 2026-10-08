# Reproducing `demo-immigration-framing`

All scripts run from this folder with Python 3.11 and pandas, numpy, statsmodels, scipy, matplotlib.

```bash
pip install pandas numpy statsmodels scipy matplotlib
python scripts/01_tidy.py        # only if the raw export is placed at ./raw_export.csv (not shipped: it contains identifiers)
python scripts/02_clean.py       # data/raw_tidy.csv -> data/clean.csv
python scripts/03_registered.py  # registered tests -> results/, figures/registered_effects.png
python scripts/04_exploratory.py # exploratory analyses (EXPLORATORY — not pre-registered)
python scripts/05_silicon.py     # recompute the silicon-sampling comparison from the frozen silicon/responses.csv
```

Or, with the tool installed: `filedrawer reproduce .` re-runs scripts 02-05 and verifies every table in
`results/` and `silicon/comparison.csv` is byte-identical to the shipped version.

`silicon/responses.csv` is a frozen LLM artifact (see `provenance/provenance.json` for the model); do not regenerate it
when reproducing.
