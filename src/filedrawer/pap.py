"""Pre-analysis plan (pap.json): schema helpers, validation, and deterministic tagging.

pap.json = {
  "source": "pap.md", "constructs": [...], "keywords": [...],
  "registration": {"status": "registered" | "none", "url": str, "note": str},   # optional; absent = "registered"
  "design": {"type": "survey_experiment",            # see DESIGN_WORDS for the accepted types
             "causal": true,                          # optional; default by type (experiments true, observational false)
             "sample_kind": "human",                  # optional: "human" | "synthetic" | "mixed" (LLM respondents)
             "synthetic": {"model", "personas", "prompt_hash", "generated_on",      # required when sample_kind != human
                           "benchmark_human_study_url", "benchmark_table"},         # (only "model" is mandatory)
             "arms": {"column", "treatment", "control", "labels": {...}, "descriptions": {...}},
             "derived": {"new_column": "pandas eval expression over existing columns"},   # optional
             "population": {"country", "sample", "description"}},   # description: who was sampled, in words
  "registered":  {"outcomes": [...], "hypotheses": [...], "subgroups": [...],
                  "sample_exclusions": [...], "multiple_testing": "none", "alpha": 0.05},
  "implemented": {same shape},
  "ambiguities": [{"hypothesis", "field", "pap_text", "interpretation", "alternatives", "reason",
                   "kind": "interpretation" | "deviation"}]
}
outcome = {"name", "label", "kind": "mean_items" | "single_item" | "custom", "columns": [...],
           "reverse": [...], "scale": [lo, hi], "construction": "free text", "custom_hint": ""}
hypothesis = {"id", "text", "outcome",
              "treatment": {"column", "contrast": [treated, control]}                      # two-arm
                        or {"column", "arms": [treated values...], "control": value}       # multi-arm
                        or {"column", "continuous": true},                                 # numeric slope
              "direction": "positive"|"negative"|"two_sided",
              "estimator": {"kind": "ols"|"diff_means"|"lin"|"did"|"custom", "robust": "HC2", "cluster": null,
                            "weights": column|null, "covariates": [...], "custom_hint": "",
                            "absorb": [columns],      # optional fixed effects, entered as C(column) terms
                            "period": column},        # "did" only: the post-period indicator (0/1)
              "pooled": true|false (multi-arm only; default true),
              "subgroup": null, "exclusions": [...]}
subgroup = {"id", "hypothesis", "moderator", "levels": {"value": "label"}, "expected": "text",
            "estimand": "effect_by" | "mm_diff",   # multi-arm hypotheses only; default effect_by
            "features": [columns],                # mm_diff: every listed feature (default: the treatment column);
                                                  # each feature's marginal means use rows where it is observed
            "covariates": true|false}              # include the hypothesis's covariates (default false)

Treatment columns. A hypothesis's treatment.column may differ from design.arms.column: in a conjoint, each
feature is its own hypothesis (treatment = that feature, covariates = the other features, so the arm
coefficients are the additive-model AMCEs). treatment.continuous enters the column as a numeric slope.
Subgroups of multi-arm hypotheses: "effect_by" reports the arm effects within each moderator level plus
arm x moderator interactions; "mm_diff" reports every level's marginal mean in the two moderator groups
and their difference (second level minus first), as in a conjoint subgroup analysis.

Arms. `design.arms.treatment` is one value for a two-arm design or a LIST of values for a multi-arm
design; `control` is always one value. Arm values are strings; integral numeric codes in the data
("1", 1, 1.0) are all read as "1". `labels` maps arm value -> display label and `descriptions` maps
arm value -> one-line description of the stimulus (shown in the report's table of arms). `design.derived` adds columns before outcomes are built, e.g.
{"ipw": "1 / pr"} to turn an assignment probability into an inverse-probability weight.

Estimators. "ols": y ~ treat + covariates. "diff_means": y ~ treat. "lin": Lin (2013) — covariates
centred at their sample means and fully interacted with the arm dummies, so the arm coefficients are
the ATEs. "did": y ~ treat * period + covariates (two-arm only); the reported coefficient is the
treat:period interaction. `absorb` adds fixed effects as C(column) terms to any of these. `weights`
names a column of regression weights (WLS, used as given); `cluster` names a cluster column
(cluster-robust SEs), otherwise `robust` (HC2 by default). Instrumental variables, discontinuities and
matching are "custom".

Design type and language. `design.type` picks the nouns the report uses (DESIGN_WORDS): arms and a
control group for experiments, levels and a reference level for a conjoint, exposure and comparison
group for observational designs, methods and a benchmark for a methods comparison. `design.causal`
(default: true for experiments, false otherwise) decides whether the text says "effect" or
"association". `design.sample_kind` marks LLM-generated respondents; such packages carry a
"synthetic respondents" badge and a mandatory limitations paragraph. A multi-arm hypothesis fits
ONE model with one coefficient per treatment arm against the control. When `pooled` is true (the
default for multi-arm), the per-arm effects are also combined by a random-effects meta-analysis
(statsmodels combine_effects, iterated tau^2, DerSimonian-Laird fallback). Caveat: the arm effects
share one control group, so they are not independent and the pooled SE is approximate.

Tagging is deterministic: implemented == registered on every compared field -> "registered"
(or "unregistered" when pap.registration.status == "none"); any difference -> "deviation"
(justified by a matching ambiguity of kind "deviation", else "undocumented"); analyses with no
registered counterpart -> "exploratory". An ambiguity of kind "interpretation" (the PAP was silent
or vague) does not make a deviation. Sub-analyses "<HID>:<arm>" and "<HID>:pooled" inherit the tag
of their hypothesis (see tag_for).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

COMPARED_FIELDS = ["outcome", "treatment.column", "treatment.contrast", "treatment.arms", "treatment.control", "treatment.continuous",
                   "direction", "estimator.kind", "estimator.robust", "estimator.cluster", "estimator.weights", "estimator.continuous", "estimator.categorical",
                   "estimator.covariates", "estimator.absorb", "estimator.period", "pooled", "subgroup", "exclusions"]

ESTIMATOR_KINDS = ("ols", "diff_means", "lin", "did", "custom")
CAUSAL_TYPES = {"survey_experiment", "conjoint", "field_experiment", "lab_experiment", "audit"}
SAMPLE_KINDS = ("human", "synthetic", "mixed")

# The nouns the deterministic text and the model notes use, by design type. Experiments get arms and a
# control group; a conjoint gets levels and a reference level; observational designs get an exposure and a
# comparison group, and "association" instead of "effect". design.causal: false switches any type to the
# associational words (but keeps its phrase); methods comparisons have their own vocabulary.
_ASSOCIATIONAL = {"unit": "exposure level", "units": "exposure levels", "reference": "comparison group",
                  "relation": "association", "relations": "associations", "estimate": "adjusted difference",
                  "estimates": "adjusted differences", "verb": "was associated with"}
DESIGN_WORDS = {
    "survey_experiment": {"phrase": "survey experiment", "unit": "arm", "units": "arms", "reference": "control group",
                          "relation": "effect", "relations": "effects", "estimate": "treatment effect",
                          "estimates": "treatment effects", "verb": "changed"},
    "conjoint": {"phrase": "conjoint experiment", "unit": "level", "units": "levels", "reference": "reference level",
                 "relation": "effect", "relations": "effects", "estimate": "AMCE", "estimates": "AMCEs", "verb": "changed"},
    "observational_survey": dict(phrase="observational survey study", **_ASSOCIATIONAL),
    "panel": dict(phrase="panel study", **_ASSOCIATIONAL),
    "methods_comparison": {"phrase": "methods comparison", "unit": "method", "units": "methods", "reference": "benchmark",
                           "relation": "gap", "relations": "gaps", "estimate": "discrepancy", "estimates": "discrepancies",
                           "verb": "differed from"},
    "other": dict(phrase="study", **_ASSOCIATIONAL),
}
DESIGN_WORDS["field_experiment"] = dict(DESIGN_WORDS["survey_experiment"], phrase="field experiment")
DESIGN_WORDS["lab_experiment"] = dict(DESIGN_WORDS["survey_experiment"], phrase="lab experiment")
DESIGN_WORDS["audit"] = dict(DESIGN_WORDS["survey_experiment"], phrase="audit experiment")


def design_type(pap: dict) -> str:
    t = str((pap.get("design") or {}).get("type") or "survey_experiment")
    return t if t in DESIGN_WORDS else "other"


def is_causal(pap: dict) -> bool:
    """design.causal when the plan sets it; otherwise true for experiments, false for everything else."""
    c = (pap.get("design") or {}).get("causal")
    if isinstance(c, bool):
        return c
    return design_type(pap) in CAUSAL_TYPES


def sample_kind(pap: dict) -> str:
    k = str((pap.get("design") or {}).get("sample_kind") or "human")
    return k if k in SAMPLE_KINDS else "human"


def words(pap: dict) -> dict:
    """The vocabulary row for this plan, plus "causal" and "sample_kind"."""
    t = design_type(pap)
    row = dict(DESIGN_WORDS[t])
    causal = is_causal(pap)
    if not causal and t != "methods_comparison":
        row.update(_ASSOCIATIONAL)
    row["type"], row["causal"], row["sample_kind"] = t, causal, sample_kind(pap)
    return row


def _get(d: dict, dotted: str):
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _norm(v):
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    if v in ("", None, [], {}):
        return None
    return str(v) if not isinstance(v, (dict, list)) else v


def arm_str(v) -> str:
    """Canonical string for an arm value: integral numbers (1, 1.0, "1.0") become "1"."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    sv = str(v).strip()
    try:
        f = float(sv)
    except ValueError:
        return sv
    return str(int(f)) if f.is_integer() else sv


def is_multiarm(h: dict) -> bool:
    t = h.get("treatment") or {}
    return isinstance(t.get("arms"), list) and len(t["arms"]) > 0


def hypothesis_arms(h: dict) -> tuple[list[str], str | None]:
    """(treated arm values, control value) for a hypothesis, as strings."""
    t = h.get("treatment") or {}
    if is_multiarm(h):
        return [arm_str(a) for a in t["arms"]], arm_str(t.get("control")) if t.get("control") is not None else None
    c = t.get("contrast") or [None, None]
    return ([arm_str(c[0])] if c[0] is not None else []), (arm_str(c[1]) if len(c) > 1 and c[1] is not None else None)


def design_arms(pap: dict) -> tuple[list[str], str | None]:
    """(treated arm values, control value) from design.arms, as strings."""
    a = pap.get("design", {}).get("arms", {}) or {}
    t = a.get("treatment")
    treated = [arm_str(x) for x in t] if isinstance(t, list) else ([arm_str(t)] if t not in (None, "") else [])
    return treated, (arm_str(a["control"]) if a.get("control") not in (None, "") else None)


def arm_label(pap: dict, arm) -> str:
    labels = pap.get("design", {}).get("arms", {}).get("labels") or {}
    return str(labels.get(arm_str(arm), arm_str(arm)))


def normalize_arms(pap: dict) -> dict:
    """Cast every arm value in the plan to its canonical string (in place; returns pap)."""
    a = pap.get("design", {}).get("arms")
    if isinstance(a, dict):
        if isinstance(a.get("treatment"), list):
            a["treatment"] = [arm_str(x) for x in a["treatment"]]
        elif a.get("treatment") not in (None, ""):
            a["treatment"] = arm_str(a["treatment"])
        if a.get("control") not in (None, ""):
            a["control"] = arm_str(a["control"])
        for k in ("labels", "descriptions"):
            if isinstance(a.get(k), dict):
                a[k] = {arm_str(kk): v for kk, v in a[k].items()}
    for section in ("registered", "implemented"):
        for h in (pap.get(section) or {}).get("hypotheses") or []:
            t = h.get("treatment")
            if not isinstance(t, dict):
                continue
            if isinstance(t.get("arms"), list):
                t["arms"] = [arm_str(x) for x in t["arms"]]
            if t.get("control") not in (None, ""):
                t["control"] = arm_str(t["control"])
            if isinstance(t.get("contrast"), list):
                t["contrast"] = [arm_str(x) for x in t["contrast"]]
    return pap


def registration_status(pap: dict) -> str:
    r = pap.get("registration")
    if isinstance(r, dict) and str(r.get("status", "registered")).lower() == "none":
        return "none"
    return "registered"


def is_pooled(h: dict) -> bool:
    return is_multiarm(h) and bool(h.get("pooled", True))


def load_pap(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_pap(pap: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(pap, indent=2, ensure_ascii=False), encoding="utf-8")


def outcome_changes(pap: dict) -> list[str]:
    """Hypotheses whose implemented outcome is a different measure from the registered one
    (different name AND different source columns). Renaming an outcome over the same columns is fine;
    analysing a different construct because the planned one is missing is not something to do silently."""
    def by_name(section):
        return {o.get("name"): o for o in (pap.get(section) or {}).get("outcomes", []) if isinstance(o, dict)}
    reg_o, imp_o = by_name("registered"), by_name("implemented")
    reg_h = {h.get("id"): h for h in (pap.get("registered") or {}).get("hypotheses", []) if isinstance(h, dict)}
    out = []
    for h in (pap.get("implemented") or {}).get("hypotheses", []):
        r = reg_h.get(h.get("id"))
        if not r:
            continue
        rn, iname = r.get("outcome"), h.get("outcome")
        if rn == iname:
            continue
        rcols = set((reg_o.get(rn) or {}).get("columns") or [])
        icols = set((imp_o.get(iname) or {}).get("columns") or [])
        if rcols and rcols == icols:
            continue
        out.append(f"{h.get('id')}: plan outcome '{rn}' ({', '.join(sorted(rcols)) or 'no matching columns'}) "
                   f"was replaced by '{iname}' ({', '.join(sorted(icols)) or 'custom'})")
    return out


ADDENDUM_DELTA_KEYS = ("estimator", "exclusions", "pooled", "collapse_arms")
ADDENDUM_ESTIMATOR_KEYS = ("kind", "robust", "cluster", "weights", "covariates", "continuous", "categorical", "absorb", "period")


def apply_addendum(base_h: dict, add: dict) -> dict:
    """Materialise an addendum as a hypothesis dict: the base hypothesis with the delta applied."""
    h = json.loads(json.dumps(base_h))
    h["id"] = add["id"]
    h["text"] = f"{base_h.get('text', '').rstrip('.')} (robustness: {add.get('label', '')})"
    delta = add.get("delta") or {}
    if isinstance(delta.get("estimator"), dict):
        est = h.setdefault("estimator", {})
        for k, v in delta["estimator"].items():
            if k in ADDENDUM_ESTIMATOR_KEYS:
                est[k] = v
    if delta.get("exclusions"):
        h["exclusions"] = list(base_h.get("exclusions") or []) + [str(x) for x in delta["exclusions"]]
    if "pooled" in delta:
        h["pooled"] = bool(delta["pooled"])
    if delta.get("collapse_arms") and isinstance((base_h.get("treatment") or {}).get("arms"), list):
        t = base_h["treatment"]
        # one treatment indicator: any treated arm vs control, in one two-arm model (no pooling approximation)
        h["treatment"] = {"column": t["column"], "contrast": ["__treated__", t["control"]], "collapse_from": list(t["arms"])}
        h["pooled"] = False
    h["subgroup"] = None
    h["base"] = base_h["id"]
    h["responds_to"] = add.get("responds_to")
    return h


def expand_addenda(pap: dict) -> dict:
    """Copy of the plan with approved/run addenda appended to implemented.hypotheses (ids like H2a)."""
    out = json.loads(json.dumps(pap))
    adds = [a for a in pap.get("addenda", []) if a.get("status") in ("approved", "run")]
    if not adds:
        return out
    imp = out["implemented"]
    by = {h["id"]: h for h in imp["hypotheses"]}
    for a in adds:
        base = by.get(a.get("base"))
        if base is None or a["id"] in by:
            continue
        h = apply_addendum(base, a)
        imp["hypotheses"].append(h)
        by[h["id"]] = h
    return out


def next_addendum_id(pap: dict, base: str, taken: set | None = None) -> str:
    taken = set(taken or ()) | {a.get("id") for a in pap.get("addenda", [])} | {h["id"] for h in pap["implemented"]["hypotheses"]}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        cand = f"{base}{letter}"
        if cand not in taken:
            return cand
    raise ValueError(f"too many addenda on {base}")


def addendum_errors(pap: dict, add: dict, data_columns: list[str]) -> list[str]:
    """Why a proposed addendum cannot run: bad base, forbidden fields, unknown columns, invalid estimator."""
    errs = []
    by = {h["id"]: h for h in pap["implemented"]["hypotheses"]}
    if add.get("base") not in by:
        return [f"base hypothesis {add.get('base')!r} does not exist"]
    delta = add.get("delta") or {}
    if not isinstance(delta, dict) or not delta:
        return ["delta is empty"]
    for k in delta:
        if k not in ADDENDUM_DELTA_KEYS:
            errs.append(f"delta may not change {k!r} (allowed: {', '.join(ADDENDUM_DELTA_KEYS)})")
    if isinstance(delta.get("estimator"), dict):
        for k in delta["estimator"]:
            if k not in ADDENDUM_ESTIMATOR_KEYS:
                errs.append(f"estimator.{k} may not be changed by an addendum")
    if delta.get("collapse_arms") and not is_multiarm(by[add["base"]]):
        errs.append("collapse_arms only applies to a multi-arm hypothesis")
    if errs:
        return errs
    trial = json.loads(json.dumps(pap))
    trial.setdefault("addenda", [])
    trial["addenda"] = [a for a in trial["addenda"] if a.get("id") != "__trial__"]
    trial["addenda"].append(dict(add, id="__trial__", status="approved"))
    return validate(expand_addenda(trial), data_columns)


def validate(pap: dict, data_columns: list[str]) -> list[str]:
    """Return a list of problems (empty = ok). Checks structure and that implemented specs
    reference existing columns."""
    errs = []
    for section in ("registered", "implemented"):
        s = pap.get(section)
        if not isinstance(s, dict):
            errs.append(f"missing section '{section}'")
            continue
        for k in ("outcomes", "hypotheses"):
            if not isinstance(s.get(k), list) or not s.get(k):
                errs.append(f"{section}.{k} must be a non-empty list")
    if errs:
        return errs
    cols = set(data_columns)
    design = pap.get("design") or {}
    if "causal" in design and not isinstance(design["causal"], bool):
        errs.append("design.causal must be true or false")
    if design.get("sample_kind") not in (None, *SAMPLE_KINDS):
        errs.append(f"design.sample_kind must be one of {', '.join(SAMPLE_KINDS)}")
    if design.get("sample_kind") in ("synthetic", "mixed") and not (design.get("synthetic") or {}).get("model"):
        errs.append("design.sample_kind is synthetic or mixed, so design.synthetic.model must name the generating model")
    derived = design.get("derived") or {}
    if not isinstance(derived, dict) or any(not isinstance(v, str) for v in derived.values()):
        errs.append("design.derived must map new column names to pandas eval expression strings")
        derived = {}
    cols |= {str(k) for k in derived}
    imp = pap["implemented"]
    out_names = {o.get("name") for o in imp["outcomes"]}
    for o in imp["outcomes"]:
        if o.get("kind", "mean_items") != "custom":
            for c in o.get("columns", []):
                if c not in cols:
                    errs.append(f"implemented outcome {o.get('name')} uses unknown column '{c}'")
    cols |= {str(n) for n in out_names if n}          # constructed outcomes exist after 02_clean and may serve as covariates
    for h in imp["hypotheses"]:
        if h.get("outcome") not in out_names:
            errs.append(f"hypothesis {h.get('id')} references unknown outcome '{h.get('outcome')}'")
        tcol = _get(h, "treatment.column")
        if tcol and tcol not in cols:
            errs.append(f"hypothesis {h.get('id')} treatment column '{tcol}' not in data")
        for c in _get(h, "estimator.covariates") or []:
            if c not in cols:
                errs.append(f"hypothesis {h.get('id')} covariate '{c}' not in data")
        w = _get(h, "estimator.weights")
        if w and w not in cols:
            errs.append(f"hypothesis {h.get('id')} weights column '{w}' not in data")
        cl = _get(h, "estimator.cluster")
        if cl and cl not in cols:
            errs.append(f"hypothesis {h.get('id')} cluster column '{cl}' not in data")
        if is_multiarm(h) and _get(h, "treatment.control") in (None, ""):
            errs.append(f"hypothesis {h.get('id')} has treatment.arms but no treatment.control")
        if _get(h, "estimator.kind") not in (None, *ESTIMATOR_KINDS):
            errs.append(f"hypothesis {h.get('id')} has unknown estimator.kind {_get(h, 'estimator.kind')!r}")
        for c in _get(h, "estimator.absorb") or []:
            if c not in cols:
                errs.append(f"hypothesis {h.get('id')} absorb column '{c}' not in data")
        if _get(h, "estimator.kind") == "did":
            period = _get(h, "estimator.period")
            if not period:
                errs.append(f"hypothesis {h.get('id')} uses estimator.kind 'did' but names no estimator.period column")
            elif period not in cols:
                errs.append(f"hypothesis {h.get('id')} period column '{period}' not in data")
            if is_multiarm(h) or _get(h, "treatment.continuous"):
                errs.append(f"hypothesis {h.get('id')}: 'did' needs a two-arm treatment contrast")
    for sg in imp.get("subgroups", []):
        if sg.get("moderator") not in cols:
            errs.append(f"subgroup {sg.get('id')} moderator '{sg.get('moderator')}' not in data")
    return errs


def _by_id(items: list[dict], key: str = "id") -> dict[str, dict]:
    return {str(x.get(key)): x for x in (items or [])}


def _is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _justification(pap: dict, hyp_id: str, field: str, kind: str = "deviation") -> str | None:
    for a in pap.get("ambiguities", []):
        if str(a.get("hypothesis")) == hyp_id and a.get("kind") == kind and \
                (a.get("field") == field or field.startswith(str(a.get("field")))):
            return f"{a.get('interpretation', '')} ({a.get('reason', '')})".strip()
    return None


def tag_for(analysis_id: str, tags: list[dict]) -> dict:
    """Tag row for an analysis id; "<HID>:<arm>" / "<HID>:pooled" sub-analyses inherit the row of <HID>."""
    by = {str(t.get("analysis_id")): t for t in tags}
    aid = str(analysis_id)
    if aid in by:
        return by[aid]
    base = aid.split(":", 1)[0]
    if base in by:
        return by[base]
    return {"analysis_id": aid, "kind": "hypothesis", "tag": "exploratory", "differences": [], "justification": ""}


def tag_analyses(pap: dict, exploratory: list[dict] | None = None) -> list[dict]:
    """One row per analysis: {analysis_id, kind, tag, differences, justification}.
    With pap.registration.status == "none" a matching spec is tagged "unregistered" instead of "registered"."""
    rows = []
    match_tag = "unregistered" if registration_status(pap) == "none" else "registered"
    reg_h, imp_h = _by_id(pap["registered"].get("hypotheses")), _by_id(pap["implemented"].get("hypotheses"))
    reg_o, imp_o = _by_id(pap["registered"].get("outcomes"), "name"), _by_id(pap["implemented"].get("outcomes"), "name")
    add_by = {a.get("id"): a for a in pap.get("addenda", []) if a.get("status") in ("approved", "run")}
    for hid, ih in imp_h.items():
        if hid in add_by:
            a = add_by[hid]
            base = imp_h.get(a.get("base")) or {}
            diffs = [{"field": f, "registered": _get(base, f), "implemented": _get(ih, f)}
                     for f in COMPARED_FIELDS if _norm(_get(base, f)) != _norm(_get(ih, f))]
            rows.append({"analysis_id": hid, "kind": "hypothesis", "tag": "robustness", "differences": diffs,
                         "justification": f"responds to reviewer issue {a.get('responds_to')}: {a.get('label', '')}",
                         "responds_to": a.get("responds_to"), "base": a.get("base")})
            continue
        rh = reg_h.get(hid)
        if rh is None:
            rows.append({"analysis_id": hid, "kind": "hypothesis", "tag": "exploratory",
                         "differences": [], "justification": "no registered counterpart"})
            continue
        diffs = []
        for f in COMPARED_FIELDS:
            if _norm(_get(rh, f)) != _norm(_get(ih, f)):
                diffs.append({"field": f, "registered": _get(rh, f), "implemented": _get(ih, f)})
        # outcome construction differences
        ro, io = reg_o.get(ih.get("outcome")), imp_o.get(ih.get("outcome"))
        if ro and io:
            for f in ("kind", "columns", "reverse"):
                if _norm(ro.get(f)) != _norm(io.get(f)):
                    diffs.append({"field": f"outcome.{f}", "registered": ro.get(f), "implemented": io.get(f)})
        # a documented choice where the plan was silent (empty registered value) fills a gap; it is not a deviation
        gaps = [d for d in diffs if _is_empty(d["registered"]) and _justification(pap, hid, d["field"], kind="interpretation")]
        devs = [d for d in diffs if d not in gaps]
        if not devs:
            rows.append({"analysis_id": hid, "kind": "hypothesis", "tag": match_tag, "differences": [],
                         "interpretations": gaps, "justification": ""})
        else:
            just = [j for j in (_justification(pap, hid, d["field"]) for d in devs) if j]
            rows.append({"analysis_id": hid, "kind": "hypothesis", "tag": "deviation", "differences": devs,
                         "interpretations": gaps,
                         "justification": "; ".join(dict.fromkeys(just)) if just else "undocumented deviation"})
    reg_s, imp_s = _by_id(pap["registered"].get("subgroups")), _by_id(pap["implemented"].get("subgroups"))
    for sid, isg in imp_s.items():
        rsg = reg_s.get(sid)
        if rsg is None:
            rows.append({"analysis_id": sid, "kind": "subgroup", "tag": "exploratory", "differences": [],
                         "justification": "no registered counterpart"})
            continue
        diffs = [{"field": f, "registered": rsg.get(f), "implemented": isg.get(f)}
                 for f in ("hypothesis", "moderator", "levels", "estimand", "covariates", "features") if _norm(rsg.get(f)) != _norm(isg.get(f))]
        if diffs:
            just = [j for j in (_justification(pap, sid, d["field"]) for d in diffs) if j]
            rows.append({"analysis_id": sid, "kind": "subgroup", "tag": "deviation", "differences": diffs,
                         "justification": "; ".join(just) if just else "undocumented deviation"})
        else:
            rows.append({"analysis_id": sid, "kind": "subgroup", "tag": match_tag, "differences": [], "justification": ""})
    for e in exploratory or []:
        rows.append({"analysis_id": e.get("id"), "kind": "exploratory", "tag": "exploratory", "differences": [],
                     "justification": "proposed by the exploratory agent; not pre-registered"})
    return rows


def tags_to_csv(rows: list[dict], path: str | Path) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["analysis_id", "kind", "tag", "n_differences", "differences", "justification"])
        for r in rows:
            w.writerow([r["analysis_id"], r["kind"], r["tag"], len(r["differences"]),
                        json.dumps(r["differences"], ensure_ascii=False), r["justification"]])


def registered_vs_implemented_table(pap: dict, tags: list[dict]) -> str:
    """Markdown table for the report."""
    lines = ["| Analysis | Tag | Registered | Implemented | Justification |", "|---|---|---|---|---|"]
    for t in tags:
        if t["kind"] == "exploratory":
            continue
        if t["differences"]:
            reg = "; ".join(f"{d['field']}={json.dumps(d['registered'])}" for d in t["differences"])
            imp = "; ".join(f"{d['field']}={json.dumps(d['implemented'])}" for d in t["differences"])
        else:
            reg = imp = "as planned" if registration_status(pap) == "none" else "as registered"
        lines.append(f"| {t['analysis_id']} | **{t['tag']}** | {reg} | {imp} | {t['justification']} |".replace("\n", " "))
    return "\n".join(lines)


# ---------------------------------------------------------------------------- plain prose
BRIEF_NAMES = {"advance_design": "the design advance", "generalizability_conditional": "the generalizability follow-up",
               "theoretical_debate": "the debate follow-up", "data_to_collect": "the data-collection plan",
               "natural_experiment": "the natural-experiment plan"}
CAUSE_WORDS = {"missing_moderator": "a missing moderator"}


def column_labels(pap: dict) -> dict:
    """Readable names for the plan's columns: outcome labels, and each moderator's name taken from its subgroup label
    ("... tertile of respondent ideology (1 very liberal ...)" -> "respondent ideology")."""
    out = {}
    for sec in ("implemented", "registered"):
        for o in (pap.get(sec) or {}).get("outcomes") or []:
            if o.get("name") and o.get("label"):
                out.setdefault(o["name"], o["label"][0].lower() + o["label"][1:] if o["label"][1:2].islower() else o["label"])
        for sg in (pap.get(sec) or {}).get("subgroups") or []:
            col, lab = sg.get("moderator"), str(sg.get("label") or "")
            if not col or col in out:
                continue
            m = re.search(r"(?:tertile|quartile|quintile|half|level|levels|group|groups) of (.+?)(?: \(|$)", lab)
            out[col] = m.group(1).strip() if m else re.sub(r"_(t|c|z|cat|bin)$", "", col).replace("_", " ")
    return out


def unregistered_words(text: str) -> str:
    """For a study with no pre-registration, its plan's analyses are 'planned', never 'registered'."""
    return re.sub(r"(?<!pre-)(?<!Pre-)\b([Rr])egistered\b", lambda m: "Planned" if m.group(1) == "R" else "planned", text or "")


def plain(text: str, labels: dict | None = None) -> str:
    """Rendered prose without internal names: brief ids become their names, cause keys become words, and column names
    the plan has labels for become those labels."""
    if not text:
        return text
    text = re.sub(r"\bthe (advance_design|generalizability_conditional|theoretical_debate|data_to_collect|natural_experiment) brief\b",
                  lambda m: BRIEF_NAMES[m.group(1)], text)
    text = re.sub(r"(?<![\w/.])(advance_design|generalizability_conditional|theoretical_debate|data_to_collect|natural_experiment)(?![\w/]|\.\w)",
                  lambda m: BRIEF_NAMES[m.group(1)], text)
    text = re.sub(r"\b(?:the )?(missing_moderator)(?: cause)?\b", lambda m: CAUSE_WORDS[m.group(1)], text)
    # agent first person in analysis notes ("so I check whether ...") -> "so this checks whether ..."
    def _this(m, word):
        start = m.start() == 0 or text[:m.start()].rstrip().endswith((".", "!", "?"))
        return ("This " if start else "this ") + word
    text = re.sub(r"\bI (check|test|compare|examine|ask|estimate)\b", lambda m: _this(m, m.group(1) + "s"), text)
    text = re.sub(r"\bI re-?(fit|estimate)\b", lambda m: _this(m, "re-" + m.group(1) + "s"), text)
    for col, lab in sorted((labels or {}).items(), key=lambda kv: -len(kv[0])):
        if "_" in col or col.isupper():          # plain words (e.g. "share") are left alone
            text = re.sub(rf"(?<![\w`/.]){re.escape(col)}(?![\w`/(]|\.\w)", lab, text)
    return text
