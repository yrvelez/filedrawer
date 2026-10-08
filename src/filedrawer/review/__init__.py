"""Review stage. Four reviewers, any combination, merged into one review.json:

  light     the single-pass reviewer (issues R1, R2, ...)
  advanced  methodology + statistics reviewers, one call each (A1, ...)
  coarse    the open-source coarse reviewer, run locally through its CLI (C1, ...)
  refine    refine.ink; no API, so the review is uploaded by hand: `filedrawer review-import <study> refine <file>` (RF1, ...)
  openreview  any referee report, e.g. one posted on OpenReview, imported the same way (OR1, ...)

The default is the Light Pass: the light reviewer plus the checking agent, which tests each claim against the tables,
triages every issue, and hands the writing agent a correction list; serious analytical issues get agent-run robustness
checks. No person is involved unless someone later runs `filedrawer address --by NAME`.

Each issue carries "source". review.json records the modes so `filedrawer address` re-reviews the same way
(light and advanced are re-run; coarse and refine issues are carried over, since they cost minutes and money
or need a manual upload).
"""
from __future__ import annotations

import json
from pathlib import Path

MODES = ("light", "advanced", "coarse", "refine", "openreview")
ALIASES = {"native": ["light"], "both": ["light", "advanced"], "all": list(MODES)}
PREFIX = {"advanced": "A", "coarse": "C", "refine": "RF", "openreview": "OR"}
TITLES = {"light": "Light Pass", "advanced": "Advanced Pass (methodology + statistics)", "coarse": "Coarse", "refine": "Refine",
          "openreview": "OpenReview (imported referee report)"}


def parse_modes(value) -> list[str]:
    """'light,advanced' | ['coarse'] | True | 'both' -> ordered list of modes; raises ValueError on unknown ones."""
    if value is True or value is None or value == "":
        return ["light"]
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for p in (str(x).strip().lower() for x in parts):
        for m in ALIASES.get(p, [p]):
            if m not in MODES:
                raise ValueError(f"unknown review mode {m!r}; choose from {', '.join(MODES)} (comma-separated)")
            if m not in out:
                out.append(m)
    return sorted(out, key=MODES.index)


def _number(issues: list[dict], mode: str) -> list[dict]:
    for n, i in enumerate(issues, 1):
        i["id"] = f"{PREFIX[mode]}{n}"
    return issues


DECISION_LABEL = {"accept": "Accepted as revised", "minor_revision": "Minor revision", "major_revision": "Major revision"}


def claims_tally(rv: dict) -> dict:
    """What the record can honestly say about the review: how many of the claims checked on the latest text were
    supported, and how many analytical issues are still open. There is no editor and therefore no accept/revise
    disposition on the page; `decision` stays internal (it only decides whether a revision and a sign-off run)."""
    so = rv.get("signoff") or {}
    claims = so.get("claims") or (rv.get("synthesis") or {}).get("claims") or rv.get("claims") or []   # archived rounds keep `claims`
    supported = sum(1 for c in claims if c.get("verdict") == "supported")
    open_issues = sum(1 for i in rv.get("issues") or []
                      if i.get("kind") == "analytical" and i.get("disposition", "address") in ("address", None) and not i.get("answered_by"))
    return {"supported": supported, "total": len(claims), "open_analytical": open_issues, "on_revised_text": bool(so)}


def tally_sentence(t: dict) -> str:
    if not t.get("total"):
        return "no claim checks recorded"
    s = f"{t['supported']} of {t['total']} claim{'s' if t['total'] != 1 else ''} supported by the results"
    s += " after the agent's corrections" if t.get("on_revised_text") else " before the agent's corrections"
    if t.get("open_analytical"):
        s += f"; {t['open_analytical']} analytical issue{'s' if t['open_analytical'] != 1 else ''} without a robustness check"
    return s


def decision_for(rv: dict, claims: list[dict] | None = None) -> str:
    """Internal: whether the text needs a revision pass, deterministic from the record (any unsupported claim or an open
    high-severity analytical issue -> major_revision; an overstated claim alone -> minor_revision; otherwise accept).
    Never shown as an editorial disposition: there is no editor."""
    claims = claims if claims is not None else ((rv.get("synthesis") or {}).get("claims") or [])
    verdicts = {c.get("verdict") for c in claims}
    open_high = any(i.get("kind") == "analytical" and i.get("severity") == "high" and i.get("disposition", "address") in ("address", None)
                    and not i.get("answered_by") for i in rv.get("issues") or [])
    if "unsupported" in verdicts or open_high:
        return "major_revision"
    if "overstated" in verdicts:
        return "minor_revision"
    return "accept"


def revision_brief(rv: dict) -> dict | None:
    """What the writing agent must act on: the checking agent's corrections, the claims judged overstated or
    unsupported (K issues), the presentational issues marked editorial, and the declined items it must
    leave alone. None when there is nothing to revise."""
    syn = rv.get("synthesis") or {}
    issues = rv.get("issues") or []
    pick = lambda i: {"id": i.get("id"), "location": i.get("location", ""), "issue": i.get("issue", ""), "fix": i.get("fix", "")}
    claims = [pick(i) for i in issues if i.get("source") == "claims"]
    editorial = [str(e) for e in (syn.get("editorial") or [])]
    presentational = [pick(i) for i in issues if i.get("source") != "claims" and i.get("disposition") == "editorial"]
    declined = [{"id": i.get("id"), "reason": i.get("disposition_reason", "")} for i in issues if i.get("disposition") == "declined"]
    if not (claims or editorial or presentational):
        return None
    return {"round": rv.get("round", 1), "editorial": editorial, "claims": claims, "presentational": presentational, "declined": declined}


def followup_brief(rv: dict) -> dict | None:
    """What is still wrong after a correction pass: claims the re-check still judges overstated or unsupported, and
    serious (high/medium) text issues the writing agent left without a note. None when nothing is left."""
    so = rv.get("signoff") or {}
    bad = [c for c in so.get("claims") or [] if c.get("verdict") in ("overstated", "unsupported")]
    noted = {str(n).partition(":")[0].strip().upper() for n in rv.get("revision_notes") or []}
    pres = [{"id": i.get("id"), "location": i.get("location", ""), "issue": i.get("issue", ""), "fix": i.get("fix", "")}
            for i in rv.get("issues") or [] if i.get("source") != "claims" and i.get("disposition") == "editorial"
            and i.get("severity") in ("high", "medium") and str(i.get("id", "")).upper() not in noted]
    if not bad and not pres:
        return None
    claims = [{"id": f"F{n}", "location": c.get("location", ""), "issue": f"Still {c.get('verdict')}: \"{c.get('claim', '')}\". {c.get('evidence', '')}",
               "fix": c.get("fix") or "Reword to match the table, or delete the sentence."} for n, c in enumerate(bad, 1)]
    declined = [{"id": i.get("id"), "reason": i.get("disposition_reason", "")} for i in rv.get("issues") or [] if i.get("disposition") == "declined"]
    return {"round": rv.get("round", 1), "pass": 2, "editorial": [], "claims": claims, "presentational": pres, "declined": declined}


def apply_corrections(ctx: dict, rv: dict, sections: dict, brief: dict, render, passes: int = 2, log=print) -> dict:
    """The writing agent applies the correction list; the checking agent re-checks the claims on the corrected text;
    whatever is still flagged goes back for another pass (at most `passes` in all). `render(sections)` re-writes the
    report between passes. Returns the final sections; rv gains revision_notes, signoff and correction_passes."""
    from ..agents import writer
    review_cfg = (ctx.get("cfg") or {}).get("review") or {}
    notes: list[str] = []
    done = 0
    while brief and done < max(1, passes):
        revised = writer.run(ctx, revision={**brief, "previous_sections": sections})
        if not revised:
            ctx.setdefault("degraded", []).append(f"correction pass {done + 1}: the writing agent returned no usable revision")
            break
        done += 1
        new = list(revised.get("revision_notes") or [])
        notes += new
        sections = {**revised, "revision_notes": notes}
        rv["revision_notes"] = notes
        render(sections)
        log(f"correction pass {done}: {len(new)} note(s) from the writing agent")
        if not review_cfg.get("signoff", True):
            break
        so = signoff(ctx, rv)
        if not so:
            break
        ok = sum(1 for c in so["claims"] if c.get("verdict") == "supported")
        log(f"claim re-check: {ok}/{len(so['claims'])} claims supported on the corrected text")
        brief = followup_brief(rv)
    rv["correction_passes"] = done
    write_review(ctx["study_dir"], rv)
    return sections


def signoff(ctx: dict, rv: dict) -> dict | None:
    """The checking agent re-judges the previous round's claims against the corrected report and records the outcome."""
    from . import orchestrate
    prev = (rv.get("synthesis") or {}).get("claims") or []
    try:
        so = orchestrate.run(ctx, rv, claims_only=True, previous_claims=prev)
    except Exception as e:                      # the sign-off is an add-on; never lose the review
        ctx.setdefault("degraded", []).append(f"review sign-off failed: {e}")
        return None
    if not so or not so.get("claims"):
        return None
    so["decision"] = decision_for(rv, so["claims"])
    rv["signoff"] = so
    rv["decision"] = so["decision"]
    write_review(ctx["study_dir"], rv)
    return so


def history_append(rv: dict) -> None:
    """Archive the current round inside review.json["rounds"] before a new round overwrites it."""
    syn = rv.get("synthesis") or {}
    rounds = rv.setdefault("rounds", [])
    rounds.append({"round": rv.get("round", len(rounds) + 1), "overall": rv.get("overall", ""), "decision": rv.get("decision"),
                   "issues": [{k: i.get(k) for k in ("id", "severity", "kind", "source", "disposition", "location", "issue")}
                              for i in rv.get("issues") or []],
                   "claims": syn.get("claims") or [], "editorial": syn.get("editorial") or [], "unresolved": syn.get("unresolved") or [],
                   "revision_notes": rv.get("revision_notes") or [], "signoff": rv.get("signoff")})


def run_review(ctx: dict, modes, prompts_dir: str | None = None, previous: dict | None = None, round_no: int = 1) -> dict:
    """Run the requested reviewers, write review.md + review.json, return the review dict.

    With `previous` (the review being answered by `address`), coarse and refine are not re-run: their issues are
    carried over from `previous`, and earlier rounds archived in previous["rounds"] are kept.
    """
    modes = parse_modes(modes)
    from ..agents import reviewer
    from . import advanced, external
    issues, models, notes, pending, overall = [], {}, [], [], ""
    carried = [i for i in (previous or {}).get("issues", []) if i.get("source") in ("coarse", "refine", "openreview")]
    if "light" in modes:
        rv = reviewer.run(ctx, write=False)
        for i in rv["issues"]:
            i["source"] = "light"
        issues += rv["issues"]
        models["light"] = rv["model"]
        overall = rv["overall"]
    if "advanced" in modes:
        ad = advanced.run(ctx, prompts_dir=prompts_dir)
        issues += _number(ad["issues"], "advanced")
        models.update({f"advanced:{k}": v for k, v in ad["models"].items()})
        notes += ad["notes"]
    if "coarse" in modes:
        if previous is not None:
            issues += [i for i in carried if i["source"] == "coarse"]
            models["coarse"] = (previous.get("models") or {}).get("coarse", "")
        else:
            co = external.run_coarse(ctx)
            notes += co["notes"]
            if co.get("raw"):
                (ctx["study_dir"] / "provenance").mkdir(exist_ok=True)
                (ctx["study_dir"] / "provenance" / "coarse_review.md").write_text(co["raw"], encoding="utf-8")
            issues += _number(co["issues"], "coarse")
            if co["model"]:
                models["coarse"] = co["model"]
    for ext in ("refine", "openreview"):            # imported by hand: carried over, or pending until imported
        if ext not in modes:
            continue
        got = [i for i in carried if i["source"] == ext]
        issues += got
        if got:
            models[ext] = ((previous or {}).get("models") or {}).get(ext) or f"{ext} (imported)"
        else:
            pending.append(ext)
    for n in notes:
        ctx.setdefault("degraded", []).append(n)
    out = {"models": models, "overall": overall, "issues": issues, "modes": modes}
    synthesize(ctx, out)
    overall, synthesis, issues = out["overall"], out.get("synthesis"), out["issues"]
    if not overall:
        overall = f"{len(issues)} issue(s) from the {', '.join(TITLES[m] for m in modes if m not in pending) or 'no'} review(s)."
    out = {"mode": ",".join(modes), "modes": modes, "models": models,
           "model": models.get("light") or next(iter(models.values()), ""), "overall": overall, "issues": issues,
           "pending": pending, "synthesis": synthesis, "round": round_no, "rounds": list((previous or {}).get("rounds") or []),
           "plan_match": _plan_match(ctx)}
    out["decision"] = decision_for(out)          # provisional; the sign-off on the revised text replaces it
    write_review(ctx["study_dir"], out)
    return out


def _plan_match(ctx: dict) -> dict | None:
    """The deterministic half of the Light Pass (see plan_match.py)."""
    try:
        from .plan_match import plan_match
        from .. import package as PKG
        return plan_match(ctx["pap"], ctx.get("tags") or [], PKG.read_summary(ctx["study_dir"]))
    except Exception as e:  # noqa: BLE001 - never lose the review over it
        ctx.setdefault("degraded", []).append(f"plan match failed: {e}")
        return None


def synthesize(ctx: dict, out: dict) -> dict:
    """The review orchestrator pass over a review dict (issues, models, overall): claim checks, a disposition per issue,
    editorial guidance, unresolved questions. Replaces earlier claim issues and dispositions; mutates and returns out."""
    out["issues"] = [i for i in out.get("issues") or [] if i.get("source") != "claims"]
    for i in out["issues"]:
        i.pop("disposition", None), i.pop("disposition_reason", None)
    out["synthesis"] = None
    if not ((ctx.get("cfg") or {}).get("review") or {}).get("orchestrate", True):    # claim checks run even when the referee found nothing
        return out
    from . import orchestrate
    try:
        scope = "light" if list(out.get("modes") or ["light"]) == ["light"] else None
        syn = orchestrate.run(ctx, {"issues": out["issues"]}, scope=scope)
    except Exception as e:                      # the orchestrator is an add-on; never lose the reviews
        ctx.setdefault("degraded", []).append(f"review orchestrator failed: {e}")
        return out
    if not syn:
        return out
    out["issues"] += orchestrate.claim_issues(syn)
    for i in out["issues"]:
        d = syn["dispositions"].get(i["id"])
        if d:
            i["disposition"], i["disposition_reason"] = d.get("disposition"), d.get("reason", "")
        elif i.get("source") == "claims":
            i["disposition"], i["disposition_reason"] = "editorial", "claim checked against the tables by the checking agent"
    out.setdefault("models", {})["orchestrator"] = syn["model"]
    out["overall"] = syn["assessment"] or out.get("overall", "")
    out["synthesis"] = {k: v for k, v in syn.items() if k != "dispositions"}
    return out


def import_review(ctx: dict, source: str, text: str) -> dict:
    """Add an external referee report (refine or coarse) to the study's review.json, replacing earlier issues from
    the same source. Returns the updated review."""
    from . import external
    if source not in external.EXTERNAL:
        raise ValueError(f"import source must be one of {', '.join(external.EXTERNAL)}")
    study = ctx["study_dir"]
    path = study / "review.json"
    rv = json.loads(path.read_text(encoding="utf-8")) if path.exists() else \
        {"modes": [], "models": {}, "model": "", "overall": "", "issues": [], "pending": []}
    rv["modes"] = parse_modes(rv.get("modes") or rv["mode"]) if (rv.get("modes") or rv.get("mode")) else []
    st = external.structure(ctx, text, source)
    (study / "provenance").mkdir(exist_ok=True)
    (study / "provenance" / f"{source}_review.txt").write_text(text, encoding="utf-8")
    rv["issues"] = [i for i in rv.get("issues", []) if i.get("source") != source] + _number(st["issues"], source)
    rv["modes"] = parse_modes(rv["modes"] + [source])
    rv["mode"] = ",".join(rv["modes"])
    rv.setdefault("models", {})[source] = f"{source} (imported; structured by {st['model']})"
    rv["pending"] = [p for p in rv.get("pending", []) if p != source]
    if not rv.get("overall"):
        rv["overall"] = f"{len(rv['issues'])} issue(s)."
    write_review(study, rv)
    return rv


def write_review(study: Path, rv: dict) -> None:
    issues = rv["issues"]
    modes = rv.get("modes") or parse_modes(rv.get("mode") or "light")
    models = ", ".join(f"{k} `{v}`" for k, v in (rv.get("models") or {}).items()) or f"`{rv.get('model', '')}`"
    md = [f"# Automated review: {', '.join(TITLES[m] for m in modes)}\n", f"Models: {models}. {rv.get('overall', '')}\n"]
    if rv.get("synthesis") or rv.get("signoff"):
        md.append(f"**Review outcome (round {rv.get('round', 1)}): {tally_sentence(claims_tally(rv))}.**\n")
    for r in rv.get("rounds") or []:
        md.append(f"Earlier round {r.get('round')}: {len(r.get('issues') or [])} issue(s); "
                  f"{tally_sentence(claims_tally(r))}.\n")
    if not issues:
        md.append("No issues raised.")
    for i in issues:
        src = "" if i.get("source", "light") == "light" else f", {i['source']}"
        dsp = f" [{i['disposition']}]" if i.get("disposition") else ""
        md.append(f"- **{i.get('severity', 'low')}** {i['id']} ({i['kind']}{src}){dsp} — {i.get('location', '')}: {i.get('issue', '')}\n"
                  f"  - Suggested fix: {i.get('fix', '')}" + (f"\n  - Disposition: {i.get('disposition_reason', '')}" if dsp else ""))
    syn = rv.get("synthesis") or {}
    if syn.get("claims"):
        md.append("\n## Claim checks (checking agent)\n")
        md += [f"- **{c.get('verdict')}** ({c.get('location', '')}): \"{c.get('claim')}\" — {c.get('evidence', '')}" for c in syn["claims"]]
    if syn.get("editorial"):
        md.append("\n## Corrections requested by the checking agent\n")
        md += [f"- {e}" for e in syn["editorial"]]
    if rv.get("revision_notes"):
        md.append("\n## Corrections made by the writing agent\n")
        md += [f"- {n}" for n in rv["revision_notes"]]
    if rv.get("signoff"):
        so = rv["signoff"]
        md.append(f"\n## Claim re-check on the corrected text\n\n{so.get('note', '')}\n")
        md += [f"- **{c.get('verdict')}** ({c.get('location', '')}{', re-check of ' + c['previous'] if c.get('previous') else ''}): "
               f"\"{c.get('claim')}\" — {c.get('evidence', '')}" for c in so.get("claims") or []]
    if syn.get("unresolved"):
        md.append("\n## Unresolved questions (candidates for extensions)\n")
        md += [f"- {u['id']}: {u['question']} ({u.get('why', '')})" for u in syn["unresolved"]]
    if any(i["kind"] == "analytical" for i in issues):
        md.append("\nAnalytical issues can be answered with robustness addenda: `filedrawer address <study>` proposes one per issue for approval.")
    if "refine" in (rv.get("pending") or []):
        from .external import REFINE_STEPS
        md.append("\n## Pending: Refine\n\n" + REFINE_STEPS.format(study="."))
    if "openreview" in (rv.get("pending") or []):
        from .external import OPENREVIEW_STEPS
        md.append("\n## Pending: OpenReview\n\n" + OPENREVIEW_STEPS.format(study="."))
    (study / "review.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (study / "review.json").write_text(json.dumps(rv, indent=1, ensure_ascii=False), encoding="utf-8")
