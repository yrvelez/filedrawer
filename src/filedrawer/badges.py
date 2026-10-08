"""SVG badges: one source of truth for the pills that say what a package is (provenance, review, registration, release,
design, data, cost). Rendered three ways from the same geometry: SVG files in figures/badges/ so report.md and README
show them on GitHub, a `badges` array in study.json and the dashboard record, and (mirrored in docs/index.html) inline
SVG on the site. The JS twin uses the same CHAR_W, PAD, HEIGHTS and PALETTE; tests/test_badges.py checks they agree."""
from __future__ import annotations

import html
import math
from pathlib import Path

PALETTE = {"indigo": "#3e5870", "green": "#5b6b3f", "amber": "#a8613b", "red": "#7d2f2b", "slate": "#615c53", "teal": "#2f5d5a", "violet": "#64506a", "blue": "#3d5878"}
LABEL_FILL = "#3b3833"
CHAR_W = 6.2          # average glyph width at 11 px, Inter/Verdana
PAD = 6
HEIGHTS = {"sm": 20, "md": 24}
FONT_PX = {"sm": 11, "md": 12}
FONT = "Inter,Verdana,DejaVu Sans,sans-serif"
ORDER = ("provenance", "sample", "review", "registration", "release", "doi", "design", "data", "cost")
# There is no editor, so the review badge carries no accept/revise verdict: it states the claim-check tally.


def seg_width(text: str, size: str = "sm") -> int:
    return 2 * PAD + math.ceil(len(text) * CHAR_W * FONT_PX[size] / 11)


def badge(name: str, label: str, value: str, color: str, href: str | None = None, size: str = "sm") -> str:
    """A shields-style two-tone pill as a standalone SVG string. `href` is not drawn (GitHub serves SVG as images); the
    markdown row wraps the image in a link instead."""
    h, font = HEIGHTS[size], FONT_PX[size]
    lw, vw = seg_width(label, size), seg_width(value, size)
    w = lw + vw
    fill = PALETTE.get(color, color)
    lab, val = html.escape(label, quote=True), html.escape(value, quote=True)
    cid = f"fd-{name}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" role="img" aria-label="{lab}: {val}">'
            f'<title>{lab}: {val}</title>'
            f'<clipPath id="{cid}"><rect width="{w}" height="{h}" rx="3" fill="#fff"/></clipPath>'
            f'<g clip-path="url(#{cid})"><rect width="{lw}" height="{h}" fill="{LABEL_FILL}"/><rect x="{lw}" width="{vw}" height="{h}" fill="{fill}"/></g>'
            f'<g fill="#fff" text-anchor="middle" font-family="{FONT}" font-size="{font}" text-rendering="geometricPrecision">'
            f'<text x="{lw / 2:g}" y="{h / 2 + 4:g}">{lab}</text><text x="{lw + vw / 2:g}" y="{h / 2 + 4:g}">{val}</text></g></svg>')


PASS_NAMES = {"light": "light pass", "advanced": "advanced", "coarse": "coarse", "refine": "refine", "openreview": "openreview"}


def review_kind(mode) -> str:
    """'light,advanced' -> 'light pass + advanced'; a package that went through the pipeline's review says which."""
    parts = [m.strip() for m in (mode if isinstance(mode, list) else str(mode or "light").split(",")) if m and m.strip()]
    return " + ".join(PASS_NAMES.get(m, m) for m in parts) or "light pass"


def badges_for(sj: dict) -> list[dict]:
    """The badge set for a study.json (or a dashboard record that carries the same keys): [{name, label, value, color, href}]."""
    prov = sj.get("provenance") or {}
    design = sj.get("design") or {}
    reg = sj.get("registration") or {}
    out = []
    mode = prov.get("mode") or sj.get("provenance_mode") or "fully_agentic"
    out.append({"name": "provenance", "label": "provenance", "value": "human reviewed" if mode == "human_reviewed" else "fully agentic",
                "color": "green" if mode == "human_reviewed" else "indigo", "href": None})
    if (design.get("sample_kind") or sj.get("sample_kind") or "human") != "human":
        out.append({"name": "sample", "label": "respondents", "value": "synthetic (LLM)", "color": "violet", "href": None})
    tally = sj.get("review_claims") or {}
    rounds = sj.get("review_rounds") or 0
    passed = prov.get("reviewer_pass") or sj.get("reviewer_pass")
    kind = review_kind(prov.get("review_mode") or sj.get("review_mode"))
    if tally.get("total"):
        value = f"{kind} · {tally.get('supported', 0)}/{tally['total']} claims supported"
        if rounds and rounds > 1:
            value = f"{kind} · round {rounds} · {tally.get('supported', 0)}/{tally['total']} claims supported"
        color = "green" if tally.get("supported", 0) == tally["total"] else "amber"
        out.append({"name": "review", "label": "review", "value": value, "color": color, "href": None})
    elif passed:
        out.append({"name": "review", "label": "review", "value": kind, "color": "teal", "href": None})
    else:
        out.append({"name": "review", "label": "review", "value": "unreviewed", "color": "slate", "href": None})
    status = reg.get("status") or sj.get("registration_status") or "registered"
    url = reg.get("url") or sj.get("registration_url")
    if status == "registered":
        note = str(reg.get("note") or "")
        ident = note.split("#", 1)[1].split()[0].rstrip(".,;") if "#" in note else ""
        out.append({"name": "registration", "label": "plan", "value": "pre-registered" + (f" #{ident}" if ident else ""), "color": "green", "href": url})
    else:
        reconstructed = bool(reg.get("note"))
        out.append({"name": "registration", "label": "plan", "value": "reconstructed" if reconstructed else "not pre-registered",
                    "color": "amber" if reconstructed else "red", "href": url})
    rel = sj.get("release_status") or "draft"
    out.append({"name": "release", "label": "status", "value": rel, "color": "green" if rel == "released" else "amber", "href": None})
    if sj.get("doi"):                                 # the Zenodo concept DOI (all versions)
        out.append({"name": "doi", "label": "DOI", "value": str(sj["doi"]), "color": "blue", "href": f"https://doi.org/{sj['doi']}"})
    dtype = str(design.get("type") or sj.get("design_type") or "study").replace("_", " ")
    out.append({"name": "design", "label": "design", "value": dtype, "color": "slate", "href": None})
    if sj.get("synthetic"):
        data = ("simulated demo", "amber")
    elif (design.get("sample_kind") or sj.get("sample_kind") or "human") != "human":
        data = ("synthetic", "amber")
    else:
        n_data = (sj.get("files") or {}).get("n_data")
        open_data = (n_data or 0) > 0 if n_data is not None else bool(sj.get("hypotheses"))
        data = ("open data", "green") if open_data else ("report only", "slate")
    out.append({"name": "data", "label": "data", "value": data[0], "color": data[1], "href": None})
    cost = prov.get("cost_usd") if prov.get("cost_usd") is not None else sj.get("cost_usd")
    if prov.get("provider") == "local":              # run on the author's own machine: no model bill, nothing sent out
        out.append({"name": "cost", "label": "models", "value": "local", "color": "teal", "href": None})
    elif cost is not None:
        out.append({"name": "cost", "label": "model calls", "value": f"${float(cost):.2f}", "color": "slate", "href": None})
    return out


def write_badges(study: Path, sj: dict) -> list[dict]:
    """figures/badges/<name>.svg for every badge; returns the badges with a "file" path each."""
    d = study / "figures" / "badges"
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.svg"):
        f.unlink()
    out = []
    for b in badges_for(sj):
        (d / f"{b['name']}.svg").write_text(badge(b["name"], b["label"], b["value"], b["color"]), encoding="utf-8")
        out.append({**b, "file": f"figures/badges/{b['name']}.svg"})
    return out


def markdown_row(badges: list[dict], base: str = "") -> str:
    """One line of badge images for report.md / README, each linked when it has an href."""
    parts = []
    for b in badges:
        src = base + (b.get("file") or f"figures/badges/{b['name']}.svg")
        img = f"![{b['label']}: {b['value']}]({src})"
        parts.append(f"[{img}]({b['href']})" if b.get("href") else img)
    return " ".join(parts)
