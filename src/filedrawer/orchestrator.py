"""Deterministic stage sequence. Context between stages is file paths plus compact dicts."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import __version__
from . import pii as PII
from . import tidy as TIDY
from . import pap as PAP
from . import package as PKG
from .analysis import templates as T
from .analysis.profile import profile_df
from .llm.client import make_provider
from .qsf import parse_qsf, Codebook
from . import exec as _exec

STAGES = ["ingest", "pii", "tidy", "pap", "clean", "registered", "tag", "exploratory", "litreview", "write", "review", "package"]


def _git_email() -> str:
    try:
        return subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _log(ctx, msg):
    print(f"[filedrawer] {msg}", flush=True)
    ctx["log"].append(f"{dt.datetime.utcnow().isoformat(timespec='seconds')}Z {msg}")


MANIFEST = ".filedrawer-manifest.json"
# Files a run writes at fixed paths; used when an older package has no manifest yet.
KNOWN_OUTPUTS = ("figures/design.svg", "figures/design.txt", "data/raw_tidy.csv", "data/clean.csv", "study.json", "report.md", "codebook.json", "codebook.md",
                 "pap.json", "pap.md", "survey.qsf", "review.md", "review.json", "RUN.md", "CITATION.cff")
OWNED_DIRS = ("results", "silicon", "provenance", "extensions")     # wholly pipeline-owned at a repo root (silicon/: left by older versions)


def _snapshot(study: Path) -> set[str]:
    skip = {".git", "inputs", ".mplconfig"}
    out = set()
    for p in study.rglob("*"):
        rel = p.relative_to(study)
        if rel.parts and rel.parts[0] in skip:
            continue
        if p.is_file():
            out.add(rel.as_posix())
    return out


def clear_previous_outputs(study: Path, before: float | None = None) -> list[str]:
    """Delete what the previous run recorded in its manifest (falling back to the fixed output names,
    numbered pipeline scripts and pipeline-owned dirs). Author files are never deleted."""
    man = study / MANIFEST
    removed = []
    files: list[str] = []
    if man.exists():
        try:
            files = json.loads(man.read_text(encoding="utf-8")).get("files", [])
        except (ValueError, OSError):
            files = []
    if not files:
        files = list(KNOWN_OUTPUTS)
        if (study / "scripts").is_dir():
            files += [p.relative_to(study).as_posix() for p in (study / "scripts").glob("0[0-9]_*.*")]
        files += [p.relative_to(study).as_posix() for d in OWNED_DIRS if (study / d).is_dir()
                  for p in (study / d).rglob("*") if p.is_file()]
    root = study.resolve()
    for rel in files:
        if rel in (".gitignore", "README.md", "run.sh", "AGENTS.md", "CITATION.cff", "zenodo.json") or rel.startswith("inputs/"):
            continue                                   # author-facing files: never removed, overwritten in place if regenerated
        p = study / rel
        try:
            p.resolve().relative_to(root)          # refuse anything outside the study dir
        except ValueError:
            continue
        if p.is_file():
            if before is not None and p.stat().st_mtime >= before:
                continue                                   # written by the run in progress; keep
            p.unlink()
            removed.append(rel)
    for d in ("data", "scripts", "figures") + OWNED_DIRS:   # empty dirs left behind are removed
        dd = study / d
        if dd.is_dir():
            for sub in sorted((x for x in dd.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
                if not any(sub.iterdir()):
                    sub.rmdir()
    man.unlink(missing_ok=True)
    return removed


def prune_exploratory(study: Path, exploratory: list) -> list[str]:
    """When the exploratory agent recorded no analyses, drop the draft script and any partial E*.csv tables it
    left behind, so `reproduce` does not try to run code whose outputs are not part of the package."""
    if exploratory:
        return []
    removed = []
    for p in [study / "scripts" / "04_exploratory.py"] + sorted((study / "results").glob("E*_*.csv")):
        if p.exists():
            p.unlink()
            removed.append(p.relative_to(study).as_posix())
    return removed


SNAPSHOT = "provenance/ctx_snapshot.json"


def finalize_package(ctx: dict, sections: dict, *, package_dir: bool, preexisting: set | None = None, accumulate: bool = False) -> dict:
    """Write provenance.json, study.json, RUN.md, CITATION.cff, report.md, the manifest and the context snapshot.
    With accumulate=True (an `address` round on a finished package) usage and cost are added to the stored totals."""
    study: Path = ctx["study_dir"]
    prov = ctx["provenance"]
    u = ctx["provider"].usage
    tot = u.totals()
    if accumulate:
        prev = prov.get("tokens") or {"in": 0, "out": 0}
        prov["tokens"] = {"in": int(prev.get("in", 0)) + tot["in"], "out": int(prev.get("out", 0)) + tot["out"]}
        prov["cost_usd"] = round(float(prov.get("cost_usd") or 0.0) + tot["cost"], 6)
        by = prov.setdefault("usage_by_agent", {})
        for agent, usage in u.by_agent.items():
            cur = by.setdefault(agent, {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "models": []})
            for k in ("calls", "in", "out", "cost"):
                cur[k] = cur.get(k, 0) + usage.get(k, 0)
            for m in usage.get("models", []):
                if m not in cur["models"]:
                    cur["models"].append(m)
        tot = {"in": prov["tokens"]["in"], "out": prov["tokens"]["out"], "cost": prov["cost_usd"]}
    else:
        prov["tokens"] = {"in": tot["in"], "out": tot["out"]}
        prov["cost_usd"] = tot["cost"]
        prov["usage_by_agent"] = u.by_agent
        starts = list(ctx["stage_times"].items())
        prov["stage_seconds"] = {k: round((starts[i + 1][1] if i + 1 < len(starts) else time.time()) - v, 2)
                                 for i, (k, v) in enumerate(starts)}
        prov["total_seconds"] = round(time.time() - ctx.get("t_all", time.time()), 1)
    prov["log"] = ctx["log"]
    (study / "provenance" / "provenance.json").write_text(json.dumps(prov, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    ctx["sections"] = sections
    sj = PKG.build_study_json(ctx)
    (study / "study.json").write_text(json.dumps(sj, indent=1, ensure_ascii=False), encoding="utf-8")
    PKG.write_run_md(study, ctx["meta"])
    (study / "CITATION.cff").write_text(PKG.citation(ctx["meta"], prov, PKG._doi(ctx))["cff"], encoding="utf-8")
    (study / "report.md").write_text(PKG.render_report(ctx, sections), encoding="utf-8")   # re-render with final file list
    snap = {"tool_version": __version__, "meta": ctx["meta"], "pap_source": ctx.get("pap_source"), "data_columns": ctx.get("data_columns", []),
            "categorical": ctx.get("categorical", []), "exploratory": ctx.get("exploratory", []), "lit": ctx.get("lit", {}),
            "tags": ctx.get("tags", []), "sections": sections, "extensions": ctx.get("extensions", []),
            "sections_draft": ctx.get("sections_draft"), "potential": ctx.get("potential")}
    (study / SNAPSHOT).write_text(json.dumps(snap, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    shutil.rmtree(study / ".mplconfig", ignore_errors=True)
    if package_dir:
        man = study / MANIFEST
        prev_files = set()
        if accumulate and man.exists():
            try:
                prev_files = set(json.loads(man.read_text(encoding="utf-8")).get("files", []))
            except (ValueError, OSError):
                prev_files = set()
        written = sorted((_snapshot(study) - (preexisting or set())) | {f for f in KNOWN_OUTPUTS if (study / f).exists()} | prev_files) \
            if not accumulate else sorted(prev_files | {f for f in KNOWN_OUTPUTS if (study / f).exists()}
                                          | {p for p in _snapshot(study) if p.startswith(("results/", "figures/", "scripts/", "provenance/", "extensions/")) or p in ("responses.md", "review.json")})
        man.write_text(json.dumps({"tool_version": __version__, "files": written}, indent=1), encoding="utf-8")
    return tot


def run_extensions(ctx: dict) -> list[dict]:
    """Propose follow-up designs (autoexperiment schema), validate them, write extensions/. Never fatal."""
    from .agents import extensions as EXT_AGENT
    from .agents import potential as POT
    from . import extensions as X
    study = ctx["study_dir"]
    # the research-potential memo first: it is the brief the designs come from, and a report section of its own
    try:
        memo, memo_problems = POT.run(ctx)
    except Exception as e:
        memo, memo_problems = None, [f"potential agent failed: {e}"]
    if memo:
        ctx["potential"] = memo
        (study / "provenance").mkdir(exist_ok=True)
        (study / "provenance" / "potential.json").write_text(json.dumps(memo, indent=1, ensure_ascii=False), encoding="utf-8")
        _log(ctx, f"research potential: {len(memo.get('why_it_did_not_land') or [])} cause(s), {len(memo.get('debates') or [])} debate(s), "
                  f"{len(memo.get('briefs') or [])} brief(s)")
    else:
        for p in memo_problems:
            ctx["degraded"].append("research potential: " + p)
        _log(ctx, "research potential: no memo; designing the legacy extension kinds")
    try:
        props, validator, problems = EXT_AGENT.run(ctx)
    except Exception as e:                                   # extensions are an add-on; a failure must not lose the package
        props, validator, problems = [], "structural", [f"extensions agent failed: {e}"]
    rows = X.save(ctx["study_dir"], props, validator, ctx["meta"].get("title", "")) if props else X.load_index(ctx["study_dir"])
    try:
        from . import diagram as DIAG
        rows = DIAG.write_extension_diagrams(ctx["study_dir"], rows)
    except Exception as e:
        ctx["degraded"].append(f"extension diagrams: {e}")
    ctx["extensions"] = rows
    for p in problems:
        ctx["degraded"].append("extensions: " + p)
    _log(ctx, f"extensions: {len(props)} proposal(s) validated by {validator}" + (f"; dropped: {'; '.join(problems)[:300]}" if problems else ""))
    return rows


def respondent_counts(pap: dict, raw, clean) -> dict:
    """When each respondent contributes several rows (conjoint profiles, panel waves), N is the number of respondents,
    counted on the column the plan clusters by; the row counts are kept alongside."""
    hyps = (pap.get("implemented") or pap.get("registered") or {}).get("hypotheses") or []
    for col in dict.fromkeys(c for c in ((h.get("estimator") or {}).get("cluster") for h in hyps) if c):
        if col in raw.columns and col in clean.columns and raw[col].nunique() < len(raw):
            return {"n_raw": int(raw[col].nunique()), "n_analysis": int(clean[col].nunique()), "unit_column": col,
                    "rows_raw": len(raw), "rows_analysis": len(clean)}
    return {"n_raw": len(raw), "n_analysis": len(clean)}


def run_pipeline(inputs: dict, cfg: dict, flags: dict) -> Path:
    """inputs: csv, qsf (optional), pap, slug, title, authors, out_dir, repo_url, branch."""
    import pandas as pd
    study = Path(inputs["package_dir"]) if inputs.get("package_dir") else Path(inputs["out_dir"]) / inputs["slug"]
    for key in ("csv", "pap", "qsf", "pap_json"):
        if inputs.get(key) and not Path(inputs[key]).is_file():
            raise SystemExit(f"input {key} not found: {inputs[key]}")
    t_start = time.time()
    deferred_clear = False
    if study.exists() and not flags.get("from_stage"):
        if inputs.get("package_dir"):
            # package at a repo root: previous outputs are removed only after the plan stage succeeds, so a run
            # that fails on its inputs leaves the committed package intact. Author files are never touched.
            deferred_clear = True
        else:
            shutil.rmtree(study)
    for d in ("data", "scripts", "results", "figures", "provenance"):
        (study / d).mkdir(parents=True, exist_ok=True)
    preexisting = _snapshot(study) if inputs.get("package_dir") else set()
    ctx: dict = {"cfg": cfg, "study_dir": study, "log": [], "stage_times": {}, "degraded": [], "git_email": _git_email()}
    ctx["provider"] = make_provider(cfg, study / "provenance" / "llm_log.jsonl", fixtures_dir=flags.get("fixtures"))
    ctx["meta"] = {"slug": inputs["slug"], "title": inputs["title"], "authors": inputs.get("authors", []),
                   "synthetic": bool(flags.get("synthetic")), "release_status": "draft",
                   "repo_url": inputs.get("repo_url", "https://github.com/yrvelez/filedrawer"), "branch": inputs.get("branch", "main"),
                   "package_at_root": bool(inputs.get("package_dir"))}
    created = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    t_all = time.time()

    def stage(name):
        ctx["stage_times"][name] = time.time()
        _log(ctx, f"stage {name}")

    # ---- ingest -------------------------------------------------------------
    stage("ingest")
    raw = Path(inputs["csv"])
    pap_text = Path(inputs["pap"]).read_text(encoding="utf-8")
    (study / "pap.md").write_text(pap_text, encoding="utf-8")
    if inputs.get("qsf"):
        shutil.copy(inputs["qsf"], study / "survey.qsf")
    gi = study / ".gitignore"
    if not gi.exists():
        gi.write_text("raw_export.csv\n.mplconfig/\n", encoding="utf-8")
    hashes = {"raw_csv_sha256": PKG.sha256(raw), "pap_sha256": PKG.sha256(inputs["pap"]),
              "qsf_sha256": PKG.sha256(inputs["qsf"]) if inputs.get("qsf") else None}

    # ---- codebook + pii -------------------------------------------------------
    stage("pii")
    df, meta = TIDY.read_export(raw)
    if inputs.get("qsf"):
        cb = parse_qsf(inputs["qsf"])
    else:
        cb = TIDY.codebook_from_csv(list(df.columns), meta)
        ctx["degraded"].append("no QSF: codebook inferred from CSV header rows; arms must come from the PAP")
    rep = PII.scan(raw, free_text_columns=cb.free_text_columns(), closed_columns=cb.closed_columns(),
                   allow_pii=flags.get("allow_pii", False),
                   keep=flags.get("pii_keep") or [])
    if flags.get("pii_model") or (cfg.get("pii") or {}).get("model"):
        from . import pii_model                       # optional local model over the kept text columns
        extra = pii_model.scan_frame(df, exclude=set(rep.flagged))
        rep.flagged.update(extra)
        _log(ctx, f"pii model: flagged {len(extra)} more column(s)" + (f" ({', '.join(extra)})" if extra else ""))
    _log(ctx, PII.summary_line(rep))
    if rep.allow_pii and rep.flagged:
        _log(ctx, "WARNING: --allow-pii is set; flagged columns are retained in the package. Do not release without review.")

    # ---- tidy ---------------------------------------------------------------------
    stage("tidy")
    TIDY.reconcile(cb, df, meta, rep)
    TIDY.resolve_arms(cb, df, pap_arm_column=flags.get("arm_column"))
    kept = TIDY.write_raw_tidy(df, rep, study / "data" / "raw_tidy.csv")
    cb.save(study / "codebook.json")
    (study / "codebook.md").write_text(cb.to_markdown(), encoding="utf-8")
    (study / "scripts" / "01_tidy.py").write_text(T.render_tidy("raw_export.csv", rep.to_drop, rep.qualtrics_header_rows, __version__), encoding="utf-8")
    raw_tidy = pd.read_csv(study / "data" / "raw_tidy.csv")
    free = set(cb.free_text_columns())
    ctx.update(codebook=cb, pii_report=rep, raw_profile=profile_df(raw_tidy, cb.columns, free_text=free),
               data_columns=list(raw_tidy.columns), pap_text=pap_text)
    n_raw = len(raw_tidy)

    # ---- pap ----------------------------------------------------------------------
    stage("pap")
    if inputs.get("pap_json"):
        pap = PAP.load_pap(inputs["pap_json"])
        pap.setdefault("source", Path(inputs["pap_json"]).name)
        errs = PAP.validate(pap, ctx["data_columns"])
        if errs:
            raise SystemExit(f"{inputs['pap_json']} is not runnable on this data:\n- " + "\n- ".join(errs))
        ctx["pap_source"] = "author-supplied pap.json"
        hashes["pap_json_sha256"] = PKG.sha256(inputs["pap_json"])
        _log(ctx, f"pap: loaded author-supplied {inputs['pap_json']} (papreader agent skipped)")
    else:
        from .agents import papreader
        pap = papreader.run(ctx)
        ctx["pap_source"] = "papreader agent"
    if cb.arms.column and not pap.get("design", {}).get("arms", {}).get("column"):
        vals = [PAP.arm_str(v) for v in cb.arms.values]
        pap.setdefault("design", {})["arms"] = {"column": cb.arms.column, "control": vals[0] if vals else None,
                                                "treatment": vals[1:] if len(vals) > 2 else (vals[-1] if vals else None)}
    PAP.normalize_arms(pap)
    swaps = PAP.outcome_changes(pap)
    if swaps:
        msg = "the analysed outcome differs from the plan's outcome:\n- " + "\n- ".join(swaps)
        if not flags.get("allow_outcome_change"):
            raise SystemExit("Stopping: " + msg + "\nSupply data with the planned outcome's columns, fix the plan, "
                             "or rerun with --allow-outcome-change to analyse the substitute (flagged in the report).")
        ctx["degraded"].append("OUTCOME CHANGED FROM PLAN (--allow-outcome-change): " + "; ".join(swaps))
        _log(ctx, "WARNING: " + msg)
    pap.setdefault("title", inputs["title"])
    PAP.save_pap(pap, study / "pap.json")
    ctx["pap"] = pap

    if deferred_clear:
        removed = clear_previous_outputs(study, before=t_start)
        if removed:
            _log(ctx, f"cleared {len(removed)} file(s) from the previous run")
    # ---- clean --------------------------------------------------------------------
    stage("clean")
    from .agents import cleaner, registered as REG, exploratory as EXP
    (study / "scripts" / "02_clean.py").write_text(T.render_clean(pap, __version__), encoding="utf-8")
    if cleaner.needs_agent(pap):
        cleaner.run(ctx)
    res = _exec.run_script(study, "scripts/02_clean.py", cfg["analysis"]["script_timeout_s"], cfg["analysis"]["language"])
    if res["exit_code"] != 0:
        raise SystemExit("02_clean.py failed:\n" + res["stderr_tail"])
    _log(ctx, res["stdout_tail"].strip().splitlines()[-1])
    clean = pd.read_csv(study / "data" / "clean.csv")
    ctx["clean_profile"] = profile_df(clean, cb.columns, free_text=free)
    n_analysis = len(clean)

    # ---- registered ---------------------------------------------------------------
    stage("registered")
    categorical = [c["name"] for c in cb.columns if c.get("values") and c["name"] in clean.columns]
    ctx["categorical"] = categorical
    (study / "scripts" / "03_registered.py").write_text(T.render_registered(pap, categorical, __version__), encoding="utf-8")
    res = _exec.run_script(study, "scripts/03_registered.py", cfg["analysis"]["script_timeout_s"], cfg["analysis"]["language"])
    if res["exit_code"] != 0:
        raise SystemExit("03_registered.py failed:\n" + res["stderr_tail"])
    for line in res["stdout_tail"].strip().splitlines():
        _log(ctx, line)
    if REG.needs_agent(pap):
        ctx["custom_results"] = REG.run(ctx)

    # ---- tag ----------------------------------------------------------------------
    stage("tag")
    # ---- exploratory --------------------------------------------------------------
    stage("exploratory")
    ctx["exploratory"] = EXP.run(ctx) if not flags.get("no_exploratory") else []
    pruned = prune_exploratory(study, ctx["exploratory"])
    if pruned:
        ctx["degraded"].append("exploratory agent produced no analyses; its draft script/tables were removed: " + ", ".join(pruned))
        _log(ctx, "exploratory: no analyses recorded; removed " + ", ".join(pruned))
    ctx["tags"] = PAP.tag_analyses(pap, ctx["exploratory"])
    PAP.tags_to_csv(ctx["tags"], study / "results" / "analysis_tags.csv")
    _log(ctx, "tags: " + ", ".join(f"{t['analysis_id']}={t['tag']}" for t in ctx["tags"]))

    # ---- litreview ----------------------------------------------------------------
    stage("litreview")
    if flags.get("no_lit"):
        ctx["lit"] = {"text": "", "works": [], "queries": [], "degraded": "skipped by flag"}
    else:
        from .agents import litreview
        from . import openalex
        fetcher = openalex.MockFetcher(flags["openalex_fixture"]) if flags.get("openalex_fixture") else None
        ctx["lit"] = litreview.run(ctx, fetcher=fetcher)
    if ctx["lit"].get("degraded"):
        ctx["degraded"].append("litreview: " + ctx["lit"]["degraded"])
    (study / "provenance" / "literature.json").write_text(json.dumps(ctx["lit"], indent=1, ensure_ascii=False), encoding="utf-8")

    # ---- synthetic respondents against a human benchmark, when the plan names one --------------------
    if PAP.sample_kind(ctx["pap"]) != "human":
        try:
            from .analysis import benchmark
            bm = benchmark.compare(study, ctx["pap"])
            _log(ctx, f"benchmark against human respondents: {'results/benchmark_human.csv' if bm else 'no benchmark table'}")
        except Exception as e:
            ctx["degraded"].append(f"benchmark: {e}")

    # ---- provenance + write -------------------------------------------------------
    stage("write")
    ctx["meta"].update(respondent_counts(ctx["pap"], raw_tidy, clean))
    # ---- the design diagram (template; a model draws it only when the plan marks the design non-standard) ----
    try:
        from . import diagram as DIAG
        fb = None
        if (ctx["pap"].get("design") or {}).get("nonstandard"):
            from .agents import diagram as DIAG_AGENT
            fb = lambda text: DIAG_AGENT.run(ctx, ctx["pap"].get("design") or {}, text)
        ctx["diagram"] = DIAG.write_study_diagram(study, ctx["pap"], ctx["meta"], fallback=fb)
        _log(ctx, f"diagram: {ctx['diagram'].get('source') or 'none drawn'} (figures/design.svg)")
    except Exception as e:                                   # a diagram is an add-on; never lose the package
        ctx["degraded"].append(f"diagram: {e}")
    ctx["provenance"] = {
        "mode": "fully_agentic", "human_steps": [], "reviewer_pass": False,
        "models": dict(cfg["models"]), "provider": ctx["provider"].name,
        "zero_retention_requested": bool(cfg["openrouter"].get("zero_data_retention", True)) if ctx["provider"].name == "openrouter" else False,
        "cost_usd": 0.0, "tokens": {"in": 0, "out": 0}, "tool_version": __version__, "created": created,
        "pap_source": ctx.get("pap_source", "papreader agent"),
        "pii": {"dropped": rep.to_drop, "kept_with_override": rep.kept_with_override, "allow_pii": rep.allow_pii,
                "flagged": rep.flagged, "scan": "header + first data row only, local regex"},
        "degraded": ctx["degraded"], "inputs": hashes,
    }
    from .agents import writer
    sections = writer.run(ctx)
    ctx["review_issues"] = None
    (study / "report.md").write_text(PKG.render_report(ctx, sections), encoding="utf-8")

    # ---- review -------------------------------------------------------------------
    stage("review")
    if flags.get("review"):
        from .review import run_review, revision_brief, signoff
        rv = run_review(ctx, flags["review"], prompts_dir=flags.get("advanced_prompts"))
        mode = rv["mode"]
        ctx["review_issues"] = rv["issues"]
        ctx["review"] = rv
        ctx["provenance"]["reviewer_pass"] = True
        ctx["provenance"]["review_mode"] = mode
        _log(ctx, f"review ({mode}): {len(rv['issues'])} issue(s); provisional decision: {rv.get('decision')}")
        ctx["sections_draft"] = sections
        review_cfg = cfg.get("review") or {}
        # ---- the agent answers serious analytical issues with robustness checks (no person in the loop) ----
        fixed = []
        if review_cfg.get("agent_fix", True) and not flags.get("no_revise"):
            from .address import agent_fix
            stage("robustness")
            fixed = agent_fix(ctx, rv, cap=int(review_cfg.get("agent_fix_cap", 3)))
            if fixed:
                rv["agent_addenda"] = [a["id"] for a in fixed]
                from .review import write_review
                write_review(study, rv)
                _log(ctx, "agent robustness checks: " + ", ".join(f"{a['id']} for {a['responds_to']}" for a in fixed))
        # ---- the writing agent applies the flagged corrections, then the claims are re-checked on the corrected text ----
        brief = revision_brief(rv) if (review_cfg.get("revise", True) and not flags.get("no_revise")) else None
        if fixed and not brief and not flags.get("no_revise"):
            brief = {"round": rv.get("round", 1), "editorial": [], "claims": [], "presentational": [], "declined": []}
        if brief:
            stage("revise")
            from .review import apply_corrections
            sections = apply_corrections(ctx, rv, sections, brief,
                                         lambda sec: (study / "report.md").write_text(PKG.render_report(ctx, sec), encoding="utf-8"),
                                         passes=int(review_cfg.get("correction_passes", 2)), log=lambda m: _log(ctx, m))
        elif flags.get("review_revise") and rv["issues"]:      # legacy: one revision turn on the raw issue list
            sections = writer.run(ctx, review_issues=rv["issues"]) or sections
        ctx["provenance"]["review_rounds"] = [{"round": rv.get("round", 1), "decision": rv.get("decision"),
                                               "k_issues": sum(1 for i in rv["issues"] if i.get("source") == "claims"),
                                               "revised": bool(rv.get("revision_notes")), "agent_addenda": rv.get("agent_addenda") or []}]
        (study / "report.md").write_text(PKG.render_report(ctx, sections), encoding="utf-8")

    # ---- extensions ---------------------------------------------------------------
    if flags.get("extensions"):
        stage("extensions")
        ctx["sections"] = sections
        run_extensions(ctx)

    # ---- package ------------------------------------------------------------------
    stage("package")
    ctx["sections"] = sections
    ctx["t_all"] = t_all
    tot = finalize_package(ctx, sections, package_dir=bool(inputs.get("package_dir")), preexisting=preexisting)
    _log(ctx, f"done: {study} (tokens in/out {tot['in']}/{tot['out']}, cost ${tot['cost']:.4f})")
    return study
