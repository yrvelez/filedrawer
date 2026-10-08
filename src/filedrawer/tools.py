"""Tools exposed to agents. Paths are relative to the study directory and jailed there.

- data/ is never readable through tools (scripts read it; agents see only aggregates).
- write_file is limited to scripts/.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from . import exec as _exec

READABLE_PREFIXES = ("scripts/", "results/", "figures/")
READABLE_FILES = ("codebook.md", "codebook.json", "pap.json", "RUN.md")
WRITABLE_PREFIXES = ("scripts/",)

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file in the study folder (scripts/, results/, codebook.*, pap.json). Data files are never readable.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file under scripts/. Overwrites.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_script", "description": "Run a script under scripts/ with the study folder as working directory. Returns exit code, stdout/stderr tails and files created under results/, figures/, data/. Print only aggregates; write tables to results/*.csv and figures to figures/*.png.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List file names under a study subfolder.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "record_result", "description": "Hand a structured result to the orchestrator. Set final=true when your task is complete.",
      "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {}, "final": {"type": "boolean"}}, "required": ["key", "value"]}}},
]


class ToolRegistry:
    def __init__(self, study_dir: str | Path, timeout_s: int = 300, language: str = "python",
                 allowed: tuple[str, ...] | None = None, writable: tuple[str, ...] | None = None):
        self.study_dir = Path(study_dir).resolve()
        self.timeout_s = timeout_s
        self.language = language
        self.records: dict[str, Any] = {}
        self.final = False
        self.allowed = set(allowed) if allowed else {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.consecutive_failures = 0
        self.writable = set(writable) if writable else None     # exact files an agent may write (None: all of scripts/)
        self.runs: dict[str, dict] = {}                          # last run_script result per script, cleared on write
        self.history_dir = self.study_dir / "provenance" / "script_history"

    def schemas(self) -> list[dict]:
        return [s for s in TOOL_SCHEMAS if s["function"]["name"] in self.allowed]

    # -- tools ---------------------------------------------------------------
    def read_file(self, path: str) -> str:
        rel = path.strip().lstrip("./")
        if rel.startswith("data/") or rel == "data":
            return "ERROR: data files are never readable; run a script and print aggregates instead."
        if not (rel.startswith(READABLE_PREFIXES) or rel in READABLE_FILES):
            return f"ERROR: {rel} is not readable (allowed: {', '.join(READABLE_PREFIXES + READABLE_FILES)})."
        try:
            p = _exec.safe_relpath(self.study_dir, rel)
            txt = p.read_text(encoding="utf-8", errors="replace")
        except (ValueError, OSError) as e:
            return f"ERROR: {e}"
        return _exec._tail(txt, 6000)

    def write_file(self, path: str, content: str) -> str:
        rel = path.strip().lstrip("./")
        if not rel.startswith(WRITABLE_PREFIXES):
            return f"ERROR: write_file is limited to {WRITABLE_PREFIXES}."
        if self.writable is not None and rel not in self.writable:
            return (f"ERROR: you may only write {', '.join(sorted(self.writable))}. Do not create helper or debug scripts: "
                    f"add print statements to that script instead.")
        try:
            p = _exec.safe_relpath(self.study_dir, rel)
        except ValueError as e:
            return f"ERROR: {e}"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            self.history_dir.mkdir(parents=True, exist_ok=True)
            n = len(list(self.history_dir.glob(p.name + ".*"))) + 1
            shutil.copy(p, self.history_dir / f"{p.name}.{n}")
        p.write_text(content, encoding="utf-8")
        self.runs.pop(rel, None)
        return f"wrote {rel} ({len(content)} chars)"

    def run_script(self, path: str) -> str:
        rel = path.strip().lstrip("./")
        if not rel.startswith("scripts/"):
            return "ERROR: only scripts under scripts/ can be run."
        try:
            res = _exec.model_view(_exec.run_script(self.study_dir, rel, self.timeout_s, self.language),
                                   self.study_dir)
        except ValueError as e:
            return f"ERROR: {e}"
        self.runs[rel] = res
        if res["exit_code"] != 0:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        out = json.dumps(res)
        if res.get("lines_withheld"):
            out += "\nNOTE: some output lines were withheld because they reproduce individual data rows. Print aggregates only (means, counts, estimates)."
        if self.consecutive_failures >= 2:
            out += "\nHINT: two failures in a row. Simplify the script; do not print data rows; check column names in codebook.md."
        return out

    def list_dir(self, path: str) -> str:
        try:
            p = _exec.safe_relpath(self.study_dir, path or ".")
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.is_dir():
            return f"ERROR: {path} is not a directory"
        return "\n".join(sorted(f.name for f in p.iterdir()))

    def record_result(self, key: str, value: Any, final: bool = False) -> str:
        if isinstance(value, str):
            # models sometimes double-encode the payload as a JSON string; accept it
            try:
                decoded = json.loads(value)
                if isinstance(decoded, (dict, list)):
                    value = decoded
            except ValueError:
                pass
        self.records[key] = value
        if final:
            self.final = True
        return f"recorded {key}" + (" (final)" if final else "")

    # -- dispatch -------------------------------------------------------------
    def dispatch(self, name: str, args: dict) -> str:
        if name not in self.allowed:
            return f"ERROR: unknown tool {name}"
        fn: Callable = getattr(self, name)
        try:
            return str(fn(**args))
        except TypeError as e:
            return f"ERROR: bad arguments for {name}: {e}"
