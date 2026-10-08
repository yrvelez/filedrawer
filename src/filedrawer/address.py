"""`filedrawer address`: answer the automated reviewer's analytical comments with robustness addenda.

The pipeline does this itself for high- and medium-severity analytical issues (see `agent_fix`): the responder agent
proposes a spec delta on one hypothesis and the agent runs it. The `filedrawer address` command repeats the step on a
finished package, optionally with a named person approving each addendum (recorded as a human step). The registered script is re-rendered with the addenda materialised as extra hypotheses (ids like H2a),
re-run, re-tagged ("robustness"), the report is re-written and re-reviewed, and responses.md records the
point-by-point reply. The registered analyses themselves are never modified.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import __version__
from . import pap as PAP
from . import package as PKG
from .analysis import templates as T
from . import exec as _exec
from .llm.client import make_provider
from .qsf import Codebook

SNAPSHOT = "provenance/ctx_snapshot.json"


def _log(ctx, msg):
    print(f"[filedrawer] {msg}", flush=True)
    ctx["log"].append(f"{dt.datetime.utcnow().isoformat(timespec='seconds')}Z {msg}")


def _ext_index(study: Path) -> list:
    p = study / "extensions" / "index.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _review_issues(study: Path) -> list | None:
    """The saved review's issues, so a re-render keeps the report's reviewer-pass note."""
    p = Path(study) / "review.json"
    try:
        return list(json.loads(p.read_text(encoding="utf-8")).get("issues") or []) if p.exists() else None
    except (ValueError, OSError):
        return None


def load_context(study: Path, cfg: dict, fixtures=None) -> dict:
    """Rebuild the pipeline context from a finished package (no agents are re-run)."""
    snap_path = study / SNAPSHOT
    if not snap_path.exists():
        raise SystemExit(f"{study} has no {SNAPSHOT}; re-run the pipeline once with this version of filedrawer first.")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    prov = json.loads((study / "provenance" / "provenance.json").read_text(encoding="utf-8"))
    pap = PAP.expand_addenda(PAP.load_pap(study / "pap.json"))      # approved/run addenda become H1a-style hypotheses again
    ctx: dict = {"cfg": cfg, "study_dir": study, "log": list(prov.get("log") or []), "stage_times": {}, "degraded": list(prov.get("degraded") or []),
                 "meta": snap["meta"], "pap": pap, "pap_source": snap.get("pap_source"), "data_columns": snap["data_columns"],
                 "categorical": snap.get("categorical", []), "exploratory": snap.get("exploratory", []), "lit": snap.get("lit", {}),
                 "tags": snap.get("tags", []), "sections": snap.get("sections", {}), "sections_draft": snap.get("sections_draft"),
                 "provenance": prov, "review_issues": _review_issues(study),
                 "extensions": _ext_index(study) or snap.get("extensions") or [], "potential": snap.get("potential"),
                 "codebook": Codebook.load(study / "codebook.json") if (study / "codebook.json").exists() else None}
    sj_path = study / "study.json"                 # a release recorded after the run must survive any re-render
    if sj_path.exists():
        try:
            sj = json.loads(sj_path.read_text(encoding="utf-8"))
            if sj.get("release_status"):
                ctx["meta"] = {**ctx["meta"], "release_status": sj["release_status"],
                               **({"released": sj["released"]} if sj.get("released") else {})}
        except ValueError:
            pass
    if (study / "responses.md").exists():
        ctx["responses"] = (study / "responses.md").read_text(encoding="utf-8").rstrip("\n").split("\n")
    ctx["provider"] = make_provider(cfg, study / "provenance" / "llm_log.jsonl", fixtures_dir=fixtures)
    return ctx


def select_issues(review: dict, wanted: list[str] | None) -> list[dict]:
    issues = review.get("issues") or []
    if wanted:
        want = {w.strip().upper() for w in wanted}
        return [i for i in issues if str(i.get("id", "")).upper() in want]
    return [i for i in issues if i.get("kind") == "analytical" and i.get("severity") in ("high", "medium")
            and i.get("disposition", "address") == "address" and not i.get("answered_by")]


def _ask(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    ans = input(prompt + " [y/N] ").strip().lower()
    return ans in ("y", "yes")


def _row(summary_rows: list[dict], hid: str) -> dict | None:
    for r in summary_rows:
        if r.get("analysis_id") == hid and not r.get("term"):
            return r
    for r in summary_rows:
        if r.get("analysis_id") == f"{hid}:pooled":
            return r
    return None


def _fmt(r: dict | None) -> str:
    if not r:
        return "no estimate"
    try:
        from .package import _pv
        return f"{float(r['estimate']):+.3f} (SE {float(r['std_error']):.3f}, {_pv(r['p_value'])}, N = {int(float(r['n']))})"
    except (KeyError, ValueError, TypeError):
        return "no estimate"


def propose(ctx: dict, chosen: list[dict], verbose: bool = True) -> list[dict]:
    """The responder agent turns each chosen analytical issue into one robustness addendum (status "proposed")."""
    from .agents import responder
    pap = ctx["pap"]
    pap.setdefault("addenda", [])
    imp_by = {h["id"]: h for h in pap["implemented"]["hypotheses"] if not h.get("base")}
    proposals = []
    for iss in chosen:
        target = (iss.get("change") or {}).get("target") or iss.get("location") or ""
        base_id = next((hid for hid in imp_by if hid and str(target).upper().startswith(hid.upper())), None)
        if base_id is None:
            base_id = next(iter(imp_by))
        _log(ctx, f"responder: proposing an addendum for {iss.get('id')} on {base_id}")
        add = responder.run(ctx, iss, imp_by[base_id])
        if not add:
            _log(ctx, f"responder: no runnable addendum for {iss.get('id')}")
            continue
        add_id = PAP.next_addendum_id(pap, add["base"], taken={p["id"] for p in proposals})
        add.update({"id": add_id, "responds_to": iss.get("id"), "issue": iss.get("issue"), "status": "proposed",
                    "proposed_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        proposals.append(add)
        if verbose:
            print(f"\n  {add_id}  responds to {iss.get('id')} [{iss.get('severity')}] {iss.get('location')}\n"
                  f"      issue : {iss.get('issue')}\n      change: {add.get('label')}  {json.dumps(add.get('delta'))}\n"
                  f"      why   : {add.get('rationale')}")
    return proposals


def run_addenda(ctx: dict, approved: list[dict]) -> dict:
    """Add approved addenda to the plan, re-render and re-run the registered script with them materialised (ids like
    H2a), re-tag, save the plan's addenda and set ctx["addenda_prompt"] for the writer. Registered analyses are untouched.
    Raises RuntimeError when the addenda do not validate or the script fails; the caller decides what to do."""
    study: Path = ctx["study_dir"]
    cfg = ctx["cfg"]
    pap = ctx["pap"]
    base = {k: v for k, v in pap.items()}
    base["addenda"] = list(pap.get("addenda") or []) + approved
    base["implemented"] = {**pap["implemented"], "hypotheses": [h for h in pap["implemented"]["hypotheses"] if not h.get("base")]}
    expanded = PAP.expand_addenda(base)
    errs = PAP.validate(expanded, ctx["data_columns"])
    if errs:
        raise RuntimeError("addenda are not runnable: " + "; ".join(errs))
    _log(ctx, "stage registered (with addenda: " + ", ".join(a["id"] for a in approved) + ")")
    (study / "scripts" / "03_registered.py").write_text(T.render_registered(expanded, ctx["categorical"], __version__), encoding="utf-8")
    res = _exec.run_script(study, "scripts/03_registered.py", cfg["analysis"]["script_timeout_s"], cfg["analysis"]["language"])
    if res["exit_code"] != 0:
        raise RuntimeError("03_registered.py failed with the addenda: " + res["stderr_tail"][-600:])
    for line in res["stdout_tail"].strip().splitlines():
        _log(ctx, line)
    for a in approved:
        a["status"] = "run"
    ctx["pap"] = expanded
    ctx["tags"] = PAP.tag_analyses(expanded, ctx["exploratory"])
    PAP.tags_to_csv(ctx["tags"], study / "results" / "analysis_tags.csv")
    pap_to_save = PAP.load_pap(study / "pap.json")
    pap_to_save["addenda"] = base["addenda"]
    PAP.save_pap(pap_to_save, study / "pap.json")
    ctx["addenda_prompt"] = "\n".join(f"- {a['id']} (robustness, responds to reviewer issue {a['responds_to']}: \"{a.get('issue', '')}\"): "
                                      f"{a['label']}; base {a['base']}. {a.get('rationale', '')}" for a in base["addenda"] if a.get("status") == "run")
    return expanded


def agent_fix(ctx: dict, rv: dict, cap: int = 3) -> list[dict]:
    """In the pipeline: the agent answers the serious analytical issues (high, then medium severity; disposition
    "address") with robustness addenda, without waiting for a person. Returns the addenda that ran."""
    chosen = sorted(select_issues(rv, None), key=lambda i: {"high": 0, "medium": 1}.get(i.get("severity"), 2))[:cap]
    if not chosen:
        return []
    proposals = propose(ctx, chosen, verbose=False)
    for a in proposals:
        a.update(status="approved", approved_by="agent")
    if not proposals:
        return []
    try:
        run_addenda(ctx, proposals)
    except RuntimeError as e:
        ctx.setdefault("degraded", []).append(f"agent robustness checks not run: {e}")
        _log(ctx, f"agent robustness checks not run: {e}")
        return []
    by_issue = {a["responds_to"]: a["id"] for a in proposals}
    for i in rv.get("issues") or []:
        if i.get("id") in by_issue:
            i["answered_by"] = by_issue[i["id"]]
    return proposals


def run_address(study: Path, cfg: dict, issues: list[str] | None = None, yes: bool = False, dry_run: bool = False,
                by: str = "", fixtures=None) -> dict:
    study = Path(study).resolve()
    ctx = load_context(study, cfg, fixtures)
    review_path = study / "review.json"
    if not review_path.exists():
        raise SystemExit("no review.json in this package; run the pipeline with --review first.")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    chosen = select_issues(review, issues)
    if not chosen:
        print("No analytical issues to address" + (f" among {issues}" if issues else " (high/medium)") + ".")
        return {"addenda": [], "ran": False}
    from .agents import writer
    from .review import run_review, revision_brief, signoff, history_append
    pap = ctx["pap"]
    pap.setdefault("addenda", [])
    proposals = propose(ctx, chosen)
    (study / "provenance" / "addenda_proposed.json").write_text(json.dumps(proposals, indent=1, ensure_ascii=False), encoding="utf-8")
    if dry_run:
        print(f"\n{len(proposals)} proposal(s) written to provenance/addenda_proposed.json; nothing was run.")
        return {"addenda": proposals, "ran": False}
    approved = []
    for add in proposals:
        ok = yes or _ask(f"Approve {add['id']} ({add['label']})?")
        if ok:
            add["status"] = "approved"
            add["approved_by"] = by or "agent"
            approved.append(add)
    if not approved:
        print("Nothing approved; nothing run.")
        return {"addenda": proposals, "ran": False}
    t0 = time.time()
    try:
        expanded = run_addenda(ctx, approved)
    except RuntimeError as e:
        raise SystemExit(str(e))
    pap = expanded
    # ---- re-write and re-review ----------------------------------------------------------------
    _log(ctx, "stage write (with addenda)")
    sections = writer.run(ctx) or ctx["sections"]
    ctx["sections"] = sections
    (study / "provenance" / "review_before_address.json").write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf-8")
    (study / "report.md").write_text(PKG.render_report(ctx, sections), encoding="utf-8")
    modes = review.get("modes") or review.get("mode") or "light"
    round_no = int(review.get("round") or 1) + 1
    _log(ctx, f"stage review (round {round_no}, {modes if isinstance(modes, str) else ','.join(modes)})")
    history_append(review)                                   # round 1 stays in review.json["rounds"]
    rv = run_review(ctx, modes, previous=review, round_no=round_no)
    ctx["review_issues"] = rv["issues"]
    ctx["review"] = rv
    # ---- the writing agent applies the round-2 corrections, then the claims are re-checked ---------
    review_cfg = cfg.get("review") or {}
    brief = revision_brief(rv) if review_cfg.get("revise", True) else None
    if brief:
        _log(ctx, "stage revise (round 2 corrections)")
        from .review import apply_corrections
        sections = apply_corrections(ctx, rv, sections, brief,
                                     lambda sec: (study / "report.md").write_text(PKG.render_report(ctx, sec), encoding="utf-8"),
                                     passes=int(review_cfg.get("correction_passes", 2)), log=lambda m: _log(ctx, m))
        ctx["sections"] = sections
    ctx["provenance"].setdefault("review_rounds", []).append(
        {"round": round_no, "decision": rv.get("decision"), "k_issues": sum(1 for i in rv["issues"] if i.get("source") == "claims"),
         "revised": bool(rv.get("revision_notes"))})
    # ---- responses memo ------------------------------------------------------------------------
    summary_rows = PKG.read_summary(study)
    memo = ["# Responses to the automated reviewer\n",
            f"Round 1 raised {len(review.get('issues') or [])} issue(s); {len(approved)} analytical issue(s) were answered with robustness "
            f"addenda, approved by {'the agent' if approved[0]['approved_by'] == 'agent' else approved[0]['approved_by']} on {dt.date.today().isoformat()}. Registered analyses were not changed.\n"]
    for a in approved:
        base_row, add_row = _row(summary_rows, a["base"]), _row(summary_rows, a["id"])
        memo.append(f"## {a['responds_to']}: {a.get('issue', '')}\n")
        memo.append(f"**Response.** {a.get('rationale', '')} Added {a['id']}, a robustness re-estimation of {a['base']}: {a['label']} "
                    f"(`{json.dumps(a.get('delta'), ensure_ascii=False)}`).\n")
        memo.append(f"**Result.** {a['base']}: {_fmt(base_row)}. {a['id']}: {_fmt(add_row)}.\n")
    memo.append(f"## Second reviewer pass\n\n{rv.get('overall', '')}\n")
    if rv["issues"]:
        memo.append("Remaining issues:\n")
        memo += [f"- **{i.get('severity')}** {i.get('id', '')} — {i.get('location', '')}: {i.get('issue', '')}" for i in rv["issues"]]
    (study / "responses.md").write_text("\n".join(memo) + "\n", encoding="utf-8")
    ctx["responses"] = memo
    # ---- provenance and package ---------------------------------------------------------------
    prov = ctx["provenance"]
    if by:                                       # only a named person approving counts as a human step
        for a in approved:
            prov.setdefault("human_steps", []).append({"step": f"approved robustness addendum {a['id']} responding to reviewer issue {a['responds_to']}",
                                                       "by": by, "date": dt.date.today().isoformat()})
        prov["mode"] = "human_reviewed"
    prov["reviewer_pass"] = True
    prov.setdefault("address_rounds", []).append({"date": dt.date.today().isoformat(), "addenda": [a["id"] for a in approved],
                                                  "seconds": round(time.time() - t0, 1)})
    from .orchestrator import finalize_package
    finalize_package(ctx, sections, package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
    _log(ctx, f"address done: {', '.join(a['id'] for a in approved)}")
    return {"addenda": approved, "ran": True, "review": rv}
