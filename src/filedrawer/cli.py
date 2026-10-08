"""filedrawer command line."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path

from . import __version__
from .config import load_config, REPO_ROOT


def _overrides(a) -> dict:
    """Config overrides from --provider / --local / --local-model / --local-url / --strong-model / --fast-model."""
    o: dict = {"provider": a.provider} if getattr(a, "provider", None) else {}
    if getattr(a, "local", False) or getattr(a, "local_model", None) or getattr(a, "local_url", None):
        o["provider"] = "local"
        loc: dict = {}
        if getattr(a, "local_model", None):
            loc["models"] = {"strong": a.local_model, "fast": a.local_model}
        if getattr(a, "local_url", None):
            loc["base_url"] = a.local_url
        if loc:
            o["local"] = loc
    models = {t: getattr(a, f"{t}_model") for t in ("strong", "fast") if getattr(a, f"{t}_model", None)}
    if models:
        o["models"] = models
    return o


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provider", choices=["openrouter", "local", "mock"], help="LLM provider (default from config.yaml)")
    p.add_argument("--strong-model", help="model for the strong tier (plan reading, writing, review), e.g. anthropic/claude-sonnet-5.5")
    p.add_argument("--fast-model", help="model for the fast tier (code, literature, design), e.g. anthropic/claude-haiku-5.5")
    p.add_argument("--local", action="store_true", help="run every agent on a local model server (Ollama by default); "
                   "no API key, nothing leaves this machine")
    p.add_argument("--local-model", help="with --local: one model for every agent, e.g. qwen3:32b (default: local.models in config.yaml)")
    p.add_argument("--local-url", help="with --local: the server's OpenAI-compatible URL (default http://localhost:11434/v1, Ollama)")
    p.add_argument("--config", help="extra config YAML")
    p.add_argument("--out", default=str(REPO_ROOT / "studies"), help="studies directory (default: ./studies)")
    p.add_argument("--review", nargs="?", const="light", default="light", metavar="MODES",
                   help="review (default: light). light = the Light Pass: reported estimates against the "
                        "result tables and analyses against the pre-analysis plan. Outside reviews (coarse, refine, openreview "
                        "or any referee report) are added afterwards with filedrawer review-import.")
    p.add_argument("--no-review", dest="review", action="store_const", const=None, help="skip the review (the badge then says unreviewed)")
    p.add_argument("--advanced-prompts", help=argparse.SUPPRESS)   # retired advanced pass; kept so older packages still re-render
    p.add_argument("--review-revise", action="store_true", help="legacy: one revision turn on the raw issue list (the decision-letter revision is now the default)")
    p.add_argument("--no-revise", action="store_true", help="skip the agent's corrections, robustness checks and claim re-check (one draft only)")
    p.add_argument("--allow-pii", action="store_true", help="keep columns flagged as identifiers/free text (loud warning in provenance)")
    p.add_argument("--pii-keep", default="", help="comma-separated flagged columns to keep")
    p.add_argument("--pii-model", action="store_true",
                   help="also read the kept text columns with a small local PII model (needs pip install 'filedrawer[pii-model]'; CPU, nothing leaves the machine)")
    p.add_argument("--arm-column", help="treatment-arm column when the QSF has no randomizer embedded data")
    p.add_argument("--no-silicon", action="store_true", help=argparse.SUPPRESS)   # silicon stage removed; kept so old run.sh scripts still work
    p.add_argument("--allow-outcome-change", action="store_true",
                   help="proceed when the analysed outcome is a different measure from the plan's (flagged in the report)")
    p.add_argument("--extensions", action="store_true", default=None,
                   help="propose three follow-up experiments (mechanism, boundary, alternative) as buildable survey designs (~$0.15-0.30)")
    p.add_argument("--no-lit", action="store_true")
    p.add_argument("--no-exploratory", action="store_true")
    p.add_argument("--fixtures", help="mock fixtures directory (mock provider)")
    p.add_argument("--openalex-fixture", help="JSON fixture served instead of OpenAlex (offline)")
    p.add_argument("--repo-url", default="https://github.com/yrvelez/filedrawer")
    p.add_argument("--branch", default="main")


def _flags(a) -> dict:
    return {"review": a.review, "advanced_prompts": a.advanced_prompts, "review_revise": a.review_revise, "no_revise": getattr(a, "no_revise", False),
            "allow_pii": a.allow_pii, "pii_model": getattr(a, "pii_model", False),
            "pii_keep": [c.strip() for c in a.pii_keep.split(",") if c.strip()], "arm_column": a.arm_column,
            "allow_outcome_change": a.allow_outcome_change,
            "no_lit": a.no_lit, "no_exploratory": a.no_exploratory, "extensions": getattr(a, "extensions", False),
            "fixtures": a.fixtures, "openalex_fixture": a.openalex_fixture, "synthetic": getattr(a, "synthetic", False)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="filedrawer", description="A file drawer for AI-generated research reports.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cf = sub.add_parser("configure", help="choose models, review, follow-ups, PII model and literature search for a study "
                                          "(writes filedrawer.setup.yaml; run.sh needs it)")
    cf.add_argument("study", nargs="?", default=".")
    cf.add_argument("--questions", action="store_true", help="print the setup questions as JSON (for agents) and exit")
    cf.add_argument("--show", action="store_true", help="print the current setup and exit")
    cf.add_argument("--yes", action="store_true", help="no prompts: unanswered questions take the recommended default")
    cf.add_argument("--where", choices=["hosted", "local"])
    cf.add_argument("--models", choices=["sonnet+haiku", "sonnet", "custom"])
    cf.add_argument("--strong-model"); cf.add_argument("--fast-model")
    cf.add_argument("--local-model"); cf.add_argument("--local-url")
    cf.add_argument("--outside-review", choices=["none", "coarse", "refine", "openreview"])
    cf.add_argument("--extensions", choices=["yes", "no"])
    cf.add_argument("--pii", choices=["rules", "rules+model"])
    cf.add_argument("--literature", choices=["on", "off"])
    pc = sub.add_parser("pii", help="show which columns of a CSV would be dropped as PII, and why (nothing is written)")
    pc.add_argument("csv"); pc.add_argument("--qsf", help="the survey's QSF (marks open-ended questions)")
    pc.add_argument("--model", action="store_true", help="also run the local PII model on the kept text columns")
    r = sub.add_parser("run", help="run the pipeline on a survey export + QSF + PAP")
    r.add_argument("--csv", required=True); r.add_argument("--qsf"); r.add_argument("--pap", required=True)
    r.add_argument("--pap-json", help="author-supplied structured plan (pap.json schema); skips the papreader agent")
    r.add_argument("--slug", required=True); r.add_argument("--title", required=True)
    r.add_argument("--authors", default="", help="comma-separated")
    r.add_argument("--synthetic", action="store_true", help="label the package as synthetic/demo data")
    r.add_argument("--package-dir", help="write the package to this exact directory (e.g. '.' for a standalone study repo) instead of <out>/<slug>")
    _common(r)

    ini = sub.add_parser("init-study", help="scaffold a standalone study repository (inputs/, README, run script, gitignore)")
    ini.add_argument("dir", help="directory to scaffold (e.g. . or ../ai-discernment)")
    ini.add_argument("--slug", required=True); ini.add_argument("--title", required=True)
    ini.add_argument("--authors", default=""); ini.add_argument("--repo-url", default="")
    ini.add_argument("--design", default="survey_experiment", choices=["survey_experiment", "methods_comparison"],
                     help="methods_comparison: a hand-built package comparing methods against a benchmark (no arms, "
                          "no pipeline run); writes study.json and report.md stubs instead of run.sh")

    d = sub.add_parser("demo", help="generate the synthetic demo and run the pipeline on it")
    _common(d)

    am = sub.add_parser("agents-md", help="(re)write a package's AGENTS.md: how to reanalyze it first, the deposit routine after; no model calls")
    am.add_argument("study")
    rp = sub.add_parser("reproduce", help="re-run scripts (02-04, or study.json's reproduce.scripts) and verify outputs are byte-identical")
    rp.add_argument("study")

    at = sub.add_parser("attest", help="record a human review step (flips provenance to human_reviewed)")
    at.add_argument("study"); at.add_argument("--step", required=True); at.add_argument("--by", required=True)
    at.add_argument("--reviewer-pass", action="store_true")

    rl = sub.add_parser("release", help="re-check PII, mark the study released and archive it on Zenodo with a DOI (--no-doi to skip)")
    rl.add_argument("--no-doi", dest="doi", action="store_false", help="release without archiving on Zenodo (default: mint or update the DOI)")
    rl.add_argument("--doi", dest="doi", action="store_true", help=argparse.SUPPRESS)
    rl.add_argument("--new-version", action="store_true", help="archive a new Zenodo version even if the package is unchanged")
    rl.add_argument("--sandbox", action="store_true", help="use sandbox.zenodo.org (test DOIs that do not resolve; citation files untouched)")
    rl.add_argument("--community", default=None, help="Zenodo community to submit to, e.g. filedrawer")
    rl.add_argument("--include-data", action="store_true",
                    help="also archive data/ on Zenodo (only files already committed to the package's git repository; "
                         "a published Zenodo record cannot be deleted). Default: data/ never leaves this machine")
    rl.add_argument("--yes", action="store_true", help="publish without asking (a published Zenodo record cannot be deleted)")
    rl.add_argument("--site", default="https://filedrawer.org", help="the repository site the record links back to")
    rl.add_argument("study"); rl.add_argument("--force", action="store_true")

    bi = sub.add_parser("build-index", help="build docs/index.json for the dashboard")
    bi.add_argument("--studies", default=str(REPO_ROOT / "studies"))
    bi.add_argument("--registry", default=str(REPO_ROOT / "docs" / "registry.json"))
    bi.add_argument("--out", default=str(REPO_ROOT / "docs"), help="output file or directory (-> index.json)")
    bi.add_argument("--include-drafts", action="store_true")
    bi.add_argument("--repo-url", default="https://github.com/yrvelez/filedrawer")
    bi.add_argument("--branch", default="main")

    sv = sub.add_parser("serve", help="serve the dashboard locally (same server as Fly)")
    sv.add_argument("--port", type=int, default=8080)

    cr = sub.add_parser("compare-results", help="compare two results/ folders: registered estimates must match (within --tol) before a rerun is filed")
    cr.add_argument("old"); cr.add_argument("new"); cr.add_argument("--tol", type=float, default=1e-6)
    hv = sub.add_parser("harvest", help="print the slim record the dashboard would build for a public GitHub URL")
    hv.add_argument("--compare", metavar="RECORD_URL", help="diff against a live record (…/records/<id>.json) and exit 1 if the paper would not be report.md")
    hv.add_argument("url"); hv.add_argument("--llm", action="store_true", help="allow a zero-retention model call to fill gaps (needs OPENROUTER_API_KEY)")

    ad = sub.add_parser("address", help="answer the reviewer's analytical issues with approved robustness addenda, re-estimate, re-write, re-review")
    ad.add_argument("study")
    ad.add_argument("--issues", default="", help="comma-separated issue ids from review.json (default: all analytical high/medium)")
    ad.add_argument("--yes", action="store_true", help="approve every proposal without asking (recorded as unattended)")
    ad.add_argument("--dry-run", action="store_true", help="only write proposals to provenance/addenda_proposed.json")
    ad.add_argument("--by", default="", help="who approves (recorded as a human review step; flips provenance to human reviewed)")
    ad.add_argument("--provider", choices=["openrouter", "local", "mock"]); ad.add_argument("--config"); ad.add_argument("--fixtures")

    ex = sub.add_parser("extensions", help="propose follow-up experiments for a finished package (writes extensions/, re-renders the report)")
    ex.add_argument("study"); ex.add_argument("--provider", choices=["openrouter", "local", "mock"]); ex.add_argument("--config"); ex.add_argument("--fixtures")
    ex.add_argument("--revise", nargs=2, metavar=("ID", "REQUEST"), help='revise one proposal, e.g. --revise alternative "add a confidence item after each post"')

    eq = sub.add_parser("extension-qsf", help="(re)write extensions/<id>.qsf from the saved designs; no model calls, no Qualtrics account")
    eq.add_argument("study")

    be = sub.add_parser("build-extension", help="build one proposed extension as an UNPUBLISHED Qualtrics draft via autoexperiment (local; never activates)")
    be.add_argument("study"); be.add_argument("id"); be.add_argument("--dry-run", action="store_true", help="only run autoexperiment's validator")
    be.add_argument("--config")

    ro = sub.add_parser("review-orchestrate", help="re-run the checking agent on an existing review.json (claim checks, dispositions, "
                        "editorial guidance, unresolved questions) and re-render the report; one strong-model call")
    ro.add_argument("study")
    ro.add_argument("--signoff", action="store_true", help="only re-check the corrected report's claims (the sign-off), not the full checking pass")
    ro.add_argument("--provider", choices=["openrouter", "local", "mock"]); ro.add_argument("--config"); ro.add_argument("--fixtures")
    ri = sub.add_parser("review-import", help="add an external referee report (refine, or a coarse run done separately) to review.json")
    ri.add_argument("study"); ri.add_argument("source", choices=["refine", "coarse", "openreview"])
    ri.add_argument("file", help="the review as PDF, Markdown or text")
    ri.add_argument("--provider", choices=["openrouter", "local", "mock"]); ri.add_argument("--config"); ri.add_argument("--fixtures")

    pb = sub.add_parser("publish", help="scan, commit and push a package to your own GitHub repository (creates it with gh; private unless --public)")
    pb.add_argument("study"); pb.add_argument("--repo", help="repository name for a new one, e.g. my-study or owner/my-study (default: the folder name)")
    pb.add_argument("--public", action="store_true", help="create the repository public (asks first unless --yes)")
    pb.add_argument("-m", "--message", default="Study package", help="commit message")
    pb.add_argument("--ack", action="append", default=[], help='acknowledge a contact detail by its key, e.g. "codebook.md: phone number"')
    pb.add_argument("--yes", action="store_true", help="do not ask")
    pb.add_argument("--submit", action="store_true", help="then list it on the File Drawer (public repositories only)")
    pb.add_argument("--server", default="https://filedrawer.org")

    sm = sub.add_parser("submit", help="list a self-run package from your public GitHub repository (a package folder, or its GitHub URL)")
    sm.add_argument("url", metavar="package_or_url", help="a study package folder in a pushed GitHub repository, or its GitHub URL")
    sm.add_argument("--server", default="https://filedrawer.org", help="default https://filedrawer.org")
    sm.add_argument("--yes", action="store_true", help="do not ask for confirmation (contacts still need --ack)")
    sm.add_argument("--dry-run", action="store_true", help="run the self-check and the scan and show the manifest; send nothing")
    sm.add_argument("--ack", action="append", default=[], help='acknowledge a contact detail by its key, e.g. "codebook.md: phone number"')
    sm.add_argument("--skip-selfcheck", action="store_true", help="skip the offline canary test (code identity is still shown)")

    a = ap.parse_args(argv)
    if a.cmd == "configure":
        from . import configure as CF
        if a.questions:
            print(CF.questions_json())
            return 0
        if a.show:
            cur = CF.load(Path(a.study))
            print(CF.describe(cur) if cur else f"no {CF.SETUP_FILE} in {a.study}: run filedrawer configure {a.study}")
            return 0 if cur else 1
        given = {"where": a.where, "models": a.models, "strong_model": a.strong_model, "fast_model": a.fast_model,
                 "local_model": a.local_model, "local_url": a.local_url, "outside_review": a.outside_review,
                 "extensions": a.extensions, "pii": a.pii, "literature": a.literature}
        return CF.run(Path(a.study), given, a.yes)
    if a.cmd == "pii":
        from . import pii as PII
        from . import tidy as TIDY
        free, closed = [], []
        if a.qsf:
            from .qsf import parse_qsf
            cb = parse_qsf(a.qsf)
            free, closed = cb.free_text_columns(), cb.closed_columns()
        rep = PII.scan(a.csv, free_text_columns=free, closed_columns=closed)
        if a.model:
            from . import pii_model
            df, _ = TIDY.read_export(a.csv)
            rep.flagged.update(pii_model.scan_frame(df, exclude=set(rep.flagged)))
        print(PII.summary_line(rep))
        for col, why in rep.flagged.items():
            print(f"  drop  {col}  ({why})")
        if not rep.flagged:
            print("  no columns flagged")
        return 0
    if a.cmd == "build-index":
        from . import index
        argv2 = ["--studies", a.studies, "--registry", a.registry, "--out", a.out, "--repo-url", a.repo_url, "--branch", a.branch]
        if a.include_drafts:
            argv2.append("--include-drafts")
        return index.main(argv2) or 0
    if a.cmd == "init-study":
        from .scaffold import init_study
        print(init_study(Path(a.dir), a.slug, a.title, a.authors, a.repo_url, a.design))
        return 0
    if a.cmd == "harvest":
        from . import harvest
        rec = harvest.harvest(a.url, use_llm=a.llm)
        rec.pop("_paper_text", None)
        figs = rec.pop("_figures", None) or {}
        rec["figures_captured"] = sorted(figs)
        if getattr(a, "compare", None):
            import urllib.request
            with urllib.request.urlopen(a.compare, timeout=30) as r:
                live = json.loads(r.read().decode("utf-8"))
            diff = harvest.compare_records(live, rec)
            print(json.dumps(diff, indent=1, ensure_ascii=False))
            bad = (rec.get("paper") or {}).get("source") != "report.md" and rec.get("kind") == "filedrawer_package"
            if bad:
                print("REFUSE: the new harvest would file something other than report.md as the paper", file=sys.stderr)
            return 1 if bad else 0
        print(json.dumps(rec, indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "compare-results":
        from .analysis.compare import compare_results
        out = compare_results(Path(a.old), Path(a.new), tol=a.tol)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out["ok"] else 1
    if a.cmd == "publish":
        from .publish import publish
        code, url = publish(a.study, repo=a.repo, public=a.public, message=a.message, acknowledge=a.ack, yes=a.yes)
        if code == 0 and url:
            print(f"Package at {url}")
            if a.submit:
                if not a.public:
                    print("Not submitted: the repository is private. Make it public, then run filedrawer submit.")
                    return 0
                from .submit import submit_package
                return submit_package(a.study, a.server, yes=a.yes, acknowledge=a.ack)
        return code
    if a.cmd == "submit" and not a.url.startswith(("http://", "https://")):
        from .submit import submit_package
        return submit_package(a.url, a.server, yes=a.yes, dry_run=a.dry_run, acknowledge=a.ack,
                              run_selfcheck=not a.skip_selfcheck)
    if a.cmd == "submit":
        import urllib.request
        req = urllib.request.Request(a.server.rstrip("/") + "/submit", data=json.dumps({"repo_url": a.url}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            out = json.loads(e.read().decode() or "{}")
        print(json.dumps(out, indent=1))
        return 0 if out.get("ok") else 1
    if a.cmd == "serve":
        import os, runpy
        os.environ["PORT"] = str(a.port)
        sys.argv = ["app.py"]
        runpy.run_path(str(REPO_ROOT / "server" / "app.py"), run_name="__main__")
        return 0
    if a.cmd == "extensions":
        from .address import load_context
        from .orchestrator import run_extensions, finalize_package
        cfg = load_config(a.config, _overrides(a))
        ctx = load_context(Path(a.study).resolve(), cfg, fixtures=a.fixtures)
        if a.revise:
            from . import extensions as X
            from .agents import extensions as EXT_AGENT
            ext_id, request = a.revise
            f = Path(a.study).resolve() / "extensions" / f"{ext_id}.json"
            if not f.exists():
                raise SystemExit(f"no extension {ext_id!r}; have: {', '.join(r['id'] for r in X.load_index(Path(a.study).resolve()))}")
            prop, validator, errs = EXT_AGENT.revise(ctx, json.loads(f.read_text(encoding="utf-8")), request)
            if prop is None:
                print("Revision failed validation; the current design is unchanged:\n- " + "\n- ".join(errs[:10]))
                return 1
            rows = X.replace(Path(a.study).resolve(), prop, validator, request, ctx["meta"].get("title", ""))
            ctx["extensions"] = rows
            finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
            print(json.dumps([{k: r.get(k) for k in ("id", "kind", "title", "status")} for r in rows], indent=1, ensure_ascii=False))
            return 0
        rows = run_extensions(ctx)
        finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
        print(json.dumps([{k: r.get(k) for k in ("id", "kind", "title", "status")} for r in rows], indent=1, ensure_ascii=False))
        return 0 if rows else 1
    if a.cmd == "extension-qsf":
        from . import extensions as X
        study = Path(a.study).resolve()
        rows = X.load_index(study)
        props = [json.loads((study / "extensions" / f"{r['id']}.json").read_text(encoding="utf-8")) for r in rows]
        sj = json.loads((study / "study.json").read_text(encoding="utf-8")) if (study / "study.json").exists() else {}
        res = X.write_qsfs(study, props, sj.get("title", ""))
        for r in rows:
            r.update(res.get(r["id"], {}))
        (study / "extensions" / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: {"qsf": v["qsf"], "errors": v["qsf_errors"], "media_to_supply": len(v["media"])} for k, v in res.items()}, indent=1))
        return 0 if all(v["qsf"] for v in res.values()) else 1
    if a.cmd == "build-extension":
        from . import extensions as X
        cfg = load_config(a.config, {})
        out = X.build(Path(a.study), a.id, cfg, dry_run=a.dry_run)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        if out.get("built"):
            from .address import load_context
            from .orchestrator import finalize_package
            study = Path(a.study).resolve()
            if (study / "provenance" / "ctx_snapshot.json").exists():
                ctx = load_context(study, cfg)
                ctx["extensions"] = X.load_index(study)
                finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
            print(f"\nUnpublished Qualtrics draft (your account only): {out.get('surveyUrl')}\nThe report now marks {a.id} as built; commit and push to update the listing.")
        return 0 if out.get("ok") else 1
    if a.cmd == "address":
        from .address import run_address
        cfg = load_config(a.config, _overrides(a))
        out = run_address(Path(a.study), cfg, issues=[x for x in a.issues.split(",") if x.strip()] or None,
                          yes=a.yes, dry_run=a.dry_run, by=a.by, fixtures=a.fixtures)
        print(json.dumps({"ran": out["ran"], "addenda": [x.get("id") for x in out["addenda"]]}, indent=1))
        return 0
    if a.cmd == "review-orchestrate":
        from .address import load_context
        from .orchestrator import finalize_package
        from .review import synthesize, write_review
        cfg = load_config(a.config, _overrides(a))
        study = Path(a.study).resolve()
        ctx = load_context(study, cfg, a.fixtures)
        rv = json.loads((study / "review.json").read_text(encoding="utf-8"))
        if a.signoff:                               # only re-check the corrected text against the tables
            from .review import signoff
            so = signoff(ctx, rv)
            if not so:
                print("the sign-off returned nothing usable; review.json unchanged", file=sys.stderr)
                return 1
            finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
            print(json.dumps({"claims": [c.get("verdict") for c in so["claims"]], "decision": so.get("decision")}, indent=1))
            return 0
        synthesize(ctx, rv)
        if not rv.get("synthesis"):
            print("the checking agent returned nothing usable; review.json left without a synthesis", file=sys.stderr)
        write_review(study, rv)
        finalize_package(ctx, ctx["sections"], package_dir=bool(ctx["meta"].get("package_at_root")), accumulate=True)
        syn = rv.get("synthesis") or {}
        print(json.dumps({"dispositions": {i["id"]: i.get("disposition") for i in rv["issues"]},
                          "claims": [c.get("verdict") for c in syn.get("claims") or []],
                          "unresolved": [u["id"] for u in syn.get("unresolved") or []]}, indent=1))
        return 0
    if a.cmd == "review-import":
        from .address import load_context
        from .review import import_review
        from .review.external import read_review_file
        cfg = load_config(a.config, _overrides(a))
        study = Path(a.study).resolve()
        rv = import_review(load_context(study, cfg, a.fixtures), a.source, read_review_file(Path(a.file)))
        prov_path = study / "provenance" / "provenance.json"
        if prov_path.exists():
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
            prov.update(review_mode=rv["mode"], reviewer_pass=True)
            prov_path.write_text(json.dumps(prov, indent=1, ensure_ascii=False), encoding="utf-8")
        n = sum(1 for i in rv["issues"] if i.get("source") == a.source)
        print(f"{n} {a.source} issue(s) added to review.json; answer analytical ones with `filedrawer address {a.study}`.")
        return 0
    if a.cmd == "agents-md":
        from . import agents_md
        print(f"wrote {agents_md.write(Path(a.study).resolve())}")
        return 0
    if a.cmd == "reproduce":
        from .release import reproduce
        out = reproduce(Path(a.study))
        print(json.dumps(out, indent=1))
        return 0 if out["ok"] else 1
    if a.cmd == "attest":
        from .release import attest
        print(json.dumps(attest(Path(a.study), a.step, a.by, a.reviewer_pass), indent=1))
        return 0
    if a.cmd == "release":
        from .release import release
        sj = release(Path(a.study), force=a.force)
        print(f"released {sj['slug']} ({sj['released']}). Rebuild the index: filedrawer build-index")
        if a.doi:
            from .zenodo import mint, token
            try:
                token(a.sandbox)
            except SystemExit as e:              # no token: the release stands, the DOI waits
                print(f"No DOI minted: {e}\nRelease again once the token is set, or pass --no-doi to silence this.")
                return 0
            out = mint(Path(a.study), sandbox=a.sandbox, community=a.community, yes=a.yes, site=a.site, new_version=a.new_version,
                       include_data=a.include_data)
            print(json.dumps(out, indent=1))
            if out["published"] and not a.sandbox and not out.get("unchanged"):
                print("Commit and push the package (zenodo.json, CITATION.cff, study.json, report.md), then refresh the listing.")
        return 0

    overrides = _overrides(a)
    cfg = load_config(a.config, overrides)
    from .orchestrator import run_pipeline
    if a.cmd == "demo":
        sys.path.insert(0, str(REPO_ROOT))
        from demo.make_demo import generate  # repo-relative import
        tmp = REPO_ROOT / "runs" / "demo_inputs"
        tmp.mkdir(parents=True, exist_ok=True)
        csv_path = generate(tmp / "demo_raw.csv")
        inputs = {"csv": str(csv_path), "qsf": str(REPO_ROOT / "demo" / "demo.qsf"), "pap": str(REPO_ROOT / "demo" / "pap.md"),
                  "slug": "demo-immigration-framing", "title": "Economic-contribution framing and immigration attitudes (synthetic demo)",
                  "authors": ["filedrawer demo"], "out_dir": a.out, "repo_url": a.repo_url, "branch": a.branch}
        flags = _flags(a)
        flags["synthetic"] = True
        if cfg["provider"] == "mock" and not flags.get("openalex_fixture"):
            flags["openalex_fixture"] = str(REPO_ROOT / "tests" / "fixtures" / "mock" / "openalex.json")
    else:
        inputs = {"csv": a.csv, "qsf": a.qsf, "pap": a.pap, "slug": a.slug, "title": a.title,
                  "authors": [x.strip() for x in a.authors.split(",") if x.strip()], "out_dir": a.out,
                  "repo_url": a.repo_url, "branch": a.branch, "package_dir": a.package_dir, "pap_json": a.pap_json}
        flags = _flags(a)
    if flags.get("extensions") is None:              # not given on the command line: the study's setup decides
        flags["extensions"] = ((cfg.get("setup") or {}).get("extensions") == "yes")
    study = run_pipeline(inputs, cfg, flags)
    print(f"\nStudy package written to {study}\n  report: {study / 'report.md'}\n  next: filedrawer attest/release, then filedrawer build-index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
