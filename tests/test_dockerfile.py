"""The server image copies an explicit list of filedrawer modules: every module server/app.py needs (directly or through
relative imports, including imports inside functions) must be on it, or the deployed server crashes on start-up."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "filedrawer"


def _deps(mod: str, seen: set) -> None:
    p = PKG / f"{mod}.py"
    if mod in seen or not p.exists():
        return
    seen.add(mod)
    for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                _deps(node.module.split(".")[0], seen)
            else:
                for alias in node.names:
                    _deps(alias.name, seen)


def test_image_contains_every_module_the_server_imports():
    server = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    direct = set(re.findall(r"from filedrawer import (\w+)", server)) | set(re.findall(r"from filedrawer\.(\w+) import", server))
    needed: set = set()
    for mod in direct:
        _deps(mod, needed)
    copied = set(re.findall(r"src/filedrawer/(\w+)\.py", (ROOT / "Dockerfile").read_text(encoding="utf-8")))
    assert direct and needed - copied == set(), f"add to the Dockerfile COPY line: {sorted(needed - copied)}"
