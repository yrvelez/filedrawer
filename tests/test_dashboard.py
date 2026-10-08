"""The dashboard is one static HTML file with one inline script; a stray apostrophe once took the site down."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_inline_scripts_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert scripts, "no inline script found"
    for js in scripts:
        r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-600:]


def test_dashboard_mentions_cost_and_deposit_routine():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "in model calls" in html and "AGENTS.md" in html and "citeText" in html
