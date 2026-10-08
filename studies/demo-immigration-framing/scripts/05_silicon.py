"""05_silicon: recompute the silicon-sampling comparison from frozen LLM responses.

The responses in silicon/responses.csv were produced once by the model named in
provenance.json and are a frozen artifact: do not regenerate them. (EXPLORATORY — not pre-registered)
"""
import json
import pathlib
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = json.loads((ROOT / "silicon" / "personas.json").read_text())
resp = pd.read_csv(ROOT / "silicon" / "responses.csv")
human = pd.read_csv(ROOT / "results" / "registered_summary.csv")
rows = []
for o in spec["outcomes"]:
    lo, hi = o.get("scale") or [1, 7]
    parts = []
    for c in o["columns"]:
        s = pd.to_numeric(resp[c], errors="coerce")
        if c in (o.get("reverse") or []):
            s = (lo + hi) - s
        parts.append(s)
    resp[o["name"]] = pd.concat(parts, axis=1).mean(axis=1, skipna=False)
    d = resp.dropna(subset=[o["name"]])
    r = smf.ols(f"{o['name']} ~ treat", data=d).fit(cov_type="HC2")
    h = human[(human["outcome"] == o["name"]) & human["term"].isna()] if "term" in human else human[human["outcome"] == o["name"]]
    rows.append({"outcome": o["name"], "silicon_estimate": r.params["treat"], "silicon_se": r.bse["treat"], "silicon_p": r.pvalues["treat"],
                 "silicon_n": int(r.nobs), "silicon_mean_control": d.loc[d.treat == 0, o["name"]].mean(), "silicon_mean_treated": d.loc[d.treat == 1, o["name"]].mean(),
                 "human_estimate": float(h["estimate"].iloc[0]) if len(h) else np.nan, "human_se": float(h["std_error"].iloc[0]) if len(h) else np.nan,
                 "human_n": int(h["n"].iloc[0]) if len(h) else 0})
    print(f"{o['name']}: silicon ATE={r.params['treat']:.3f} (SE {r.bse['treat']:.3f}, N={int(r.nobs)}) vs human ATE={rows[-1]['human_estimate']:.3f}")
cmp = pd.DataFrame(rows)
cmp.to_csv(ROOT / "silicon" / "comparison.csv", index=False)
fig, ax = plt.subplots(figsize=(6, 1.2 + 0.7 * len(cmp)))
y = np.arange(len(cmp))
ax.errorbar(cmp["human_estimate"], y + 0.15, xerr=1.96 * cmp["human_se"], fmt="o", color="#2457a6", capsize=3, label="Human respondents")
ax.errorbar(cmp["silicon_estimate"], y - 0.15, xerr=1.96 * cmp["silicon_se"], fmt="s", color="#d9822b", capsize=3, label="Silicon sample (LLM personas)")
ax.set_yticks(y); ax.set_yticklabels(cmp["outcome"]); ax.axvline(0, color="#888", lw=1)
ax.set_xlabel("Treatment effect (95% CI)"); ax.legend(frameon=False, fontsize=8); ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig(ROOT / "figures" / "silicon_comparison.png", dpi=150); plt.close(fig)
print("wrote silicon/comparison.csv and figures/silicon_comparison.png")
