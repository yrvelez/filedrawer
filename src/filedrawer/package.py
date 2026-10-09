"""Study package writers: report.md skeleton, study.json, provenance.json, RUN.md."""
from __future__ import annotations

import re

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

from . import __version__
from . import pap as P
from .agents.base import csv_to_markdown
from .badges import badges_for, write_badges, markdown_row
from .pap import (words, is_causal, sample_kind, registered_vs_implemented_table, is_multiarm, is_pooled, hypothesis_arms, tag_for,
                  registration_status, design_arms, arm_label)

BADGES = {"fully_agentic": "FULLY AGENTIC — no human review recorded",
          "human_reviewed": "HUMAN REVIEWED — see provenance.human_steps"}


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _review_json(study: Path) -> dict:
    p = study / "review.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _claims_tally(rv: dict) -> dict | None:
    """{supported, total, open_analytical, on_revised_text} for the badge/record; None without a claim check."""
    from .review import claims_tally
    t = claims_tally(rv) if rv else None
    return t if t and t["total"] else None


def read_summary(study: Path) -> list[dict]:
    p = study / "results" / "registered_summary.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _f(x, d=3):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if d >= 4 and 0 < v < 1e-4:
        return "< 0.0001"
    return f"{v:.{d}f}"


def _pv(x) -> str:
    """A p-value for prose: "p = 0.012", or "p < 0.001" rather than a misleading "p = 0.000"."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return f"p = {x}"
    return "p < 0.001" if 0 <= v < 0.0005 else f"p = {v:.3f}"


def _yn(x) -> str:
    return "yes" if str(x) == "True" else "no"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _arms_desc(pap: dict) -> str:
    treated, control = design_arms(pap)
    col = pap.get("design", {}).get("arms", {}).get("column")
    w = words(pap)
    if len(treated) > 1:
        tl = ", ".join(f"{a} = {arm_label(pap, a)}" if arm_label(pap, a) != a else a for a in treated)
        return f"`{col}`: {len(treated)} {w['units']} ({tl}) vs {w['reference']} {control} = {arm_label(pap, control)}"
    return f"`{col}` = {pap.get('design', {}).get('arms', {}).get('treatment')} vs {pap.get('design', {}).get('arms', {}).get('control')}"


POOLED_CAVEAT = ("Caveat: the arm effects share one control group, so they are not independent; the random-effects "
                 "pooling treats them as if they were, and its standard error and heterogeneity statistics are approximate.")


def _arms_table(rows: list[dict], unit: str = "Arm") -> str:
    L = [f"| {unit} | Estimate | SE | p | 95% CI | n ({unit.lower()}) | Supported |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r.get('arm_label', r.get('arm_code'))} | {_f(r['estimate'])} | {_f(r['std_error'])} | {_f(r['p_value'], 4)} | "
                 f"[{_f(r['conf_low'])}, {_f(r['conf_high'])}] | {r.get('n_arm', '')} | {_yn(r.get('supported'))} |")
    return "\n".join(L)


def _feature_label(pap: dict, column: str) -> str:
    for f in (pap.get("design") or {}).get("features") or []:
        if f.get("column") == column:
            return f.get("label") or column
    return column


def _heading(pap: dict, h: dict) -> str:
    """Section heading for a hypothesis: its own short label when the plan gives one, else the outcome."""
    return h.get("label") or _outcome_label(pap, h["outcome"])


def _group_section(study: Path, pap: dict, gid: str, members: list[dict], tags: list, sections: dict,
                   unregistered: bool, suffix: str) -> list[str]:
    """One compact section for a group of hypotheses (e.g. re-weighted AMCEs): a single table with each level's
    estimate beside the estimate of the same feature and outcome without weights, when the plan has one."""
    imp = pap["implemented"]["hypotheses"]
    title = (pap["implemented"].get("groups") or {}).get(gid, gid)
    L = [f"<!-- fd:group id={gid} -->\n### {title}\n", f"*{_hypothesis_label(tag_for(members[0]['id'], tags), unregistered, suffix)}. "
         f"Analyses {members[0]['id']}–{members[-1]['id']}.*\n"]
    note = (sections.get("results", {}) or {}).get(gid, "").strip()
    if note:
        L.append(note + "\n")
    L.append("| Outcome | Feature | Level | Estimate | 95% CI | p | Without weights |")
    L.append("|---|---|---|---|---|---|---|")
    for h in members:
        col = (h.get("treatment") or {}).get("column")
        base = next((b for b in imp if b is not h and not b.get("group") and b.get("outcome") == h.get("outcome")
                     and (b.get("treatment") or {}).get("column") == col and not (b.get("estimator") or {}).get("weights")), None)
        base_rows = {r.get("arm_code"): r for r in read_rows(study / "results" / f"{base['id']}_arms.csv")} if base else {}
        for r in read_rows(study / "results" / f"{h['id']}_arms.csv"):
            b = base_rows.get(r.get("arm_code"))
            L.append(f"| {_short_label(_outcome_label(pap, h['outcome']))} | {_feature_label(pap, col)} | {r.get('arm_label', r.get('arm_code'))} | "
                     f"{_f(r['estimate'])} | [{_f(r['conf_low'])}, {_f(r['conf_high'])}] | {'< 0.001' if _f(r['p_value'], 3) == '0.000' else _f(r['p_value'], 3)} | "
                     f"{_f(b['estimate']) + ' (' + base['id'] + ')' if b else ''} |")
    L.append("")
    return L


def _hypothesis_label(tag: dict, unregistered: bool, suffix: str) -> str:
    """Short, sentence-case status line printed under a hypothesis heading."""
    planned = "Post hoc, not pre-registered"
    return {"registered": planned if unregistered else "Pre-registered", "unregistered": planned,
            "deviation": f"Deviation from {'the original analysis plan' if unregistered else 'pre-registration'}: {tag.get('justification', '')}",
            "robustness": f"Robustness check added in response to the automated reviewer; {tag.get('justification', '')}",
            "exploratory": "Exploratory, not pre-registered"}.get(tag.get("tag", "exploratory"), "Exploratory, not pre-registered")


def _outcome_label(pap: dict, name: str) -> str:
    for o in pap.get("implemented", {}).get("outcomes", []):
        if o.get("name") == name and o.get("label"):
            return o["label"]
    return name


def _is_true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _short_label(text: str) -> str:
    """Outcome label without its parenthetical definition, for compact tables."""
    return re.sub(r"\s*\([^)]*\)", "", text).strip()


def _key_findings(pap: dict, summary: dict, max_rows: int = 6) -> list[str]:
    """Deterministic 'key findings' table: significant arm effects (largest first) and every pooled estimate.
    Numbers come straight from results/registered_summary.csv."""
    rows = []
    hyp_order = {h.get("id"): i for i, h in enumerate(pap.get("implemented", {}).get("hypotheses", []))}
    addenda_ids = {a.get("id") for a in pap.get("addenda", [])} | {h["id"] for h in pap.get("implemented", {}).get("hypotheses", []) if h.get("base")}
    registered_ids = {h.get("id") for h in pap.get("registered", {}).get("hypotheses", [])}
    for key, r in summary.items():
        if "|" in key and r.get("arm") in (None, ""):
            continue                        # subgroup interaction rows
        if registered_ids and key.split(":", 1)[0].split("|", 1)[0] not in registered_ids:
            continue                        # exploratory analyses are never headline findings
        if key.split(":", 1)[0] in addenda_ids:
            continue                        # robustness addenda are reported in their own section, never as headline findings
        try:
            e, se = float(r["estimate"]), float(r["std_error"])
        except (TypeError, ValueError):
            continue
        try:
            p = float(r.get("p_value"))
        except (TypeError, ValueError):
            p = float("nan")
        base = key.split(":", 1)[0]
        arm = r.get("arm")
        outcome = _short_label(_outcome_label(pap, r.get("outcome", "")))
        order = hyp_order.get(base, 99)
        ci = f"[{e - 1.96 * se:+.3f}, {e + 1.96 * se:+.3f}]"
        if arm == "pooled":
            k = r.get("k_arms")
            k = int(float(k)) if k not in (None, "") else None
            what = f"Pooled across {k} {words(pap)['units']}: {outcome}" if k else f"Pooled: {outcome}"
            rows.append((2, order, -abs(e), f"{e:+.3f}", what, ci, p, base))
        elif _is_true(r.get("supported")):
            who = arm_label(pap, str(arm)) if arm not in (None, "") else base
            rows.append((1, order, -abs(e), f"{e:+.3f}", f"{who}: {outcome}", ci, p, base))
    rows.sort()
    sig = [r for r in rows if r[0] == 1][:max_rows]
    pooled = [r for r in rows if r[0] == 2]
    out = sig + pooled
    if not out:
        return []
    L = ["| Estimate | Finding | 95% CI | p |", "|---|---|---|---|"]
    for _, _, _, est, what, ci, p, base in out:
        ptxt = "<0.001" if p == p and p < 0.001 else _f(p, 3)
        L.append(f"| **{est}** | {what} ({base}) | {ci} | {ptxt} |")
    return L


def _model_block(h: dict, pap: dict, first: dict | None) -> list[str]:
    """Model details kept out of the prose, in one code block."""
    est = h.get("estimator", {})
    kind = {"lin": "Lin (2013) covariate adjustment", "ols": "OLS", "diff_means": "difference in means",
            "did": "difference in differences (treat x post)"}.get(est.get("kind", "ols"), est.get("kind"))
    if est.get("absorb"):
        kind += f", fixed effects for {', '.join(est['absorb'])}"
    lines = []
    if first and first.get("formula"):
        lines.append(str(first["formula"]))
    bits = [kind]
    if est.get("weights"):
        bits.append(f"weights = {est['weights']}")
    if first:
        bits.append(f"cluster-robust SEs on {est.get('cluster')}" if first.get("cov_type") == "cluster" else f"{first.get('cov_type')} robust SEs")
        bits.append(f"N = {first.get('n')}")
    bits.append(f"{str(h.get('direction', 'two_sided')).replace('_', '-')} test, alpha = {pap['implemented'].get('alpha', 0.05)}")
    lines.append(" | ".join(str(b) for b in bits if b))
    return ["```", *lines, "```"]


def _multiarm_section(study: Path, pap: dict, h: dict, summary: dict, note: str = "", figure: bool = True) -> list[str]:
    L: list[str] = []
    arms, control = hypothesis_arms(h)
    rows = read_rows(study / "results" / f"{h['id']}_arms.csv")
    first = next((v for k, v in summary.items() if k.startswith(h["id"] + ":") and v.get("arm") not in ("pooled", "", None)), None)
    pooled = summary.get(h["id"] + ":pooled")
    if figure and (study / "figures" / f"{h['id']}_arms.png").exists():
        L.append(f"![{h['id']}: effect by arm](figures/{h['id']}_arms.png)\n")
    if note:
        L.append(note + "\n")
    if pooled:
        k = int(float(pooled.get("k_arms") or len(rows)))
        L.append(f"Pooling the {k} arm effects with a random-effects model gives {_f(pooled['estimate'])} (SE {_f(pooled['std_error'])}, "
                 f"{_pv(pooled['p_value'])}; tau² {_f(pooled.get('tau2'), 4)}, I² {_f(pooled.get('i2'), 2)}). "
                 f"The arms share one control group, so this pooled standard error is approximate.\n")
    elif is_pooled(h):
        L.append("(pooled estimate not available)\n")
    L.append(f"#### Details: model and estimates by arm ({h['id']})\n")
    L += _model_block(h, pap, first)
    if rows:
        L.append("")
        L.append(_arms_table(rows, words(pap)["unit"].capitalize()))
    else:
        L.append(f"(missing: {h['id']}_arms.csv)")
    return L


DISPOSITION_LABEL = {"address": "Robustness check proposed", "answered": "Answered with a robustness check", "editorial": "Fixed in text",
                     "declined": "Declined", "unresolved": "Left for a follow-up study", None: "Open"}

REVIEW_OPTIONS = [
    ("light", "Light Pass", "checks every reported estimate against the result tables and each analysis against the "
     "pre-analysis plan; the default (a few cents)", "--review light"),
    ("coarse", "Coarse", "the open-source coarse-ink reviewer, run on your machine (about $1-2), then imported",
     "uvx coarse-ink review report.md, then filedrawer review-import . coarse FILE"),
    ("refine", "Refine", "upload the report to refine.ink, then import its review", "filedrawer review-import . refine FILE"),
    ("openreview", "OpenReview or any referee report", "import a review posted on OpenReview, or any other referee report",
     "filedrawer review-import . openreview FILE"),
]


def _short(text, n: int = 220) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _cell(text, n: int = 400) -> str:
    return _short(text, n).replace("|", "/")


def _review_section(ctx: dict, summary: dict) -> list[str]:
    """The review as flagged corrections: what the review found and what the agent did about each serious item
    (reworded a claim, ran a robustness check, fixed the text, declined, or left it open), the full log folded
    below, and the other review options. Built from review.json and the plan's addenda; no person is implied."""
    from .review import claims_tally, tally_sentence, TITLES, parse_modes
    from .address import _row, _fmt
    study: Path = ctx["study_dir"]
    path = study / "review.json"
    if not path.exists():
        return []
    rv = json.loads(path.read_text(encoding="utf-8"))
    issues, syn, so = rv.get("issues") or [], rv.get("synthesis") or {}, rv.get("signoff") or {}
    addenda = {a.get("id"): a for a in (ctx["pap"].get("addenda") or [])}
    if (study / "pap.json").exists():            # the saved plan carries the final status of each addendum
        addenda.update({a.get("id"): a for a in json.loads((study / "pap.json").read_text(encoding="utf-8")).get("addenda") or []})
    ran = [a for a in addenda.values() if a.get("status") == "run"]
    if not issues and not syn and not ran:
        return []
    try:
        modes = parse_modes(rv.get("modes") or rv.get("mode") or "light")
    except ValueError:
        modes = ["light"]
    rnd = rv.get("round") or (len(rv.get("rounds") or []) + 1)
    names = " + ".join(TITLES.get(m, m).split(" (")[0] for m in modes)
    models = dict(rv.get("models") or {})
    if rv.get("model") and not any(k != "orchestrator" for k in models):
        models = {"light": rv["model"], **models}
    referee = ", ".join(f"`{v}`" for k, v in models.items() if k != "orchestrator") or "`?`"
    checker = models.get("orchestrator")
    L = [f"\n<!-- fd:section id=review round={rnd} -->\n## Review\n"]
    light_only = modes == ["light"]
    what = ("The Light Pass does two things: it checks every reported estimate against the result tables, and every analysis "
            "against the pre-analysis plan. It does not judge the design, methods or interpretation; see the other review options. "
            if light_only else "")
    L.append(f"*{names} review: referee {referee}" + (f", checking agent `{checker}`" if checker else "") + f". {what}"
             "The agent applied the corrections below itself; no person reviewed or revised this report. "
             "Registered analyses are never changed" + ("." if light_only else ": robustness checks sit beside them.") + "*\n")

    # ---- what was flagged and what the agent did -----------------------------------------------------------
    notes = rv.get("revision_notes") or (ctx.get("sections") or {}).get("revision_notes") or []

    def note_for(prefix: str) -> str:
        for n in notes:
            head, _, rest = str(n).partition(":")
            if head.strip().upper() == prefix.upper():
                return rest.strip()
        return ""

    after = {c.get("previous"): c for c in (so.get("claims") or []) if c.get("previous")}
    rows: list[tuple[int, str]] = []                      # (order, one-line bullet): open items first
    n_reworded = n_open = n_text = 0
    k = 0
    for n, c in enumerate(syn.get("claims") or [], 1):
        if c.get("verdict") not in ("overstated", "unsupported"):
            continue
        k += 1
        a = after.get(f"C{n}")
        if a:
            now = a.get("verdict", "")
            gone = "removed" in str(a.get("evidence", "")).lower()
            if now == "supported":
                n_reworded += 1
                rows.append((3, f"- **{'Removed' if gone else 'Reworded'}** · {c.get('location', '')}"
                                + ("" if gone else f": now “{_short(a.get('claim'), 120)}”")))
            else:
                n_open += 1
                rows.append((0, f"- **Still {now}** · {c.get('location', '')}: “{_short(a.get('claim'), 110)}” ({_short(a.get('evidence'), 110)})"))
        else:
            fixed = note_for(f"K{k}")
            n_reworded += bool(fixed)
            n_open += not fixed
            rows.append((3 if fixed else 0, f"- **{'Reworded' if fixed else 'Flagged ' + c['verdict']}** · {c.get('location', '')}: "
                                            + _short(fixed or c.get("claim"), 120)))
    flagged_first = {f"C{n}" for n, c in enumerate(syn.get("claims") or [], 1) if c.get("verdict") in ("overstated", "unsupported")}
    for a in so.get("claims") or []:                   # flagged only on the re-check of the corrected text
        if a.get("verdict") in ("overstated", "unsupported") and a.get("previous") not in flagged_first:
            n_open += 1
            rows.append((0, f"- **Now {a['verdict']}** · {a.get('location', '')}: “{_short(a.get('claim'), 110)}” ({_short(a.get('evidence'), 110)})"))
    rows_s = read_summary(study)

    def est(hid: str) -> str:
        r = _row(rows_s, hid)
        try:
            return f"{float(r['estimate']):+.3f} ({_pv(r['p_value'])})"
        except (TypeError, KeyError, ValueError):
            return "no estimate"

    for a in sorted(ran, key=lambda x: x.get("id", "")):
        who = "" if a.get("approved_by") in (None, "", "agent", "unattended (--yes)") else f" (approved by {a['approved_by']})"
        rows.append((1, f"- **Robustness check {a['id']}** · {a.get('responds_to', '')}: {_short(a.get('label'), 80)}{who}. "
                        f"{a['base']} {est(a['base'])}, {a['id']} {est(a['id'])}"))
    answered = {a.get("responds_to") for a in ran}
    noted = {str(x).partition(":")[0].strip().upper() for x in notes}
    declined = 0
    for i in issues:
        if i.get("source") == "claims" or i.get("id") in answered or i.get("answered_by"):
            continue
        sev, disp = i.get("severity"), i.get("disposition")
        if disp == "declined":
            declined += 1
            rows.append((4, f"- **Declined** · {i['id']}: {_short(i.get('disposition_reason') or i.get('issue'), 140)}"))
        elif disp == "editorial" and sev in ("high", "medium"):
            if str(i["id"]).upper() in noted:
                n_text += 1
                rows.append((3, f"- **Fixed in text** · {i['id']}, {i.get('location', '')}: {_short(note_for(i['id']), 120)}"))
            else:
                n_open += 1
                rows.append((0, f"- **Not yet fixed** · {i['id']}, {i.get('location', '')}: {_short(i.get('issue'), 120)}"))
        elif i.get("kind") == "analytical" and sev in ("high", "medium") and disp in (None, "address"):
            n_open += 1
            rows.append((0, f"- **No robustness check** · {i['id']}, {i.get('location', '')}: {_short(i.get('issue'), 120)}"))
    for order, label in ((3, ("**Reworded**", "**Removed**", "**Fixed in text**")),):
        done_rows = [r for o, r in rows if o == order]
        if len(done_rows) > 2:                       # routine successes collapse to one line; the log has each before/after
            locs = []
            for r in done_rows:
                loc = r.split(" · ", 1)[1].split(":", 1)[0].strip()
                locs.append(loc)
            counts: dict[str, int] = {}
            for loc in locs:
                counts[loc] = counts.get(loc, 0) + 1
            where = ", ".join(f"{k} ({v})" if v > 1 else k for k, v in counts.items())
            rows = [(o, r) for o, r in rows if o != order] + [(order, f"- **Corrected** · {len(done_rows)} items reworded or fixed in the text: "
                                                                       f"{where}. Before and after are in the log below.")]
    pm = rv.get("plan_match") or {}
    for pr in pm.get("problems") or []:
        n_open += 1
        rows.append((0, f"- **Plan mismatch** · {pr['id']}: {pr['problem']}"))
    rows.sort(key=lambda r: r[0])
    t = claims_tally(rv)
    parts = [f"{t['supported']} of {t['total']} checked claims supported after the agent's corrections"
             + (f" ({t['corrected']} flagged claim{'s' if t['corrected'] != 1 else ''} corrected)" if t.get("corrected") else "")
             if t.get("total") and t.get("on_revised_text")
             else (f"{t['supported']} of {t['total']} checked claims supported before corrections" if t.get("total") else "no claims checked")]
    if pm.get("rows"):
        from .review.plan_match import sentence as plan_sentence
        parts.append(plan_sentence(pm))
    for nn, word in ((n_reworded, "reworded"), (len(ran), "robustness check" + ("s" if len(ran) != 1 else "") + " run"),
                     (n_text, "text fix" + ("es" if n_text != 1 else "")), (declined, "declined"), (n_open, "still open")):
        if nn:
            parts.append(f"{nn} {word}")
    if rv.get("correction_passes"):
        parts.append(f"{rv['correction_passes']} correction pass{'es' if rv['correction_passes'] != 1 else ''}")
    L.append(f"**Outcome.** {'; '.join(parts)}.\n")
    if rows:
        L.append("#### Corrections\n")
        L += [r for _, r in rows]
        L.append("")
    else:
        L.append("Nothing serious was flagged.\n")

    # ---- the full log, folded ------------------------------------------------------------------------------
    L.append("#### Details: full review log\n")
    role = {"light": "referee", "orchestrator": "checking agent"}
    L.append("Models: " + ", ".join(f"{role.get(k, k)} `{v}`" for k, v in models.items()) + ".\n")
    if rv.get("overall"):
        L.append(f"**Assessment.** {rv['overall'].strip()}\n")
    if pm.get("rows"):
        L.append("| Registered analysis | Against the plan | Differences | Stated reason |")
        L.append("|---|---|---|---|")
        L += [f"| {r['id']} | {r['status']} | {', '.join(r['differences']) or '—'} | {_cell(r['reason'], 200) or '—'} |" for r in pm["rows"]]
        L.append("")
    for r in rv.get("rounds") or []:
        L.append(f"*Round {r.get('round')}: {len(r.get('issues') or [])} issue(s); {tally_sentence(claims_tally(r))}.*\n")
    if issues:
        L.append("| Issue | Severity | Kind | Source | Outcome | What the referee said |")
        L.append("|---|---|---|---|---|---|")
        for i in sorted(issues, key=lambda x: ({"high": 0, "medium": 1, "low": 2}.get(x.get("severity"), 3), x.get("id", ""))):
            out = DISPOSITION_LABEL.get("answered" if i.get("id") in answered else i.get("disposition"), i.get("disposition") or "Open")
            reason = i.get("disposition_reason") or ""
            L.append(f"| {i['id']} | {i.get('severity', '')} | {i.get('kind', '')} | {i.get('source', 'light')} | {out} | "
                     f"{_cell((i.get('location', '') + ': ' if i.get('location') else '') + str(i.get('issue', '')))}"
                     + (f" *{_cell(reason, 200)}*" if reason else "") + " |")
        L.append("")
    if syn.get("claims"):
        L.append("| Claim | Where | Verdict | Evidence |" + (" After corrections |" if so else ""))
        L.append("|---|---|---|---|" + ("---|" if so else ""))
        for n, c in enumerate(syn["claims"], 1):
            row = f"| {_cell(c.get('claim'))} | {c.get('location', '')} | {c.get('verdict', '')} | {_cell(c.get('evidence'))} |"
            if so:
                a = after.get(f"C{n}")
                row += f" {a.get('verdict', '')}: {_cell(a.get('claim'))} |" if a else " — |"
            L.append(row)
        L.append("")
    if syn.get("editorial"):
        L.append("Corrections the checking agent asked for, and what the writing agent did:\n")
        L += [f"- G{n}. {_short(e, 240)}" + (f" Done: {_short(note_for(f'G{n}'), 200)}" if note_for(f"G{n}") else "")
              for n, e in enumerate(syn["editorial"], 1)]
        L.append("")
    if so.get("note"):
        L.append(f"Re-check of the corrected text: {so['note']}\n")
    if syn.get("unresolved"):
        L.append("Questions this study cannot settle (taken up under Proposed extensions): "
                 + " ".join(f"{u['id']}. {_short(u['question'], 200)}" for u in syn["unresolved"]) + "\n")

    # ---- other options ---------------------------------------------------------------------------------------
    others = [o for o in REVIEW_OPTIONS if o[0] not in modes]
    if others:
        L.append("#### Other review options\n")
        L += [f"- **{name}**: {what}. `{cmd}`" for _, name, what, cmd in others]
        L.append("")
    return L


CAUSE_LABEL = {"power": "Statistical power", "measurement": "Measurement", "design": "Design", "framing": "Framing",
               "missing_moderator": "A moderator not measured", "sample": "Sample", "analysis": "Analysis"}


def _plain_tree(obj, labels: dict):
    """A copy of an agent-written structure with internal names replaced in every string (see pap.plain)."""
    if isinstance(obj, dict):
        return {k: (v if k in ("id", "brief_id", "kind", "mode", "cause", "qsf", "svg", "text", "text_file", "table", "figure", "debate") else _plain_tree(v, labels)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain_tree(v, labels) for v in obj]
    return P.plain(obj, labels) if isinstance(obj, str) else obj


def _research_potential(ctx: dict) -> list[str]:
    """The research-potential memo as a report section: what stands, why the study did not land, the debates it bears
    on (with the retrieved works), the verdict. The briefs themselves appear as the proposed extensions."""
    memo = ctx.get("potential") or {}
    if not memo:
        return []
    memo = _plain_tree(memo, P.column_labels(ctx.get("pap") or {}))
    works = {str(w.get("doi") or w.get("id")): w for w in (ctx.get("lit") or {}).get("works") or []}
    works.update({str(w.get("id")): w for w in (ctx.get("lit") or {}).get("works") or []})

    def cite(wid: str) -> str:
        w = works.get(wid)
        if not w:
            return wid
        a = (w.get("authors") or ["?"])[0].split()[-1]
        return f"{a}{' et al.' if len(w.get('authors') or []) > 1 else ''} ({w.get('year')})"

    L = ["\n<!-- fd:section id=potential -->\n## Research potential\n",
         "*The agent's assessment of what this study can still become. The proposed extensions below are built from it.*\n"]
    if memo.get("got_right"):
        L.append("**What stands.** " + " ".join(s.rstrip(".") + "." for s in memo["got_right"]) + "\n")
    if memo.get("verdict"):
        L.append(f"**Verdict.** {memo['verdict'].strip()}\n")
    if memo.get("why_it_did_not_land") or memo.get("debates"):
        L.append("#### Details: why it may not have landed, and the debates it bears on\n")
    if memo.get("why_it_did_not_land"):
        L.append("**Why it may not have landed.**\n")
        L.append("| Cause | What happened | Evidence |")
        L.append("|---|---|---|")
        for c in memo["why_it_did_not_land"]:
            L.append(f"| {CAUSE_LABEL.get(c.get('cause'), c.get('cause'))} | {str(c.get('detail', '')).replace('|', '/')} | {str(c.get('evidence', '')).replace('|', '/')} |")
        L.append("")
    for d in memo.get("debates") or []:
        side = {"a": [], "b": [], "neutral": []}
        for w in d.get("works") or []:
            side.setdefault(w.get("side", "neutral"), []).append(cite(w["id"]))
        L.append(f"**{d['id']}. {d.get('label', '')}.** (a) {d.get('position_a', '')}" + (f" [{', '.join(side['a'])}]" if side["a"] else "")
                 + f" (b) {d.get('position_b', '')}" + (f" [{', '.join(side['b'])}]" if side["b"] else "")
                 + f" This study: {d.get('what_this_study_says', '')}\n")
    return L


def _provenance_line(ctx: dict) -> str:
    prov, meta = ctx["provenance"], ctx["meta"]
    tok = prov.get("tokens") or {}
    cost = prov.get("cost_usd")
    spend = (f" Model calls: ${cost:.2f}, {int(tok.get('in', 0)) / 1000:.0f}k tokens in and {int(tok.get('out', 0)) / 1000:.0f}k out."
             if cost else "")
    return (f"**Provenance: {BADGES[prov['mode']]}.** filedrawer {__version__}, {prov['created'][:10]}; "
            f"orchestrator `{prov['models']['strong']}`, standard `{prov['models']['fast']}`"
            f"{', zero data retention requested' if prov.get('zero_retention_requested') else ''}"
            f"{', run on local models (no data left the machine)' if prov.get('provider') == 'local' else ''}. "
            f"Reviewer pass: {'yes' if prov.get('reviewer_pass') else 'no'}. Human steps recorded: {len(prov.get('human_steps', []))}. "
            f"Release status: {meta.get('release_status', 'draft')}.{spend} Cite as: {citation(meta, prov, _doi(ctx))['text']}")


def _hypotheses_block(study: Path, pap: dict, tags: list, hyps: list, subgroups: list, summary: dict, sections: dict,
                      unregistered: bool, suffix: str, het_heading: str) -> list[str]:
    """Results for a list of hypotheses and subgroups (the registered set, or the authors' exploratory set)."""
    L: list[str] = []
    done_groups: set = set()
    conjoint = str((pap.get("design") or {}).get("type")) == "conjoint"
    for h in hyps:
        if h.get("group"):
            if h["group"] not in done_groups:
                done_groups.add(h["group"])
                L += _group_section(study, pap, h["group"], [g for g in hyps if g.get("group") == h["group"]], tags, sections,
                                    unregistered, suffix)
            continue
        t = tag_for(h["id"], tags)
        lab = _hypothesis_label(t, unregistered, suffix)
        L.append(f"<!-- fd:hyp id={h['id']} tag={t.get('tag', '')} outcome={h.get('outcome', '')} -->\n### {h['id']}. {_heading(pap, h)}\n")
        L.append(f"*{P.plain(h['text'].strip(), P.column_labels(pap))}*  \n*{lab}.*\n")
        note = (sections.get("results", {}) or {}).get(h["id"], "").strip()
        if is_multiarm(h):
            L += _multiarm_section(study, pap, h, summary, note=note, figure=not conjoint)
            L.append("")
            continue
        s = summary.get(h["id"])
        if s and (h.get("treatment") or {}).get("continuous"):
            L.append(f"Slope of {_outcome_label(pap, s['outcome'])} on `{h['treatment']['column']}`: {_f(s['estimate'])} "
                     f"(SE {_f(s['std_error'])}, {_pv(s['p_value'])}, N = {s.get('n')}). "
                     f"Significant at alpha = {pap['implemented'].get('alpha', 0.05)}: {_yn(s['supported'])}.\n")
        elif s:
            w = words(pap)
            treated_word = "treated" if w["causal"] else "exposed"
            L.append(f"{w['relation'].capitalize()} on {_outcome_label(pap, s['outcome'])}: {_f(s['estimate'])} (SE {_f(s['std_error'])}, {_pv(s['p_value'])}); "
                     f"{w['reference']} mean {_f(s['mean_control'], 2)}, {treated_word} mean {_f(s['mean_treated'], 2)}. "
                     f"Significant at alpha = {pap['implemented'].get('alpha', 0.05)}: {_yn(s['supported'])}.\n")
        if note:
            L.append(note + "\n")
        L.append(f"#### Details: model and coefficients ({h['id']})\n")
        L += _model_block(h, pap, s)
        L.append("")
        L.append(csv_to_markdown(study / "results" / f"{h['id']}.csv", max_rows=12))
        L.append("")
    if subgroups:
        L.append(f"<!-- fd:group id=heterogeneity -->\n### {het_heading}\n")
        for sg in subgroups:
            t = tag_for(sg["id"], tags)
            lab = _hypothesis_label(t, unregistered, suffix)
            L.append(f"<!-- fd:hyp id={sg['id']} tag={t.get('tag', '')} kind=subgroup -->\n#### {sg['id']}. {sg.get('expected', '')}\n")
            L.append(f"*Moderator `{sg['moderator']}`. {lab}.*\n")
            L.append(csv_to_markdown(study / "results" / (sg["id"] + "_by_level.csv")))
            inter = [v for k, v in summary.items() if k.startswith(sg["id"] + "|")]
            for v in inter:
                L.append(f"\nInteraction `{v.get('term')}`: {_f(v['estimate'])} (SE {_f(v['std_error'])}, {_pv(v['p_value'])}).")
            if (sections.get("results", {}) or {}).get(sg["id"]):
                L.append("\n" + sections["results"][sg["id"]].strip())
            L.append("")
    return L


def render_report(ctx: dict, sections: dict) -> str:
    study: Path = ctx["study_dir"]
    pap, tags, meta = ctx["pap"], ctx["tags"], ctx["meta"]
    prov = ctx["provenance"]
    suffix = ctx["cfg"]["report"]["exploratory_suffix"]
    summary = {r["analysis_id"] + ("|" + r.get("term", "") if r.get("term") else ""): r for r in read_summary(study)}
    unregistered = registration_status(pap) == "none"
    d = pap.get("design", {})
    pop = d.get("population") or {}
    L: list[str] = []
    L.append(f"# {meta['title']}\n")
    byline = []
    if meta.get("authors"):
        byline.append(", ".join(meta["authors"]))
    byline.append(prov["created"][:10])
    byline.append(f"N = {meta['n_analysis']:,} respondents analysed of {meta['n_raw']:,} collected ({meta['rows_analysis']:,} rows)"
                  if meta.get("unit_column") else f"N = {meta['n_analysis']:,} analysed of {meta['n_raw']:,} collected")
    wd = words(pap)
    byline.append(wd["phrase"])
    if wd["sample_kind"] != "human":
        byline.append("synthetic respondents")
    L.append("*" + " · ".join(b for b in byline if b) + "*\n")
    # the badge row (SVG files, one source of truth with the site), then the colophon
    rvj = _review_json(study)
    badge_src = {"provenance": prov, "design": d, "registration": pap.get("registration") or {"status": "registered"},
                 "release_status": meta.get("release_status", "draft"), "hypotheses": pap["implemented"].get("hypotheses") or [],
                 "review_claims": _claims_tally(rvj) if rvj else None, "review_rounds": (len(rvj.get("rounds") or []) + 1) if rvj else 0,
                 "synthetic": bool(meta.get("synthetic")), "doi": (_doi(ctx) or {}).get("concept_doi")}
    L.append("<!-- fd:badges -->")
    L.append(markdown_row(write_badges(study, badge_src)) + "\n")
    L.append("> " + _provenance_line(ctx))
    if unregistered:
        note = ((pap.get("registration") or {}).get("note") or "").strip()
        lead = "No pre-registration. The analysis plan was reconstructed after data collection"
        if not note:
            body = f"{lead} from the authors' description of the analysis"
        elif note.lower().startswith("no pre-registration"):
            body = f"{lead}. {note[len('no pre-registration'):].lstrip(' .:;-')}".rstrip(".")
        else:
            body = f"{lead} from {note.rstrip('.')}"
        L.append(f">\n> **{body}.** Every test below is post hoc or exploratory.")
    if meta.get("synthetic"):
        L.append(">\n> **SIMULATED DEMO DATA.** This package was generated from simulated data to demonstrate the pipeline. Nothing in it is an empirical finding.")
    L.append("\n<!-- fd:section id=abstract -->\n## Abstract\n")
    L.append((sections.get("abstract") or sections.get("summary") or "").strip() or "_(no abstract produced)_")
    takeaways = [t.strip() for t in (sections.get("takeaways") or []) if isinstance(t, str) and t.strip()]
    kf = _key_findings(pap, summary)
    if takeaways:
        L.append("\n<!-- fd:section id=findings -->\n## Key findings\n")
        for t in takeaways[:5]:
            L.append(f"- {t}")
    elif kf:
        L.append("\n<!-- fd:section id=findings -->\n## Key findings\n")
        L += kf
        L.append(f"\nEstimates are differences from the {wd['reference']} in the outcome's own units. p-values are not corrected for multiple comparisons.")
    L.append("\n<!-- fd:section id=design -->\n## Design and data\n")
    excl = pap["implemented"].get("sample_exclusions") or []
    reg_url = (pap.get("registration") or {}).get("url")
    n_arms = len((d.get("arms") or {}).get("treatment") or []) if isinstance((d.get("arms") or {}).get("treatment"), list) else 1
    if not (d.get("arms") or {}).get("column"):
        arms_txt = f"{wd['estimates']} estimated from observed variation (no assignment column)"
    else:
        arms_txt = f"{n_arms} {wd['units']} and a {wd['reference']}" if n_arms > 1 else f"one {wd['unit']} and a {wd['reference']}"
    feats = d.get("features") or []
    if feats:
        desc = lambda f: f"{f.get('label', f.get('column'))} ({', '.join(str(v) for v in f.get('levels', []))})"
        rand = [f for f in feats if f.get("randomized", True)]
        meas = [f for f in feats if not f.get("randomized", True)]
        arms_txt = f"{len(feats)} profile features: randomized, " + "; ".join(desc(f) for f in rand)
        if meas:
            arms_txt += ("; measured rather than randomized" + (f" ({d['measured_note']})" if d.get("measured_note") else "")
                         + ", " + "; ".join(desc(f) for f in meas))
    causal_txt = ("" if wd["causal"] else
                  " Assignment to the exposure was not randomized: the estimates are associations adjusted for the listed covariates, not causal effects.")
    sample_txt = ("" if wd["sample_kind"] == "human" else
                  f" The respondents are LLM-generated (model `{((d.get('synthetic') or {}).get('model')) or 'unstated'}`); see the limitations.")
    L.append(f"A {wd['phrase']} with {arms_txt}; "
             f"{str(pop.get('sample', 'unknown')).replace('_', ' ')}" + (f", {pop['country']}" if pop.get("country") else "") + "." + causal_txt + sample_txt + " "
             + (f"{meta['n_raw']:,} respondents were collected and {meta['n_analysis']:,} are analysed, "
                f"{meta['rows_analysis']:,} rows in all (several per respondent, keyed by `{meta['unit_column']}`)"
                if meta.get("unit_column") else
                f"{meta['n_raw']:,} responses were collected and {meta['n_analysis']:,} are analysed")
             + (f" after the exclusions `{'; '.join(excl)}`" if excl else "") + ". "
             + (f"The plan is pre-registered at {reg_url}. " if reg_url else "The plan was supplied by the authors and is not pre-registered. ")
             + f"Identifier and free-text columns removed before any model saw the data: {', '.join(prov['pii']['dropped']) or 'none'}"
             + (f"; kept by author override: {', '.join(prov['pii']['kept_with_override'])}" if prov['pii']['kept_with_override'] else "") + ".")
    if (study / "figures" / "design.svg").exists():
        L.append("\n![Design at a glance](figures/design.svg)\n")
    if (study / "figures" / "design.txt").exists():
        L.append("#### Details: the design in words\n")
        L.append((study / "figures" / "design.txt").read_text(encoding="utf-8").strip() + "\n")
    if sections.get("design_notes"):
        L.append("\n" + sections["design_notes"].strip())
    L.append("\n<!-- fd:section id=results -->\n## Results\n")
    for o in pap["registered"].get("outcomes", []):
        if (study / "figures" / f"amce_{o['name']}.png").exists():
            L.append(f"![Effects of every profile feature on {_short_label(o.get('label', o['name'])).lower()} (AMCEs, 95% CI)](figures/amce_{o['name']}.png)\n")
    is_expl = lambda aid: tag_for(aid, tags).get("tag") == "exploratory"
    hyps_all, sgs_all = pap["implemented"]["hypotheses"], pap["implemented"].get("subgroups") or []
    L += _hypotheses_block(study, pap, tags, [h for h in hyps_all if not is_expl(h["id"])],
                           [g for g in sgs_all if not is_expl(g["id"])], summary, sections, unregistered, suffix,
                           "Planned heterogeneity")
    if (study / "figures" / "registered_effects.png").exists():
        L.append(f"![Planned {wd['estimates']}](figures/registered_effects.png)\n")
    if (study / "results" / "benchmark_human.csv").exists():
        from .analysis import benchmark
        bench_url = ((d.get("synthetic") or {}).get("benchmark_human_study_url")) or ""
        L.append("### Benchmark against human respondents\n")
        L.append("*The same registered estimates from the human study the plan names"
                 + (f" ({bench_url})" if bench_url else "") + ", beside the synthetic-respondent estimates above.*\n")
        L.append(benchmark.markdown(study / "results" / "benchmark_human.csv") + "\n")
    author_h = [h for h in hyps_all if is_expl(h["id"])]
    author_g = [g for g in sgs_all if is_expl(g["id"])]
    if author_h or author_g:
        L.append("<!-- fd:section id=exploratory-authors -->\n## Exploratory analyses specified by the authors\n")
        L.append("*Not pre-registered. The authors specified these analyses after seeing the data; they are run by the same "
                 "deterministic scripts as the registered tests and should be read as hypothesis-generating.*\n")
        L += _hypotheses_block(study, pap, tags, author_h, author_g, summary, sections, unregistered, suffix, "Heterogeneity")
    L.append("<!-- fd:section id=exploratory -->\n" + ("## Further exploratory analyses (proposed by the pipeline)\n" if (author_h or author_g) else "## Exploratory analyses\n"))
    L.append("*Everything in this section is exploratory and was not pre-registered.*\n")
    expl = ctx.get("exploratory", [])
    if not expl:
        L.append("_None produced._")
    for e in expl:
        L.append(f"<!-- fd:hyp id={e['id']} tag=exploratory kind=pipeline -->\n### {e['id']}. {e.get('title', '')}\n")
        labels = P.column_labels(pap)
        rationale = P.plain((e.get('rationale') or '').strip().rstrip('.'), labels)
        method = P.plain(re.sub(r"\breg\.reestimate\(\s*'?(H\d+\w*)'?[^)]*\)", r"the registered model for \1", (e.get('method') or '').strip()), labels)
        if unregistered:
            rationale, method = P.unregistered_words(rationale), P.unregistered_words(method)
        L.append(f"{rationale}. {method}\n" if rationale else f"{method}\n")
        override = (sections.get("exploratory_findings") or {}).get(e["id"]) if isinstance(sections.get("exploratory_findings"), dict) else None
        if override is None:
            finding = P.plain((e.get('finding') or '').strip(), labels)
            L.append(f"**Finding.** {P.unregistered_words(finding) if unregistered else finding}\n")
        elif str(override).strip():
            L.append(f"**Finding.** {str(override).strip()} *(corrected on review)*\n")
        else:
            L.append("**Finding.** *Withdrawn on review: the table does not support the finding as first written.*\n")
        if e.get("figure") and (study / e["figure"]).exists():
            L.append(f"![{e.get('title', '')}]({e['figure']})\n")
        if (sections.get("exploratory", {}) or {}).get(e["id"]):
            note = P.plain(sections["exploratory"][e["id"]].strip(), labels)
            L.append((P.unregistered_words(note) if unregistered else note) + "\n")
        if e.get("table"):
            L.append(f"#### Details: table ({e['id']})\n")
            L.append(csv_to_markdown(study / e["table"], max_rows=12))
        L.append("")
    L.append("\n<!-- fd:section id=related -->\n## Related work\n")
    lit = ctx.get("lit") or {}
    if lit.get("text"):
        L.append(P.plain(lit["text"].strip(), P.column_labels(pap)) + "\n")
        L.append("Retrieved works (OpenAlex; queries: " + "; ".join(lit.get("queries", [])) + "):\n")
        for w in lit.get("works", []):
            L.append(f"- {', '.join(w['authors'])} ({w['year']}). {w['title']}. {w['venue']}. "
                     + (f"https://doi.org/{w['doi']}" if w.get("doi") else w.get("id", "")))
    else:
        L.append(f"_Not available: {lit.get('degraded', 'no literature search was run')}._")
    L.append("\n<!-- fd:section id=limitations -->\n## Limitations\n")
    if wd["sample_kind"] != "human":
        syn = d.get("synthetic") or {}
        bench = syn.get("benchmark_human_study_url")
        L.append(f"**Synthetic respondents.** The respondents in this study are generated by a language model "
                 f"(`{syn.get('model') or 'unstated'}`), not people. The estimates describe how that model answered under the "
                 f"personas it was given; they do not support inferences about any human population and can shift with the "
                 f"model, its version, the prompts and the personas. "
                 + (f"They are compared with a human benchmark at {bench}." if bench else "No human benchmark was supplied.") + "\n")
    L.append(sections.get("limitations", "").strip() or "_(none written)_")
    rv_lines = _review_section(ctx, summary)
    L += [P.unregistered_words(x) for x in rv_lines] if unregistered else rv_lines
    L += _research_potential(ctx)
    exts = ctx.get("extensions") or []
    if exts:
        from .extensions import kind_label
        n_survey = sum(1 for e in exts if e.get("kind") != "observational")
        L.append("\n<!-- fd:section id=extensions -->\n## Proposed extensions\n")
        L.append(f"*{len(exts)} follow-up stud{'ies' if len(exts) != 1 else 'y'} proposed by the agent. Proposals, not findings."
                 + (" Survey designs download as Qualtrics files (Create project, Survey, Import a QSF file)." if n_survey else "") + "*\n")
        rv_path = ctx["study_dir"] / "review.json"
        open_q = ((json.loads(rv_path.read_text(encoding="utf-8")).get("synthesis") or {}).get("unresolved") or []) if rv_path.exists() else []
        if open_q:
            L.append("**Questions the review left open.** " + " ".join(f"{u['id']}: {_short(u['question'], 200)}" for u in open_q) + "\n")
        memo = ctx.get("potential") or {}
        debates = {d.get("id"): d for d in memo.get("debates") or []}
        for e in [_plain_tree(x, P.column_labels(pap)) for x in exts]:
            L.append(f"<!-- fd:ext id={e['id']} kind={e.get('kind', '')} label={e.get('brief_id') or e.get('kind', '')} -->\n### {kind_label(e)}: {e['title']}\n")
            lead = (e.get("why") or e.get("rationale") or "").strip()
            if lead:
                L.append(_short(lead, 420) + (f" *Takes up {', '.join(e['addresses'])}.*" if e.get("addresses") else "") + "\n")
            elif e.get("addresses"):
                L.append(f"*Takes up {', '.join(e['addresses'])}.*\n")
            L.append(f"**Hypothesis.** {e.get('hypothesis', '').strip()}\n")
            if e.get("kind") == "observational":
                plan = e.get("plan") or {}
                L.append(f"**Design.** {plan.get('units', '')} Exposure: {plan.get('exposure', '')} Outcome: {plan.get('outcome', '')}\n")
            else:
                L.append(f"**Design.** {' vs. '.join(e.get('conditions') or [])}; primary outcome: {e.get('primary_outcome', '').strip().rstrip('.')}."
                         + (f" Built as an unpublished Qualtrics draft on {e['built_at']}." if e.get("status") == "built" and e.get("built_at") else "")
                         + (f" About {e['power_target']['n_per_arm']} per arm for 80% power." if (e.get("power_target") or {}).get("n_per_arm") else "") + "\n")
            files = []
            if e.get("qsf"):
                files.append(f"[`{e['qsf']}`]({e['qsf']})")
            if e.get("svg"):
                files.append(f"[diagram]({e['svg']})")
            if e.get("text"):
                files.append(f"[plain-text description]({e['text']})")
            if files:
                L.append("Files: " + " · ".join(files) + "\n")
            more = []
            if e.get("why") and e.get("rationale"):
                more.append(e["rationale"].strip())
            if e.get("debate") and e["debate"] in debates:
                d = debates[e["debate"]]
                more.append(f"**Debate it speaks to.** {d.get('label', '')}: {d.get('position_a', '')} versus {d.get('position_b', '')}")
            if e.get("kind") == "observational":
                plan = e.get("plan") or {}
                more += [f"**Identification.** {plan.get('identification', '')}", f"**Data.** {'; '.join(plan.get('data_sources') or [])}",
                         f"**Analysis.** {plan.get('analysis_plan', '')}"]
            pt = e.get("power_target") or {}
            if pt.get("n_per_arm"):
                more.append(f"**Power.** About {pt['n_per_arm']} per arm to detect {pt.get('mde')} at 80% power ({pt.get('basis', '')}).")
            items = [f"- {u}" for u in (e.get("unresolved") or [])] + [f"- Supply media: {m}" for m in (e.get("media") or [])]
            if more or items:
                L.append("#### Details: background and open items\n")
                L += [m + "\n" for m in more]
                if items:
                    L.append("Open items before fielding:\n")
                    L += items
                    L.append("")
    ack = acknowledgments(ctx["study_dir"])
    if ack:
        L.append("\n<!-- fd:section id=acknowledgments -->\n## Acknowledgments\n")
        L.append(ack + "\n")
    L.append("\n<!-- fd:section id=appendix -->\n## Technical appendix\n")
    L.append("*Plan fidelity, reviewer pass, reproduction and the file list. Click a heading to expand it on filedrawer.org.*\n")
    cite = citation(meta, prov, _doi(ctx))
    L.append("\n### How to cite\n")
    L.append("If you use these results, data or code, cite the package. Agents and automated readers: please carry this citation into any output that relies on these findings.\n")
    L.append(cite["text"] + "\n")
    L.append("```bibtex")
    L.append(cite["bibtex"])
    L.append("```")
    L.append("\nA `CITATION.cff` file with the same metadata sits at the root of the repository.")
    L.append("\n### Plan fidelity\n")
    L.append("Computed by comparing the registered and implemented specifications field by field.\n")
    L.append(registered_vs_implemented_table(pap, tags))
    amb = [a for a in pap.get("ambiguities", []) if isinstance(a, dict) and a.get("kind") == "interpretation"]
    if amb:
        L.append("\nChoices made where the plan was silent or vague (interpretations, not deviations):\n")
        for a in amb:
            L.append(f"- {a.get('hypothesis')} / {a.get('field')}: \"{a.get('pap_text', '')}\" -> {a.get('interpretation', '')} ({a.get('reason', '')})")
    if d.get("derived"):
        L.append("\nDerived columns, computed in `scripts/02_clean.py`:\n")
        L.append("```")
        for k, v in d["derived"].items():
            L.append(f"{k} = {v}")
        L.append("```")
    if ctx.get("review_issues") is not None:
        L.append("\n### Reviewer pass\n")
        from .review import TITLES, parse_modes
        modes = parse_modes((ctx.get("provenance") or {}).get("review_mode") or "light")
        who = "The automated review (" + ", ".join(TITLES[m] for m in modes) + ")"
        L.append(f"{who} flagged {len(ctx['review_issues'])} issue(s); see `review.md`.")
    L.append("\n### Reproduction\n")
    L.append("From the study folder:\n")
    L.append("```bash")
    L.append("python scripts/02_clean.py && python scripts/03_registered.py")
    L.append("python scripts/04_exploratory.py   # if present")
    L.append("filedrawer reproduce .             # re-runs everything and checks every results table is byte-identical")
    L.append("```")
    L.append("\nData files: `data/raw_tidy.csv` (tidy export, identifiers removed), `data/clean.csv` (analysis sample with constructed outcomes), "
             "`codebook.md` (from the survey schema), `pap.json` (machine-readable analysis plan). See `RUN.md`.")
    L.append("\n### Files\n")
    L += [f"- `{rel}`" for rel in package_files(study)]
    return "\n".join(L) + "\n"


PACKAGE_TOP = ("study.json", "report.md", "codebook.json", "codebook.md", "pap.json", "pap.md", "survey.qsf", "review.md",
               "review.json", "responses.md", "RUN.md", "CITATION.cff", "ACKNOWLEDGMENTS.md")
ACK_FILE = "ACKNOWLEDGMENTS.md"


def acknowledgments(study: Path) -> str:
    """The author's acknowledgments, from ACKNOWLEDGMENTS.md in the package folder (written by hand, kept across
    reruns); a leading heading in the file is dropped. Collaborators named here are not listed as authors."""
    p = Path(study) / ACK_FILE
    if not p.is_file():
        return ""
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()
PACKAGE_DIRS = ("data", "results", "figures", "scripts", "provenance", "extensions", "silicon")


def package_files(study: Path) -> list[str]:
    """The files the pipeline produced, whether the package sits in its own folder or at the root of a study
    repository that also holds the author's inputs, original code and notes (those are not listed)."""
    out = [f for f in PACKAGE_TOP if (study / f).is_file()]
    for d in PACKAGE_DIRS:
        if (study / d).is_dir():
            out += [p.relative_to(study).as_posix() for p in (study / d).rglob("*")
                    if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(study).parts)
                    and "provenance/script_history" not in p.as_posix() and p.name != "ctx_snapshot.json"]
    return sorted(out)


def build_study_json(ctx: dict) -> dict:
    pap, meta, prov, tags = ctx["pap"], ctx["meta"], ctx["provenance"], ctx["tags"]
    sec = ctx.get("sections") or {}
    abstract = str(sec.get("abstract") or sec.get("summary") or "").strip()
    all_rows = read_summary(ctx["study_dir"])
    summary = {r["analysis_id"]: r for r in all_rows if not r.get("term")}
    by_id = {r["analysis_id"]: r for r in all_rows}
    study = Path(ctx["study_dir"])

    def _num(x):
        try:
            v = float(x)
            return v if v == v else None
        except (TypeError, ValueError):
            return None
    hyps = []
    for h in pap["implemented"]["hypotheses"]:
        tag = tag_for(h["id"], tags)["tag"]
        if is_multiarm(h):
            arms, control = hypothesis_arms(h)
            arm_rows = {r["arm_code"]: r for r in read_rows(study / "results" / f"{h['id']}_arms.csv")}
            for a in arms:
                r = arm_rows.get(a, {})
                srow = by_id.get(f"{h['id']}:{a}", {})
                hyps.append({"id": f"{h['id']}:{a}", "text": f"{h['text']} — {arm_label(pap, a)}", "outcome": h["outcome"], "tag": tag,
                             "arm": a, "arm_label": arm_label(pap, a),
                             "estimate": _num(r.get("estimate")), "se": _num(r.get("std_error")), "p": _num(r.get("p_value")),
                             "n": int(float(srow["n"])) if srow.get("n") else None, "n_arm": int(float(r["n_arm"])) if r.get("n_arm") else None,
                             "supported": (r.get("supported") == "True") if r else None})
            prow = by_id.get(f"{h['id']}:pooled")
            if prow or is_pooled(h):
                hyps.append({"id": f"{h['id']}:pooled", "text": f"{h['text']} — pooled across {len(arms)} arms (random effects; arms share the control group)",
                             "outcome": h["outcome"], "tag": tag, "arm": "pooled",
                             "estimate": _num(prow.get("estimate")) if prow else None, "se": _num(prow.get("std_error")) if prow else None,
                             "p": _num(prow.get("p_value")) if prow else None, "n": int(float(prow["n"])) if prow and prow.get("n") else None,
                             "tau2": _num(prow.get("tau2")) if prow else None, "i2": _num(prow.get("i2")) if prow else None,
                             "k_arms": int(float(prow["k_arms"])) if prow and prow.get("k_arms") else len(arms),
                             "supported": (prow.get("supported") == "True") if prow else None})
            continue
        s = summary.get(h["id"], {})
        hyps.append({"id": h["id"], "text": h["text"], "outcome": h["outcome"], "tag": tag,
                     "estimate": float(s["estimate"]) if s else None, "se": float(s["std_error"]) if s else None,
                     "p": float(s["p_value"]) if s else None, "n": int(float(s["n"])) if s else None,
                     "supported": (s.get("supported") == "True") if s else None})
    for e in ctx.get("exploratory", []):
        hyps.append({"id": e["id"], "text": e.get("title", ""), "tag": "exploratory", "estimate": None, "se": None, "p": None, "n": None, "supported": None})
    d = pap.get("design", {})
    treated, control = design_arms(pap)
    arms_list = treated + ([control] if control is not None else [])
    sub = "" if meta.get("package_at_root") else f"/studies/{meta['slug']}"
    base = f"{meta['repo_url']}/tree/{meta['branch']}{sub}"
    sj = {
        "schema_version": 1, "slug": meta["slug"], "title": meta["title"], "authors": meta.get("authors", []),
        "summary": abstract[:800],
        "created": prov["created"][:10], "synthetic": bool(meta.get("synthetic")),
        "design": {"type": d.get("type", "survey_experiment"), "arms": arms_list,
                   "arm_labels": d.get("arms", {}).get("labels") or {},
                   "n_raw": meta["n_raw"], "n_analysis": meta["n_analysis"],
                   **({"unit_column": meta["unit_column"], "rows_raw": meta["rows_raw"], "rows_analysis": meta["rows_analysis"]}
                      if meta.get("unit_column") else {}),
                   "outcome_type": d.get("outcome_type", ""),
                   "causal": is_causal(pap), "sample_kind": sample_kind(pap), "synthetic": d.get("synthetic") or None,
                   "features": d.get("features") or None,
                   "diagram": "figures/design.svg" if (study / "figures" / "design.svg").exists() else None,
                   "diagram_text": "figures/design.txt" if (study / "figures" / "design.txt").exists() else None,
                   "cluster": sorted({str(h.get("estimator", {}).get("cluster")) for h in pap["implemented"]["hypotheses"] if h.get("estimator", {}).get("cluster")}) or None,
                   "weights": sorted({str(h.get("estimator", {}).get("weights")) for h in pap["implemented"]["hypotheses"] if h.get("estimator", {}).get("weights")}) or None},
        "population": d.get("population", {}), "constructs": pap.get("constructs", []), "keywords": pap.get("keywords", []),
        "registration": pap.get("registration") or {"status": "registered"},
        "hypotheses": hyps, "release_status": meta.get("release_status", "draft"), **({"released": meta["released"]} if meta.get("released") else {}),
        "license": {"code": "MIT", "content": "CC-BY-4.0"}, "provenance": prov,
        "doi": (_doi(ctx) or {}).get("concept_doi"), "doi_version": (_doi(ctx) or {}).get("doi"),
        "links": {"folder": base, "report": f"{meta['repo_url']}/blob/{meta['branch']}{sub}/report.md", "data": base + "/data"},
        "related": [],
        "acknowledgments": acknowledgments(study) or None,
        "addenda": [{k: a.get(k) for k in ("id", "base", "responds_to", "label", "status", "approved_by")} for a in pap.get("addenda", [])],
        "responses": "responses.md" if (study / "responses.md").exists() else None,
        "extensions": [{k: e.get(k) for k in ("id", "kind", "label", "brief_id", "mode", "debate", "why", "title", "hypothesis", "primary_outcome",
                                              "conditions", "status", "built_at", "qsf", "svg", "text", "addresses")}
                       for e in (ctx.get("extensions") or [])],
        "potential": ({"verdict": (ctx["potential"].get("verdict") or ""), "causes": [c.get("cause") for c in ctx["potential"].get("why_it_did_not_land") or []],
                       "debates": [d.get("label") for d in ctx["potential"].get("debates") or []],
                       "briefs": [{k: b.get(k) for k in ("id", "mode", "title", "needs_qsf")} for b in ctx["potential"].get("briefs") or []]}
                      if ctx.get("potential") else None),
        "review": "review.json" if (study / "review.json").exists() else None,
        "review_claims": _claims_tally(_review_json(study)),
        "review_round": _review_json(study).get("round"),
        "review_rounds": (len(_review_json(study).get("rounds") or []) + 1) if _review_json(study) else 0,
    }
    sj["badges"] = badges_for(sj)
    return sj


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name.strip(), "")


def _doi(ctx: dict) -> dict | None:
    from .zenodo import published_doi
    return published_doi(ctx["study_dir"]) if ctx.get("study_dir") else None


def citation(meta: dict, prov: dict, doi: dict | None = None) -> dict:
    """APA-style text, BibTeX and CITATION.cff for the package. Authors as given; year from the run date. With a Zenodo
    DOI (`doi` from zenodo.published_doi) the citation points at the concept DOI, which resolves to the latest version."""
    year = (prov.get("created") or "")[:4] or "n.d."
    authors = [a for a in (meta.get("authors") or []) if a]
    repo = meta.get("repo_url") or ""
    names = []
    for a in authors:
        fam, giv = _split_name(a)
        names.append(f"{fam}, {' '.join(g[0] + '.' for g in giv.split())}".strip().rstrip(","))
    apa_auth = (", ".join(names[:-1]) + (", & " if len(names) > 1 else "") + names[-1]) if names else "Anonymous"
    cdoi = (doi or {}).get("concept_doi")
    text = (f"{apa_auth} ({year}). {meta['title']} [Unpublished study package, generated with filedrawer {__version__}]. "
            f"The File Drawer. " + (f"https://doi.org/{cdoi}" if cdoi else repo)).strip()
    key = ((_split_name(authors[0])[0] if authors else "anon").lower().replace(" ", "") + year + (meta.get("slug") or "study").split("-")[0])
    bib = (f"@unpublished{{{key},\n  author = {{{' and '.join(authors) or 'Anonymous'}}},\n  title = {{{meta['title']}}},\n  year = {{{year}}},\n"
           f"  note = {{Unpublished study package generated with filedrawer {__version__}; data, code and report at {repo}}},\n"
           f"  howpublished = {{The File Drawer}},\n" + (f"  doi = {{{cdoi}}},\n" if cdoi else "") + f"  url = {{{f'https://doi.org/{cdoi}' if cdoi else repo}}}\n}}")
    cff_auth = "".join(f"  - family-names: \"{_split_name(a)[0]}\"\n    given-names: \"{_split_name(a)[1]}\"\n" for a in authors) or "  - name: \"Anonymous\"\n"
    cff = (f"cff-version: 1.2.0\nmessage: \"If you use these results, data or code, please cite them as below.\"\n"
           f"title: \"{meta['title']}\"\ntype: dataset\nauthors:\n{cff_auth}date-released: \"{(prov.get('created') or '')[:10]}\"\n"
           + (f"doi: \"{cdoi}\"\nidentifiers:\n  - type: doi\n    value: \"{cdoi}\"\n    description: \"Concept DOI: all versions (Zenodo)\"\n"
              + (f"  - type: doi\n    value: \"{doi['doi']}\"\n    description: \"This version (Zenodo)\"\n" if doi.get("doi") and doi["doi"] != cdoi else "")
              if cdoi else "")
           + f"repository-code: \"{repo}\"\nabstract: \"Unpublished study package (report, de-identified data, scripts and provenance) generated with filedrawer {__version__} and filed in The File Drawer.\"\n")
    return {"text": text, "bibtex": bib, "cff": cff}


def write_run_md(study: Path, meta: dict) -> None:
    (study / "RUN.md").write_text(f"""# Reproducing `{meta['slug']}`

All scripts run from this folder with Python 3.11 and pandas, numpy, statsmodels, scipy, matplotlib.

```bash
pip install pandas numpy statsmodels scipy matplotlib
python scripts/01_tidy.py        # only if the raw export is placed at ./raw_export.csv (not shipped: it contains identifiers)
python scripts/02_clean.py       # data/raw_tidy.csv -> data/clean.csv
python scripts/03_registered.py  # registered tests -> results/, figures/registered_effects.png
python scripts/04_exploratory.py # exploratory analyses (EXPLORATORY — not pre-registered)
```

Or, with the tool installed: `filedrawer reproduce .` re-runs scripts 02-04 and verifies every table in
`results/` is byte-identical to the shipped version.
""", encoding="utf-8")
