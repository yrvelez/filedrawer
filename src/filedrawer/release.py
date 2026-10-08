"""attest (record human review steps), release (PII re-check + flip status), reproduce (byte-identical check)."""
from __future__ import annotations

import datetime as dt
import filecmp
import json
import re
import shutil
import tempfile
from pathlib import Path

from . import pii as PII
from . import exec as _exec


def _load(study: Path) -> dict:
    return json.loads((study / "study.json").read_text(encoding="utf-8"))


def _save(study: Path, sj: dict) -> None:
    from .badges import badges_for, write_badges, markdown_row
    sj["badges"] = badges_for(sj)
    (study / "study.json").write_text(json.dumps(sj, indent=1, ensure_ascii=False), encoding="utf-8")
    prov_path = study / "provenance" / "provenance.json"
    if prov_path.exists():
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
        prov.update({k: sj["provenance"][k] for k in ("mode", "human_steps", "reviewer_pass")})
        prov_path.write_text(json.dumps(prov, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    # refresh the badges (SVG files + the row in the report) and the colophon line
    rp = study / "report.md"
    if rp.exists():
        from .package import BADGES
        text = rp.read_text(encoding="utf-8")
        if "<!-- fd:badges -->" in text:
            row = markdown_row(write_badges(study, sj))
            text = re.sub(r"(<!-- fd:badges -->\n)[^\n]*", lambda m: m.group(1) + row, text, count=1)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("> **Provenance:"):
                tail = line.split(".** ", 1)[1] if ".** " in line else ""
                tail = tail.replace("Human steps recorded: " + str(max(0, len(sj["provenance"]["human_steps"]) - 1)),
                                    "Human steps recorded: " + str(len(sj["provenance"]["human_steps"])))
                for status in ("draft", "released"):
                    tail = tail.replace(f"Release status: **{status}**", f"Release status: **{sj['release_status']}**")
                lines[i] = f"> **Provenance: {BADGES[sj['provenance']['mode']]}.** " + tail
                break
        rp.write_text("\n".join(lines) + "\n", encoding="utf-8")


def attest(study: Path, step: str, by: str, reviewer_pass: bool = False) -> dict:
    sj = _load(study)
    sj["provenance"]["human_steps"].append({"step": step, "by": by, "date": dt.date.today().isoformat()})
    sj["provenance"]["mode"] = "human_reviewed"
    if reviewer_pass:
        sj["provenance"]["reviewer_pass"] = True
    _save(study, sj)
    return sj["provenance"]


def release(study: Path, force: bool = False) -> dict:
    problems = []
    for f in sorted((study / "data").rglob("*.csv")):
        flagged = PII.assert_no_pii(f)
        if flagged:
            problems.append(f"{f.relative_to(study / 'data')}: {flagged}")
    if problems and not force:
        raise SystemExit("Release blocked: identifier-like columns remain in data files:\n  " + "\n  ".join(problems)
                         + "\nRemove them (re-run without --allow-pii) or pass --force after a manual review.")
    sj = _load(study)
    sj["release_status"] = "released"
    sj["released"] = dt.date.today().isoformat()
    if problems:
        sj["provenance"].setdefault("degraded", []).append("released with --force despite PII flags: " + "; ".join(problems))
    _save(study, sj)
    return sj


def _declared(study: Path) -> dict | None:
    """The `reproduce` block of a hand-built package's study.json: {"scripts": [...], "outputs": [globs]}."""
    try:
        spec = _load(study).get("reproduce")
    except (OSError, ValueError):
        return None
    return spec if isinstance(spec, dict) and spec.get("scripts") else None


def reproduce(study: Path, timeout_s: int = 600) -> dict:
    """Re-run the analysis in a copy and compare its outputs byte for byte.

    Pipeline packages re-run scripts 02-04 and compare every results/*.csv. Hand-built packages
    (e.g. design type methods_comparison) declare their own scripts and output globs in
    study.json under "reproduce"; R scripts run with Rscript."""
    study = Path(study).resolve()             # "." has an empty .name, which made work == tmp
    spec = _declared(study)
    tmp = Path(tempfile.mkdtemp(prefix="fd-repro-"))
    work = tmp / (study.name or "study")
    shutil.copytree(study, work, ignore=shutil.ignore_patterns("provenance", ".mplconfig", ".git", "inputs", "__pycache__"))
    if spec:
        scripts = [str(x) for x in spec["scripts"]]
        globs = [str(g) for g in spec.get("outputs") or ["results/*.csv"]]
        language = "auto"
    else:
        scripts = [f"scripts/{s.name}" for s in sorted((work / "scripts").glob("0[2-4]_*.py"))]
        globs = ["results/*.csv"]
        language = "python"
    targets = sorted({p.relative_to(study) for g in globs for p in study.glob(g)
                      if p.is_file() and p.name != "analysis_tags.csv"})
    for rel in targets:
        (work / rel).unlink(missing_ok=True)
    outputs = {"scripts": [], "identical": [], "different": [], "missing": []}
    for rel_script in scripts:
        r = _exec.run_script(work, rel_script, timeout_s, language)
        outputs["scripts"].append({"script": rel_script, "exit_code": r["exit_code"], "stderr": r["stderr_tail"][-500:]})
        if r["exit_code"] != 0:
            break
    for rel in targets:
        a, b = study / rel, work / rel
        if not b.exists():
            outputs["missing"].append(str(rel))
        elif filecmp.cmp(a, b, shallow=False):
            outputs["identical"].append(str(rel))
        else:
            outputs["different"].append(str(rel))
    outputs["ok"] = not outputs["different"] and not outputs["missing"] and all(s["exit_code"] == 0 for s in outputs["scripts"])
    if spec and not targets:                  # a declared glob that matches nothing verifies nothing
        outputs["ok"] = False
        outputs["missing"] = globs
    shutil.rmtree(tmp, ignore_errors=True)
    return outputs
