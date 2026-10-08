"""`filedrawer configure`: the author's setup choices, asked once per study and written to filedrawer.setup.yaml.

The questions live here as data (`QUESTIONS`) so a coding agent can show them in its own question UI
(`filedrawer configure --questions` prints them as JSON) and pass the answers back as flags with `--yes`; a person
running the command in a terminal is asked interactively. `filedrawer run` reads filedrawer.setup.yaml from the study
folder, and the scaffolded run.sh refuses to start without it, so no one runs on defaults they never saw.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import yaml

SETUP_FILE = "filedrawer.setup.yaml"
HOSTED_PAIRS = {
    "sonnet+haiku": ("anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"),
    "sonnet": ("anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"),
}
LOCAL_DEFAULT = {"model": "qwen/qwen3.8-27b", "url": "http://localhost:1234/v1"}

QUESTIONS = [
    {"id": "where", "question": "Where should the models run?", "default": "hosted", "options": [
        {"value": "hosted", "label": "Hosted, via OpenRouter",
         "detail": "Fastest (about 5 minutes per paper). Roughly $0.40-1.50 per paper with the recommended models. "
                   "Needs your own OpenRouter key; requests are sent with zero data retention. Only the survey schema, "
                   "the plan and aggregate results are sent, never respondent rows."},
        {"value": "local", "label": "Local, on this machine",
         "detail": "Free and nothing leaves the machine, but slow (hours per paper on a 27B model). Needs about 32 GB "
                   "of memory and a local model server such as LM Studio, llama.cpp or Ollama."}]},
    {"id": "models", "question": "Which hosted models?", "default": "sonnet+haiku", "when": {"where": "hosted"}, "options": [
        {"value": "sonnet+haiku", "label": "Claude Sonnet 5.5 + Claude Haiku 5.5",
         "detail": "Sonnet reads the plan, writes and reviews; Haiku writes code and searches the literature. The tested default."},
        {"value": "sonnet", "label": "Claude Sonnet 5.5 for everything", "detail": "Higher quality on code-heavy studies; costs more."},
        {"value": "custom", "label": "Other OpenRouter models",
         "detail": "Give --strong-model and --fast-model (OpenRouter slugs). Untested models may need prompt tuning."}]},
    {"id": "local_model", "question": "Which local model and server?", "default": LOCAL_DEFAULT["model"], "when": {"where": "local"},
     "free_text": True, "detail": f"Model name as your server lists it, and the server URL (--local-url, default {LOCAL_DEFAULT['url']}). "
                                  "Tested: qwen/qwen3.8-27b in LM Studio."},
    {"id": "outside_review", "question": "Add an outside review after the run? The Light Pass (every reported number checked "
                                         "against the tables, every analysis against your plan) always runs.",
     "default": "none", "options": [
        {"value": "none", "label": "Not now", "detail": "You can add one at any time later."},
        {"value": "coarse", "label": "Coarse", "detail": "Open-source reviewer you run on your machine (about $1-2): "
                                                         "uvx coarse-ink review report.md, then filedrawer review-import . coarse FILE."},
        {"value": "refine", "label": "Refine", "detail": "Upload report.md at refine.ink, then filedrawer review-import . refine FILE."},
        {"value": "openreview", "label": "OpenReview or any referee report",
         "detail": "Import a review posted on OpenReview, or any other referee report: filedrawer review-import . openreview FILE."}]},
    {"id": "extensions", "question": "Propose follow-up studies?", "default": "yes", "options": [
        {"value": "yes", "label": "Yes", "detail": "Two or three designs aimed at what the study left open, each with a "
                                                   "diagram and an importable Qualtrics file. A few cents."},
        {"value": "no", "label": "No", "detail": ""}]},
    {"id": "pii", "question": "How should identifiers be found?", "default": "rules", "options": [
        {"value": "rules", "label": "Rules", "detail": "Known id columns, Prolific / MTurk / CloudResearch / Qualtrics ids and IP "
                                                       "addresses by value, contact details, open-ended answers. Always on."},
        {"value": "rules+model", "label": "Rules + a local model",
         "detail": "Also reads the kept text columns with a small model on your CPU, which catches e.g. a column of names under "
                   "an innocent header. Needs pip install 'filedrawer[pii-model]' (about 2 GB). Nothing leaves the machine."}]},
    {"id": "literature", "question": "Search the published literature for related work?", "default": "on", "options": [
        {"value": "on", "label": "On", "detail": "OpenAlex; only the study's title, keywords and hypotheses are sent."},
        {"value": "off", "label": "Off", "detail": ""}]},
]
DEFAULTS = {q["id"]: q["default"] for q in QUESTIONS}


def _applies(q: dict, answers: dict) -> bool:
    return all(answers.get(k) == v for k, v in (q.get("when") or {}).items())


def validate(answers: dict) -> list[str]:
    errs = []
    for q in QUESTIONS:
        if not _applies(q, answers) or q.get("free_text"):
            continue
        ok = {o["value"] for o in q["options"]}
        if answers.get(q["id"]) not in ok:
            errs.append(f"{q['id']}: one of {sorted(ok)}")
    if answers.get("where") == "hosted" and answers.get("models") == "custom" and not (answers.get("strong_model") and answers.get("fast_model")):
        errs.append("models=custom needs --strong-model and --fast-model")
    return errs


def to_config(answers: dict) -> dict:
    """The config the pipeline reads: provider and models, PII model, literature search, plus the answers themselves."""
    cfg: dict = {"setup": dict(answers)}
    if answers["where"] == "local":
        model = answers.get("local_model") or LOCAL_DEFAULT["model"]
        cfg["provider"] = "local"
        cfg["local"] = {"base_url": answers.get("local_url") or LOCAL_DEFAULT["url"], "models": {"strong": model, "fast": model}}
    else:
        strong, fast = HOSTED_PAIRS.get(answers.get("models"), (answers.get("strong_model"), answers.get("fast_model")))
        cfg["provider"] = "openrouter"
        cfg["models"] = {"strong": strong, "fast": fast}
    cfg["pii"] = {"model": answers.get("pii") == "rules+model"}
    cfg["litreview"] = {"enabled": answers.get("literature") != "off"}
    return cfg


def ask_interactively(answers: dict) -> dict:
    """Fill in whatever was not given on the command line by asking in the terminal (defaults on Enter)."""
    for q in QUESTIONS:
        if q["id"] in answers or not _applies(q, answers):
            continue
        print(f"\n{q['question']}")
        if q.get("free_text"):
            print(f"  {q['detail']}")
            got = input(f"  [{q['default']}]: ").strip()
            answers[q["id"]] = got or q["default"]
            if answers.get("where") == "local":
                url = input(f"  server URL [{LOCAL_DEFAULT['url']}]: ").strip()
                answers["local_url"] = url or LOCAL_DEFAULT["url"]
            continue
        for i, o in enumerate(q["options"], 1):
            mark = " (recommended)" if o["value"] == q["default"] else ""
            print(f"  {i}. {o['label']}{mark}" + (f"\n     {o['detail']}" if o["detail"] else ""))
        while True:
            got = input(f"  choose 1-{len(q['options'])} [default {q['default']}]: ").strip()
            if not got:
                answers[q["id"]] = q["default"]
                break
            if got.isdigit() and 1 <= int(got) <= len(q["options"]):
                answers[q["id"]] = q["options"][int(got) - 1]["value"]
                break
        if q["id"] == "models" and answers["models"] == "custom":
            answers["strong_model"] = input("  strong model (OpenRouter slug): ").strip()
            answers["fast_model"] = input("  fast model (OpenRouter slug): ").strip()
    return answers


def write(study: Path, answers: dict) -> Path:
    cfg = to_config(answers)
    p = Path(study) / SETUP_FILE
    head = (f"# Written by `filedrawer configure` on {dt.date.today().isoformat()}. `filedrawer run` reads it from this folder;\n"
            "# re-run `filedrawer configure` to change it. Hand edits are fine.\n")
    p.write_text(head + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def load(study: Path) -> dict | None:
    p = Path(study) / SETUP_FILE
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def describe(cfg: dict) -> str:
    a = cfg.get("setup") or {}
    if cfg.get("provider") == "local":
        models = f"local: {cfg['local']['models']['strong']} at {cfg['local']['base_url']}"
    else:
        models = f"hosted: {cfg['models']['strong']} (plan, writing, review) + {cfg['models']['fast']} (code, literature)"
    review = "Light Pass" + ("" if a.get("outside_review", "none") == "none" else f", then {a['outside_review']} (import after the run)")
    return "\n".join([
        f"models      {models}",
        f"review      {review}",
        f"follow-ups  {'yes' if a.get('extensions') == 'yes' else 'no'}",
        f"PII         {'rules + local model' if (cfg.get('pii') or {}).get('model') else 'rules'}",
        f"literature  {'on' if (cfg.get('litreview') or {}).get('enabled', True) else 'off'}",
    ])


def questions_json() -> str:
    return json.dumps({"file": SETUP_FILE, "questions": QUESTIONS,
                       "answer_flags": "filedrawer configure . --yes --where hosted|local --models sonnet+haiku|sonnet|custom "
                                       "[--strong-model S --fast-model F] [--local-model M --local-url U] "
                                       "--outside-review none|coarse|refine|openreview --extensions yes|no "
                                       "--pii rules|rules+model --literature on|off"}, indent=1)


def run(study: Path, given: dict, yes: bool) -> int:
    answers = {k: v for k, v in given.items() if v is not None}
    if yes:
        for q in QUESTIONS:                          # unanswered questions take the recommended default
            if q["id"] not in answers and _applies(q, {**DEFAULTS, **answers}):
                answers[q["id"]] = q["default"]
    elif sys.stdin.isatty():
        answers = ask_interactively(answers)
    else:
        print("filedrawer configure needs answers: run it in a terminal, or pass the choices with --yes "
              "(see `filedrawer configure --questions`).", file=sys.stderr)
        return 2
    errs = validate(answers)
    if errs:
        print("Not written: " + "; ".join(errs), file=sys.stderr)
        return 2
    p = write(study, answers)
    print(f"wrote {p}\n" + describe(to_config(answers)))
    return 0
