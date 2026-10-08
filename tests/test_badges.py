"""SVG badges: geometry, the badge set derived from study.json / records, files and the markdown row, and parity between
badges.py and the JS twin in docs/index.html."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from filedrawer import badges as B

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://www.w3.org/2000/svg}"


def test_badge_geometry_and_markup():
    svg = B.badge("provenance", "provenance", "fully agentic", "indigo")
    root = ET.fromstring(svg)
    lw, vw = B.seg_width("provenance"), B.seg_width("fully agentic")
    assert root.get("width") == str(lw + vw) and root.get("height") == "20"
    rects = [r for r in root.iter(NS + "rect")]
    assert [r.get("width") for r in rects][1:] == [str(lw), str(vw)]              # label half, value half (after the clip rect)
    assert rects[2].get("fill") == B.PALETTE["indigo"] and rects[1].get("fill") == B.LABEL_FILL
    texts = [t.text for t in root.iter(NS + "text")]
    assert texts == ["provenance", "fully agentic"] and root.find(NS + "title").text == "provenance: fully agentic"
    assert B.badge("x", "a", "b", "#123456", size="md").count('height="24"') == 4            # svg, clip rect, two halves
    assert "&lt;" in B.badge("x", "<a>", "b", "slate")                              # escaped


def _sj(**over):
    sj = {"provenance": {"mode": "fully_agentic", "reviewer_pass": True, "cost_usd": 2.3607},
          "registration": {"status": "registered", "url": "https://aspredicted.org/x.pdf", "note": "AsPredicted #132,508"},
          "release_status": "draft", "design": {"type": "conjoint", "sample_kind": "human"}, "hypotheses": [{"id": "H1"}],
          "review_claims": {"supported": 11, "total": 12, "open_analytical": 2, "on_revised_text": True}, "review_rounds": 1}
    sj.update(over)
    return sj


def test_badges_for_a_package():
    by = {b["name"]: b for b in B.badges_for(_sj())}
    assert [b["name"] for b in B.badges_for(_sj())] == ["provenance", "review", "registration", "release", "design", "data", "cost"]
    assert by["provenance"]["value"] == "fully agentic" and by["review"]["value"] == "light pass · 11/12 claims supported" and by["review"]["color"] == "amber"
    assert by["registration"]["value"] == "pre-registered #132,508" and by["registration"]["href"].startswith("https://aspredicted")
    assert by["release"]["value"] == "draft" and by["design"]["value"] == "conjoint" and by["data"]["value"] == "open data"
    assert by["cost"]["value"] == "$2.36"


def test_badges_for_variants():
    by = {b["name"]: b for b in B.badges_for(_sj(provenance={"mode": "human_reviewed", "reviewer_pass": False}, review_claims=None,
                                                  registration={"status": "none", "note": "reconstructed from the analysis script"},
                                                  release_status="released", design={"type": "survey_experiment", "sample_kind": "synthetic"}))}
    assert by["provenance"]["value"] == "human reviewed" and by["review"]["value"] == "unreviewed"
    assert by["sample"]["value"] == "synthetic (LLM)" and by["data"]["value"] == "synthetic"
    assert by["registration"]["value"] == "reconstructed" and by["registration"]["color"] == "amber" and by["release"]["color"] == "green"
    assert "cost" not in by
    by = {b["name"]: b for b in B.badges_for(_sj(review_claims={"supported": 3, "total": 3}, review_rounds=2, synthetic=True))}
    assert by["review"]["value"] == "light pass · round 2 · 3/3 claims supported" and by["review"]["color"] == "green" and by["data"]["value"] == "simulated demo"
    # a dashboard record (flattened keys)
    rec = {"provenance_mode": "fully_agentic", "reviewer_pass": True, "registration_status": "none", "release_status": "released",
           "design_type": "survey_experiment", "files": {"n_data": 0}, "cost_usd": 0.1}
    by = {b["name"]: b for b in B.badges_for(rec)}
    assert by["review"]["value"] == "light pass" and by["registration"]["value"] == "not pre-registered" and by["data"]["value"] == "report only"


def test_write_and_markdown_row(tmp_path):
    rows = B.write_badges(tmp_path, _sj())
    assert all((tmp_path / r["file"]).exists() for r in rows) and len(list((tmp_path / "figures" / "badges").glob("*.svg"))) == len(rows)
    md = B.markdown_row(rows)
    assert md.startswith("![provenance: fully agentic](figures/badges/provenance.svg)")
    assert "[![plan: pre-registered #132,508](figures/badges/registration.svg)](https://aspredicted.org/x.pdf)" in md
    assert B.markdown_row(rows, base="https://raw.example/").count("https://raw.example/figures/badges/") == len(rows)


def test_js_twin_matches_python_geometry():
    html = (ROOT / "docs" / "index.html").read_text()
    if "function badgeSVG" not in html:
        pytest.skip("the JS badge renderer is not in docs/index.html yet")
    js = html[html.index("function badgeSVG") - 2000:html.index("function badgeSVG") + 3000]
    assert float(re.search(r"CHAR_W\s*=\s*([\d.]+)", js).group(1)) == B.CHAR_W
    assert int(re.search(r"PAD\s*=\s*(\d+)", js).group(1)) == B.PAD
    heights = dict(re.findall(r"(sm|md)\s*:\s*(\d+)", re.search(r"HEIGHTS\s*=\s*\{([^}]*)\}", js).group(1)))
    assert {k: int(v) for k, v in heights.items()} == B.HEIGHTS
    palette = dict(re.findall(r"(\w+)\s*:\s*['\"](#[0-9a-fA-F]{6})['\"]", re.search(r"PALETTE\s*=\s*\{([^}]*)\}", js).group(1)))
    assert palette == B.PALETTE
    assert B.LABEL_FILL in js and "rx=\"3\"" in js.replace("'", '"')
