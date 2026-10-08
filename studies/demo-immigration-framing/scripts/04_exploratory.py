"""04_exploratory: three exploratory analyses (EXPLORATORY — not pre-registered).

E1 item-level effects; E2 robustness to attention-check failures; E3 heterogeneity by ideology.
"""
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
RES, FIG = ROOT / "results", ROOT / "figures"
RES.mkdir(exist_ok=True); FIG.mkdir(exist_ok=True)
df = pd.read_csv(ROOT / "data" / "clean.csv")


def ate(d, y, extra=""):
    r = smf.ols(f"{y} ~ treat{extra}", data=d).fit(cov_type="HC2")
    ci = r.conf_int().loc["treat"]
    return dict(estimate=r.params["treat"], std_error=r.bse["treat"], p_value=r.pvalues["treat"],
                conf_low=ci[0], conf_high=ci[1], n=int(r.nobs))

# E1: item-level effects
items = {"imm_att_1": "Immigrants strengthen economy", "imm_att_2r": "Reduce immigration (reversed)", "imm_att_3": "Path to citizenship"}
df["imm_att_2r"] = 8 - df["imm_att_2"]
rows = [dict(item=lab, **ate(df, col)) for col, lab in items.items()]
e1 = pd.DataFrame(rows); e1.to_csv(RES / "E1_item_effects.csv", index=False)
print("E1 item effects:", ", ".join(f"{r['item']}={r['estimate']:.2f}" for r in rows))
fig, ax = plt.subplots(figsize=(6, 2.6))
ax.errorbar(e1["estimate"], range(len(e1)), xerr=1.96 * e1["std_error"], fmt="o", color="#2457a6", capsize=3)
ax.set_yticks(range(len(e1))); ax.set_yticklabels(e1["item"]); ax.axvline(0, color="#888", lw=1)
ax.set_xlabel("Treatment effect on item (95% CI)"); ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig(FIG / "E1_item_effects.png", dpi=150); plt.close(fig)

# E2: robustness to attention-check failures
rows = [dict(sample="all", **ate(df, "imm_index")), dict(sample="passed attention check", **ate(df[df["attn"] == 5], "imm_index"))]
pd.DataFrame(rows).to_csv(RES / "E2_attention_robustness.csv", index=False)
print(f"E2 attention robustness: all={rows[0]['estimate']:.2f}, passed={rows[1]['estimate']:.2f}")

# E3: heterogeneity by ideology (5-point)
rows = [dict(ideo5=int(k), **ate(sub, "imm_index")) for k, sub in df.groupby("ideo5")]
pd.DataFrame(rows).to_csv(RES / "E3_by_ideology.csv", index=False)
inter = smf.ols("imm_index ~ treat * ideo5", data=df).fit(cov_type="HC2")
print(f"E3 by ideology: interaction treat:ideo5 = {inter.params['treat:ideo5']:.3f} (p={inter.pvalues['treat:ideo5']:.3f})")
