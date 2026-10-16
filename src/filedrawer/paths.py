"""Keep the author's machine out of a package: absolute paths in logs become package-relative, or bare file names.

Run logs, provenance and the model-request log are committed with a study, so a path such as
/Users/<name>/Projects/<folder>/pap.json would publish the author's username and folder layout. Everything the
pipeline writes to those logs goes through `scrub`.
"""
from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

# an absolute POSIX path under a user or temp tree, or a Windows user path; stops at whitespace, quotes and brackets
_ABS = re.compile(r"(?<![\w.~/])(?:/(?:Users|home|private|var|tmp|Volumes|mnt|opt|root)/[^\s\"'`<>|,;()\[\]{}]*"
                  r"|[A-Za-z]:\\\\?(?:Users|Documents and Settings)\\[^\s\"'`<>|,;()\[\]{}]*)")


def _roots(root) -> list[str]:
    if not root:
        return []
    out = {str(Path(root)), str(Path(root).resolve())}
    for r in list(out):                                   # macOS: /var -> /private/var, /tmp -> /private/tmp
        if r.startswith("/private/"):
            out.add(r[len("/private"):])
    return sorted(out, key=len, reverse=True)


def scrub(text: str, root=None) -> str:
    """Replace absolute paths: inside `root` with the path relative to it ('.' for root itself), elsewhere with the
    file or folder name alone."""
    if not text or ("/" not in text and "\\" not in text):
        return text
    roots = _roots(root)

    def sub(m: re.Match) -> str:
        raw = m.group(0)
        path = raw.rstrip(".:")                            # keep sentence punctuation outside the path
        tail = raw[len(path):]
        for r in roots:
            if path == r:
                return "." + tail
            if path.startswith(r.rstrip("/") + "/"):
                return path[len(r.rstrip("/")) + 1:] + tail
        name = PureWindowsPath(path).name if "\\" in path else Path(path).name
        return (name or "<local path>") + tail

    return _ABS.sub(sub, text)
