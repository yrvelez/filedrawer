"""Run generated analysis scripts in a subprocess with a cwd jail and a scrubbed environment.

This is a containment measure, not a security boundary: scripts run as the
invoking user. What it guarantees: no API key in the environment, a timeout,
capped output, and a guard against printing row-level data back to the agent:
`model_view()` withholds every output line that reproduces an individual row of
the study's data (agents must write CSV/PNG outputs and print only aggregates).
"""
from __future__ import annotations

import csv
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

OUTPUT_DIRS = ("results", "figures", "data")
TAIL_CHARS = 3000
_NUMERIC_ROW = re.compile(r"^\s*(?:[-+]?\d+(?:\.\d+)?|NA|nan|None|\w{1,12})?(?:[,\t ]+[-+]?\d+(?:\.\d+)?){4,}\s*$")
# data a script can read and print: the study's tidy/clean data and any raw export kept in inputs/
ROW_SOURCES = ("data/*.csv", "inputs/*.csv")
_NUM_TOKEN = re.compile(r"(?<![\w.])[-+]?\d+(?:\.(\d+))?(?:[eE]([-+]?\d+))?(?![\w.%])")
_PAIR = re.compile(r"^\s*(\S+)\s+(\S.*?)\s*$")   # "column   value", as in print(df.iloc[i])
_RECORD_SPLIT = re.compile(r"[\]\)\}]\s*,\s*[\[\(\{]")   # "], [" between printed records
_KV = re.compile(r"""["']?([A-Za-z_][\w.]*)["']?\s*[:=]\s*("[^"]*"|'[^']*'|[^,;}\]\s]+)""")   # 'name': value
WITHHELD = "[{n} line(s) withheld: they reproduce individual rows of the data. Print aggregates only.]"


def _snapshot(study_dir: Path) -> dict[str, float]:
    snap = {}
    for d in OUTPUT_DIRS:
        p = study_dir / d
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    snap[str(f.relative_to(study_dir))] = f.stat().st_mtime
    return snap


def _tail(s: str, n: int = TAIL_CHARS) -> str:
    if len(s) <= n:
        return s
    head = n // 4
    return s[:head] + f"\n... [{len(s) - n} chars omitted] ...\n" + s[-(n - head):]


def looks_like_row_dump(stdout: str, max_rows: int = 25) -> bool:
    rows = sum(1 for line in stdout.splitlines() if _NUMERIC_ROW.match(line))
    return rows > max_rows


class _Frame:
    """One data file, indexed by column so printed values can be traced back to rows."""

    def __init__(self, df):
        import numpy as np
        import pandas as pd
        self.n = len(df)
        self.rare_k = max(5, math.ceil(0.002 * self.n))  # a value this rare singles out a row (or one respondent)
        self.cols: dict = {}   # name -> ("num", sorted values, row order) | ("txt", {value: rows})
        self.ids: set[str] = set()
        self.codes: set[str] = set()
        for c in df.columns:
            s = df[c]
            if s.nunique(dropna=True) <= 1:
                continue
            if not pd.api.types.is_numeric_dtype(s):
                conv = pd.to_numeric(s, errors="coerce")
                if conv.notna().sum() >= 0.5 * s.notna().sum():
                    s = conv
            if pd.api.types.is_numeric_dtype(s):
                v = s.to_numpy(dtype=float)
                rows = np.flatnonzero(~np.isnan(v))
                order = rows[np.argsort(v[rows], kind="stable")]
                self.cols[str(c)] = ("num", v[order], order)
                u = np.unique(v[rows])
                # sequential ids (1..k, many values): they locate a row but say nothing about anyone
                if len(u) >= max(50, 0.1 * self.n) and np.all(u == np.round(u)) and u[-1] - u[0] + 1 == len(u):
                    self.ids.add(str(c))
                elif len(u) <= 12:                     # codes: binaries, Likert items, trial numbers
                    self.codes.add(str(c))
            else:
                groups: dict[str, list[int]] = {}
                for i, x in enumerate(s.astype("string").fillna("").tolist()):
                    if x.strip():
                        groups.setdefault(x.strip(), []).append(i)
                if len(groups) <= 50000:
                    self.cols[str(c)] = ("txt", {k: np.asarray(v) for k, v in groups.items()})

    def rows_for(self, name: str, raw: str):
        """(rows whose `name` value prints as `raw`, informative?) or None if `raw` is not a value
        of that column. Values of id columns and of code columns (12 or fewer distinct values) are not
        informative on their own."""
        import numpy as np
        spec = self.cols.get(name)
        if spec is None:
            return None
        raw = raw.strip().strip("\"'")
        if spec[0] == "txt":
            idx = spec[1].get(raw)
            return None if idx is None else (idx, len(raw) >= 3)
        m = _NUM_TOKEN.fullmatch(raw)
        if not m:
            return None
        v, d = float(m.group(0)), len(m.group(1) or "") - int(m.group(2) or 0)   # printed precision
        # the value as printed at d decimals; a whole number matches only itself (a weight of 3.87
        # is never printed as "4")
        tol = 0.5 * 10.0 ** (-d) * 1.000001 if d > 0 else 1e-9 * max(1.0, abs(v))
        sv, order = spec[1], spec[2]
        idx = order[int(np.searchsorted(sv, v - tol, "left")):int(np.searchsorted(sv, v + tol, "right"))]
        return idx, name not in self.ids and name not in self.codes

    def pairs_match(self, pairs: list[tuple[str, str]]) -> bool:
        """Do these (column, printed value) pairs reproduce one row? Yes when the row matches at least
        half of the pairs and half (two or more) of the values that are not small codes, one of them
        rare (two, for a table of two or three columns; whole numbers count half); or, for
        rows of common values only, when it matches at least four pairs and 80% of them."""
        import numpy as np
        hits = np.zeros(self.n, dtype=np.int32)
        info = np.zeros(self.n, dtype=np.int32)     # matched values that are not small codes
        rare = np.zeros(self.n, dtype=np.float32)   # rare matches: 1, or 0.5 for a whole number
        used = used_info = 0
        for name, raw in pairs:
            r = self.rows_for(name, raw)
            if r is None:
                continue
            used += 1
            idx, informative = r
            used_info += informative
            if len(idx):
                hits[idx] += 1
                if informative:
                    info[idx] += 1
                    if len(idx) <= self.rare_k:
                        rare[idx] += 0.5 if _is_whole(raw) else 1.0
        if used < 2:
            return False
        need_rare = 1.0 if used >= 4 else 2.0       # two or three columns: two rare values
        if bool(((hits >= max(2, math.ceil(0.5 * used))) & (info >= max(2, math.ceil(0.5 * used_info)))
                 & (rare >= need_rare)).any()):
            return True
        return used >= 4 and bool((hits >= max(4, math.ceil(0.8 * used))).any())

    def bag_matches(self, line: str) -> bool:
        """Fallback for values printed without column names (f-strings, lists of values). Each
        bracketed record on the line is judged alone, on its informative values (not ids or small
        codes). Withheld when one row accounts for two values that are each rare in their column, or
        when one row accounts for three or more of the values, at least half of them, and no more
        than a handful of rows share that combination (a respondent identified jointly)."""
        import numpy as np
        for seg in _RECORD_SPLIT.split(line):
            toks = list(dict.fromkeys(m.group(0) for m in _NUM_TOKEN.finditer(seg)))
            rare_pts = np.zeros(self.n, dtype=np.float32)
            matched = np.zeros(self.n, dtype=np.int32)    # informative values on the line equal to the row's
            n_inf = 0
            for t in toks:
                best = np.zeros(self.n, dtype=np.float32)
                hit = np.zeros(self.n, dtype=bool)
                informative_tok = False
                for name, spec in self.cols.items():
                    if spec[0] != "num":
                        continue
                    idx, informative = self.rows_for(name, t)
                    if not informative:
                        continue
                    informative_tok = True
                    if not len(idx):
                        continue
                    hit[idx] = True
                    if len(idx) <= self.rare_k:
                        best[idx] = np.maximum(best[idx], 0.5 if _is_whole(t) else 1.0)
                n_inf += informative_tok
                rare_pts += best                            # one token counts once per row
                matched += hit
            text_hits: dict[str, tuple] = {}             # one text value counts once per row
            for name, spec in self.cols.items():
                if spec[0] == "txt":
                    for val, idx in spec[1].items():
                        if len(val) >= 3 and val in seg and re.search(r"(?<!\w)" + re.escape(val) + r"(?!\w)", seg):
                            hit, rare = text_hits.get(val, (np.zeros(self.n, dtype=bool), np.zeros(self.n, dtype=bool)))
                            hit[idx] = True
                            if len(idx) <= self.rare_k:
                                rare[idx] = True
                            text_hits[val] = (hit, rare)
            for hit, rare in text_hits.values():
                n_inf += 1
                matched += hit
                rare_pts += rare
            if bool((rare_pts >= 2).any()):
                return True
            top = int(matched.max()) if self.n else 0
            if top >= 3 and top >= math.ceil(0.5 * n_inf) and int((matched == top).sum()) <= self.rare_k:
                return True
        return False


def _is_whole(raw: str) -> bool:
    try:
        v = float(raw.strip().strip("\"'"))
    except ValueError:
        return False
    return v == round(v)


_FRAMES: dict[str, tuple[float, _Frame]] = {}


def _row_frames(study_dir: Path) -> list[_Frame]:
    import pandas as pd
    out = []
    for pattern in ROW_SOURCES:
        for f in sorted(study_dir.glob(pattern)):
            try:
                mtime = f.stat().st_mtime
                key = str(f.resolve())
                if key not in _FRAMES or _FRAMES[key][0] != mtime:
                    _FRAMES[key] = (mtime, _Frame(pd.read_csv(f, low_memory=False)))
                out.append(_FRAMES[key][1])
            except Exception:   # unreadable file: nothing to match against
                continue
    return out


def _kv_records(line: str, names: set[str]) -> list[list[tuple[str, str]]]:
    """'name': value / name=value pairs on one line (JSON records, dicts, f-strings), split into
    records wherever a column name repeats."""
    records: list[list[tuple[str, str]]] = [[]]
    for m in _KV.finditer(line):
        if m.group(1) not in names:
            continue
        if any(n == m.group(1) for n, _ in records[-1]):
            records.append([])
        records[-1].append((m.group(1), m.group(2)))
    return [r for r in records if len(r) >= 2]


def _table_pairs(lines: list[str], names: set[str]) -> dict[int, list[tuple[str, str]]]:
    """Pairs for lines printed under a header of column names: pandas tables (fixed width, right
    aligned, wide frames wrapped into chunks) and CSV. Wrapped chunks of the same frame are joined,
    so each printed row is judged on all of its columns. Keys are line indices."""
    out: dict[int, list[tuple[str, str]]] = {}
    i, chunk_rows = 0, []                      # chunk_rows: line indices of the previous chunk
    while i < len(lines):
        head = lines[i]
        if head.count(",") >= 1 and all(h.strip().strip('"') in names for h in head.split(",") if h.strip()):
            cols = [h.strip().strip('"') for h in head.split(",")]
            j = i + 1
            while j < len(lines) and lines[j].count(",") >= len(cols) - 1:
                vals = next(csv.reader([lines[j]]))
                out[j] = [(c, v) for c, v in zip(cols, vals) if c in names]
                j += 1
            i, chunk_rows = j, []
            continue
        words = [(m.group(0), m.end()) for m in re.finditer(r"\S+", head)]
        named = [(w, e) for w, e in words if w in names]
        if len(named) >= 2 and len(named) >= len([w for w, _ in words if w not in ("...", "\\")]) - 1:
            ends = [e for _, e in named]
            j, rows = i + 1, []
            while j < len(lines) and lines[j].strip() and not lines[j].lstrip().startswith("["):
                ln = lines[j]
                if len(ln) < min(ends) or re.match(r"^\s*\S+\s*$", ln) and j == i + 1:
                    j += 1                         # index-name line under the header
                    continue
                pairs, prev = [], 0
                for (w, e), start in zip(named, [0] + ends[:-1]):
                    pairs.append((w, re.split(r"\s{2,}", ln[start:e].strip())[-1]))
                rows.append(j)
                out[j] = pairs
                j += 1
            if chunk_rows and len(chunk_rows) == len(rows):   # continuation chunk of a wrapped frame
                for a, b in zip(chunk_rows, rows):
                    joined = out[a] + out[b]
                    out[a] = out[b] = joined
                rows = [x for pair in zip(chunk_rows, rows) for x in pair]
            chunk_rows = rows if rows else chunk_rows
            i = j + 1 if j < len(lines) and not lines[j].strip() else j
            continue
        i += 1
    return out


def withhold_rows(text: str, study_dir: str | Path) -> tuple[str, int]:
    """Replace every line of `text` that reproduces an individual row of the study's data with a
    notice. Runs locally against the data files; only the filtered text reaches the model.
    Returns (filtered text, number of lines withheld)."""
    if not text.strip():
        return text, 0
    if looks_like_row_dump(text):
        return "[output withheld: it looks like row-level data. Print aggregates only.]", len(text.splitlines())
    frames = _row_frames(Path(study_dir).resolve())
    if not frames:
        return text, 0
    lines = text.splitlines()
    hold = [False] * len(lines)
    names = set().union(*(fr.cols for fr in frames))
    # 1. tables under a header of column names (pandas prints, CSV)
    table = _table_pairs(lines, names)
    for i, pairs in table.items():
        if any(fr.pairs_match(pairs) for fr in frames):
            hold[i] = True
    # 2. name/value pairs on one line (JSON, dicts)
    for i, line in enumerate(lines):
        if not hold[i]:
            if any(fr.pairs_match(rec) for rec in _kv_records(line, names) for fr in frames):
                hold[i] = True
    # 3. vertically printed rows: runs of "column value" lines (print(df.iloc[i]))
    block: list[int] = []
    for i in range(len(lines) + 1):
        m = _PAIR.match(lines[i]) if i < len(lines) else None
        if m and m.group(1) in names:
            block.append(i)
            continue
        if len(block) >= 2:
            pairs = [_PAIR.match(lines[j]).groups() for j in block]
            cells = [re.split(r"\s{2,}", v) for _, v in pairs]
            widths = {len(c) for c in cells}
            if len(widths) == 1 and widths != {1}:     # transposed rows: one row per position
                rows = [[(n, c[k]) for (n, _), c in zip(pairs, cells)] for k in range(widths.pop())]
            else:
                rows = [pairs]
            if any(fr.pairs_match(r) for r in rows for fr in frames):
                for j in block:
                    hold[j] = True
        block = []
    # 4. values printed without column names
    for i, line in enumerate(lines):
        if not hold[i] and i not in table and _NUM_TOKEN.search(line) and any(fr.bag_matches(line) for fr in frames):
            hold[i] = True
    kept, run = [], 0
    for line, h in zip(lines, hold):
        if h:
            run += 1
            continue
        if run:
            kept.append(WITHHELD.format(n=run))
            run = 0
        kept.append(line)
    if run:
        kept.append(WITHHELD.format(n=run))
    return "\n".join(kept), sum(hold)


def _tail_lines(s: str, n: int = TAIL_CHARS) -> str:
    """Like _tail, but cuts at line boundaries, so no printed row is split across the cut."""
    if len(s) <= n:
        return s
    lines = s.splitlines()
    head, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > n // 4:
            break
        head.append(ln)
        used += len(ln) + 1
    tail, used = [], 0
    for ln in reversed(lines[len(head):]):
        if used + len(ln) + 1 > n - n // 4:
            break
        tail.insert(0, ln)
        used += len(ln) + 1
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... [{omitted} lines omitted] ..."] + tail)


def model_view(res: dict, study_dir: str | Path) -> dict:
    """The part of a run_script result that may be shown to a model: the full stdout and stderr
    are filtered (lines reproducing data rows withheld) and only then trimmed."""
    out = {k: v for k, v in res.items() if not k.startswith("_")}
    n = 0
    for key, full in (("stdout_tail", "_stdout"), ("stderr_tail", "_stderr")):
        text = res.get(full, res.get(key, ""))
        if len(text) > 20 * TAIL_CHARS:            # only the part that would be shown is checked
            text = _tail_lines(text, 4 * TAIL_CHARS)
        filtered, k = withhold_rows(text, study_dir)
        out[key], n = _tail_lines(filtered), n + k
    if n:
        out["lines_withheld"] = n
    return out


def safe_relpath(study_dir: Path, rel: str) -> Path:
    rel = rel.strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError(f"invalid path: {rel!r}")
    p = (study_dir / rel).resolve()
    if study_dir.resolve() not in p.parents and p != study_dir.resolve():
        raise ValueError(f"path escapes study directory: {rel!r}")
    return p


def run_script(study_dir: str | Path, rel_path: str, timeout_s: int = 300,
               language: str = "python") -> dict:
    study_dir = Path(study_dir).resolve()
    script = safe_relpath(study_dir, rel_path)
    if not script.exists():
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": f"script not found: {rel_path}",
                "files_created": [], "duration_s": 0.0}
    if language == "auto":                     # author-supplied scripts (methods packages): pick by extension
        language = "rscript" if script.suffix.lower() == ".r" else "python"
    if language == "python":
        cmd = [sys.executable, "-X", "utf8", str(script)]
    elif language == "rscript":
        cmd = ["Rscript", "--vanilla", str(script)]
    elif language == "r":
        from .analysis.r_backend import rscript_command
        cmd = rscript_command(script)
    else:
        raise ValueError(f"unknown language {language}")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(study_dir)),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(study_dir / ".mplconfig"),
        "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": "2",
    }
    for d in OUTPUT_DIRS:
        (study_dir / d).mkdir(exist_ok=True)
    before = _snapshot(study_dir)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=study_dir, env=env, capture_output=True, text=True,
                              timeout=timeout_s, errors="replace")
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code = 124
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = f"TIMEOUT after {timeout_s}s"
    dur = round(time.time() - t0, 2)
    after = _snapshot(study_dir)
    created = sorted(k for k, m in after.items() if k not in before or before[k] != m)
    if looks_like_row_dump(out):
        out = "[stdout suppressed: output looks like row-level data; print aggregates only]"
    return {"exit_code": code, "stdout_tail": _tail(out), "stderr_tail": _tail(err),
            "files_created": created, "duration_s": dur, "_stdout": out, "_stderr": err}
