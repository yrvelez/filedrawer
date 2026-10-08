"""Design diagrams: a deterministic SVG for every standard design from pap.json (and for each extension proposal),
a plain-text description saved beside it, and a sanitizer for model-authored SVG used when the design is not standard.

SVG conventions: viewBox 0 0 960 H, width 100%, Inter/Helvetica, colours through CSS variables with fallbacks and a
dark-mode block (the same palette as the figures), role="img" with <title> and <desc>, and one <g id="arm-<code>"
data-text="..."> per arm or level so the site can bind click-through text."""
from __future__ import annotations

import html
import re
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

from . import pap as P

W = 960
STYLE = """<style>
:root{--fd-ink:#1c1b19;--fd-muted:#8a877f;--fd-rule:#d9d5cc;--fd-accent:#8b1e2d;--fd-bg:#ffffff}
@media (prefers-color-scheme: dark){:root{--fd-ink:#ecebe6;--fd-muted:#b4b1a9;--fd-rule:#4a4a52;--fd-accent:#e08a96;--fd-bg:#1d1d23}}
.box{fill:var(--fd-bg,#fff);stroke:var(--fd-ink,#1c1b19);stroke-width:1.2}
.arm{fill:var(--fd-bg,#fff);stroke:var(--fd-accent,#8b1e2d);stroke-width:1.4}
.soft{fill:var(--fd-bg,#fff);stroke:var(--fd-rule,#d9d5cc);stroke-width:1.2}
text{font-family:Inter,Helvetica,Arial,sans-serif;fill:var(--fd-ink,#1c1b19);font-size:14px}
.cap{font-size:12px;fill:var(--fd-muted,#8a877f)}
.kicker{font-size:11px;letter-spacing:.08em;fill:var(--fd-muted,#8a877f);text-transform:uppercase}
.edge{stroke:var(--fd-ink,#1c1b19);stroke-width:1.2;fill:none;marker-end:url(#fd-arrow)}
.assoc{stroke-dasharray:5 4}
#fd-arrow path{fill:var(--fd-ink,#1c1b19)}
</style>
<defs><marker id="fd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>"""
ALLOWED_TAGS = {"svg", "g", "rect", "line", "path", "text", "tspan", "polygon", "polyline", "circle", "ellipse", "title", "desc", "defs",
                "marker", "style", "clipPath", "use"}
MAX_SVG_BYTES = 60_000


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _wrap(s: str, width_px: float, size: int = 14) -> list[str]:
    chars = max(8, int(width_px / (size * 0.56)))
    return textwrap.wrap(str(s or ""), chars)[:4] or [""]


def _box(x, y, w, h, title, caption=None, cls="box", gid=None, data_text=None, kicker=None) -> str:
    out = [f'<g{" id=" + chr(34) + _esc(gid) + chr(34) if gid else ""}{" data-text=" + chr(34) + _esc(data_text) + chr(34) if data_text else ""}>']
    out.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>')
    ty = y + 22
    if kicker:
        out.append(f'<text class="kicker" x="{x + 12}" y="{ty}">{_esc(kicker)}</text>')
        ty += 18
    for ln in _wrap(title, w - 24):
        out.append(f'<text x="{x + 12}" y="{ty}">{_esc(ln)}</text>')
        ty += 18
    if caption:
        for ln in _wrap(caption, w - 24, 12)[:2]:
            out.append(f'<text class="cap" x="{x + 12}" y="{ty}">{_esc(ln)}</text>')
            ty += 15
    out.append("</g>")
    return "\n".join(out)


def _arrow(x1, y1, x2, y2, assoc=False, label=None) -> str:
    s = f'<path class="edge{" assoc" if assoc else ""}" d="M{x1},{y1} L{x2},{y2}"/>'
    if label:
        s += f'<text class="cap" x="{(x1 + x2) / 2}" y="{min(y1, y2) - 6}" text-anchor="middle">{_esc(label)}</text>'
    return s


def _svg(body: str, height: int, title: str, desc: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="100%" role="img" aria-labelledby="fd-title fd-desc">'
            f'<title id="fd-title">{_esc(title)}</title><desc id="fd-desc">{_esc(desc)}</desc>{STYLE}{body}</svg>')


def _box_h(title: str, w: int, caption: str | None = None, kicker: str | None = None) -> int:
    return 30 + (18 if kicker else 0) + 18 * len(_wrap(title, w - 24)) + (15 * min(2, len(_wrap(caption, w - 24, 12))) if caption else 0)


# ---- the study ---------------------------------------------------------------------------------------------------

def describe(pap: dict, meta: dict | None = None) -> str:
    """The diagram in words: the same facts, for the alt text, the .txt file and click-through on the site."""
    w = P.words(pap)
    d = pap.get("design") or {}
    pop = d.get("population") or {}
    meta = meta or {}
    parts = [f"A {w['phrase']}" + (f" on {str(pop.get('sample', '')).replace('_', ' ')} respondents" if pop.get("sample") else "")
             + (f" in {pop['country']}" if pop.get("country") else "") + (f" (N = {meta['n_analysis']:,} analysed)" if meta.get("n_analysis") else "") + "."]
    if w["sample_kind"] != "human":
        parts.append(f"The respondents are LLM-generated ({(d.get('synthetic') or {}).get('model') or 'model unstated'}).")
    feats = d.get("features") or []
    treated, control = P.design_arms(pap)
    outcomes = [o.get("label") or o.get("name") for o in (pap.get("registered") or pap.get("implemented") or {}).get("outcomes", [])]
    if feats:
        parts.append(f"Each profile is built from {len(feats)} features: " + "; ".join(
            f"{f.get('label', f.get('column'))} ({', '.join(str(v) for v in f.get('levels', []))}{'' if f.get('randomized', True) else '; measured, not randomized'})"
            for f in feats) + ".")
        parts.append("Respondents compare two profiles per task and choose one for each outcome.")
    elif treated and control is not None:
        labels = [P.arm_label(pap, a) for a in treated]
        verb = "randomly assigned to" if w["causal"] else "observed in"
        parts.append(f"Respondents are {verb} {len(treated) + 1} {w['units']}: {', '.join(labels)}, against the {w['reference']} {P.arm_label(pap, control)}.")
        descs = (d.get("arms") or {}).get("descriptions") or {}
        for a in treated:
            if descs.get(a):
                parts.append(f"{P.arm_label(pap, a)}: {descs[a]}")
    elif d.get("type") == "methods_comparison":
        parts.append(f"Methods compared: {', '.join(d.get('methods') or [])}; benchmark: {d.get('benchmark', '')}; metrics: {', '.join(d.get('metrics') or [])}.")
    if outcomes:
        parts.append(f"Outcome{'s' if len(outcomes) > 1 else ''}: {', '.join(outcomes)}.")
    names = P.column_labels(pap)
    mods = [names.get(s["moderator"], s["moderator"]) for s in (pap.get("registered") or {}).get("subgroups", []) if s.get("moderator")]
    if mods:
        parts.append(f"{'Planned' if P.registration_status(pap) == 'none' else 'Registered'} moderators: {', '.join(dict.fromkeys(mods))}.")
    if not w["causal"] and d.get("type") != "methods_comparison":
        covs = sorted({c for h in (pap.get("implemented") or {}).get("hypotheses", []) for c in (h.get("estimator") or {}).get("covariates") or []})
        parts.append("Estimates are associations" + (f", adjusted for {', '.join(covs)}" if covs else "") + ".")
    return " ".join(parts)


def render(pap: dict, meta: dict | None = None) -> str | None:
    """SVG for a standard design, or None when the plan does not describe one (the caller may ask a model)."""
    w = P.words(pap)
    d = pap.get("design") or {}
    t = w["type"]
    desc = describe(pap, meta)
    title = f"Design: {w['phrase']}"
    if d.get("nonstandard"):
        return None
    if d.get("features"):
        return _render_conjoint(pap, w, title, desc)
    if t == "methods_comparison":
        return _render_methods(pap, w, title, desc)
    treated, control = P.design_arms(pap)
    if treated and control is not None:
        return _render_arms(pap, w, title, desc, treated, control)
    if not w["causal"]:
        return _render_observational(pap, w, title, desc)
    return None


def _outcome_boxes(pap: dict, x: int, y0: int, w_box: int) -> tuple[str, int]:
    outs = [(o.get("label") or o.get("name")) for o in (pap.get("registered") or pap.get("implemented") or {}).get("outcomes", [])] or ["outcome"]
    body, y = [], y0
    for o in outs[:6]:
        h = _box_h(o, w_box, kicker="Outcome")
        body.append(_box(x, y, w_box, h, o, cls="box", kicker="Outcome"))
        y += h + 10
    return "\n".join(body), y


def _render_arms(pap, w, title, desc, treated, control) -> str:
    d = pap.get("design") or {}
    descs = (d.get("arms") or {}).get("descriptions") or {}
    pop = d.get("population") or {}
    arms = [control] + list(treated)
    col_w, gap = 250, 36
    x_pop, x_rand, x_arm, x_out = 20, 300, 400, 700
    body, y = [], 20
    body.append(f'<text class="kicker" x="20" y="16">{_esc(title)}</text>')
    pop_txt = (str(pop.get("sample", "sample")).replace("_", " ") + (f", {pop['country']}" if pop.get("country") else "")) or "sample"
    pop_h = _box_h(pop_txt, 230, kicker="Population")
    arm_y, arm_hs = [], []
    yy = 30
    for a in arms:
        lab = P.arm_label(pap, a) + (f" ({w['reference']})" if a == control else "")
        cap = descs.get(a)
        h = _box_h(lab, col_w, cap, kicker=None)
        arm_y.append(yy)
        arm_hs.append(h)
        yy += h + 14
    total = max(yy, pop_h + 60)
    mid = total / 2
    body.append(_box(x_pop, mid - pop_h / 2, 230, pop_h, pop_txt, kicker="Population", cls="box"))
    if w["causal"]:
        body.append(f'<polygon class="box" points="{x_rand},{mid - 28} {x_rand + 40},{mid} {x_rand},{mid + 28} {x_rand - 40},{mid}"/>')
        body.append(f'<text class="cap" x="{x_rand}" y="{mid + 4}" text-anchor="middle">R</text>')
        body.append(_arrow(x_pop + 230, mid, x_rand - 42, mid, label="randomized"))
        src_x = x_rand + 42
    else:
        body.append(_arrow(x_pop + 230, mid, x_arm - 4, mid, assoc=True, label="observed"))
        src_x = None
    for a, y0, h in zip(arms, arm_y, arm_hs):
        lab = P.arm_label(pap, a) + (f" ({w['reference']})" if a == control else "")
        body.append(_box(x_arm, y0, col_w, h, lab, descs.get(a), cls="box" if a == control else "arm", gid=f"arm-{P.arm_str(a)}",
                         data_text=(descs.get(a) or lab)))
        if src_x is not None:
            body.append(_arrow(src_x, mid, x_arm - 4, y0 + h / 2))
    outs, y_end = _outcome_boxes(pap, x_out, 30, 230)
    body.append(outs)
    body.append(_arrow(x_arm + col_w, mid, x_out - 4, mid, assoc=not w["causal"], label=w["relation"]))
    mods = list(dict.fromkeys(s.get("moderator") for s in (pap.get("registered") or {}).get("subgroups", []) if s.get("moderator")))
    height = max(total, y_end) + 10
    if mods:
        body.append(f'<text class="cap" x="{x_arm}" y="{height + 14}">{'Planned' if P.registration_status(pap) == 'none' else 'Registered'} moderators: {_esc(", ".join(mods))}</text>')
        height += 30
    return _svg("\n".join(body), int(height) + 10, title, desc)


def _render_conjoint(pap, w, title, desc) -> str:
    d = pap.get("design") or {}
    feats = d.get("features") or []
    body, y = [f'<text class="kicker" x="20" y="16">{_esc(title)}</text>'], 30
    label_w, chip_h, x0 = 230, 26, 20
    for f in feats:
        lab = f.get("label", f.get("column"))
        rand = f.get("randomized", True)
        body.append(f'<text x="{x0}" y="{y + 18}">{_esc(lab)}</text>')
        body.append(f'<text class="cap" x="{x0}" y="{y + 34}">{"randomized" if rand else "measured"}</text>')
        x = x0 + label_w
        for lvl in f.get("levels", [])[:8]:
            cw = int(max(60, min(170, 9 * len(str(lvl)) + 20)))
            if x + cw > 700:
                break
            body.append(_box(x, y, cw, chip_h, str(lvl), cls="arm" if rand else "soft", gid=f"arm-{P.arm_str(f.get('column'))}-{P.arm_str(lvl)}",
                             data_text=f"{lab}: {lvl}"))
            x += cw + 8
        y += chip_h + 22
    mid = (30 + y) / 2
    task_h = 70
    body.append(_box(720, mid - task_h / 2, 110, task_h, "Two profiles per task", "choose one", cls="box"))
    body.append(_arrow(700, mid, 716, mid))
    outs, y_end = _outcome_boxes(pap, 850, 30, 100)
    body.append(outs)
    body.append(_arrow(830, mid, 846, mid, label=w["estimate"]))
    return _svg("\n".join(body), int(max(y, y_end)) + 20, title, desc)


def _render_methods(pap, w, title, desc) -> str:
    d = pap.get("design") or {}
    methods = d.get("methods") or ["method"]
    body, y = [f'<text class="kicker" x="20" y="16">{_esc(title)}</text>'], 30
    for m in methods[:6]:
        h = _box_h(m, 300, kicker="Method")
        body.append(_box(20, y, 300, h, m, cls="arm", gid=f"arm-{re.sub(r'[^A-Za-z0-9_]+', '_', str(m))}", data_text=m, kicker="Method"))
        y += h + 12
    mid = (30 + y) / 2
    bench = d.get("benchmark") or "benchmark"
    bh = _box_h(bench, 260, kicker="Benchmark")
    body.append(_box(400, mid - bh / 2, 260, bh, bench, cls="box", kicker="Benchmark"))
    body.append(_arrow(320, mid, 396, mid, label=w["relation"]))
    metrics = ", ".join(d.get("metrics") or []) or "metrics"
    mh = _box_h(metrics, 230, kicker="Metrics")
    body.append(_box(710, mid - mh / 2, 230, mh, metrics, cls="box", kicker="Metrics"))
    body.append(_arrow(660, mid, 706, mid))
    return _svg("\n".join(body), int(max(y, mid + bh / 2, mid + mh / 2)) + 20, title, desc)


def _render_observational(pap, w, title, desc) -> str:
    hyps = (pap.get("implemented") or {}).get("hypotheses", [])
    exposures = list(dict.fromkeys((h.get("treatment") or {}).get("column") for h in hyps if (h.get("treatment") or {}).get("column")))
    covs = sorted({c for h in hyps for c in (h.get("estimator") or {}).get("covariates") or []})
    body = [f'<text class="kicker" x="20" y="16">{_esc(title)}</text>']
    ex = ", ".join(exposures) or "exposure"
    eh = _box_h(ex, 280, kicker="Exposure")
    body.append(_box(20, 40, 280, eh, ex, cls="arm", gid="arm-exposure", data_text=ex, kicker="Exposure"))
    outs, y_end = _outcome_boxes(pap, 620, 40, 300)
    body.append(outs)
    body.append(_arrow(300, 40 + eh / 2, 616, 40 + eh / 2, assoc=True, label=w["relation"]))
    y = max(40 + eh, y_end) + 30
    cv = ", ".join(covs) or "none"
    ch = _box_h(cv, 440, kicker="Adjusted for")
    body.append(_box(250, y, 440, ch, cv, cls="soft", kicker="Adjusted for"))
    body.append(_arrow(300, y, 160, 40 + eh + 2))
    body.append(_arrow(640, y, 760, y_end - 8 if y_end > 60 else 60))
    return _svg("\n".join(body), int(y + ch) + 20, title, desc)


# ---- extensions --------------------------------------------------------------------------------------------------

def describe_extension(p: dict) -> str:
    d = p.get("design") or {}
    if p.get("kind") == "observational":
        return (f"{p.get('title', '')}. {d.get('units', '')} Exposure: {d.get('exposure', '')} Outcome: {d.get('outcome', '')} "
                f"Identification: {d.get('identification', '')}").strip()
    conds = [c.get("title") for c in d.get("conditions") or []]
    return (f"{p.get('title', '')}. {len(conds)} conditions: {', '.join(conds)}. Primary outcome: {d.get('primaryOutcome', '')}. "
            f"Population: {d.get('population', '')}").strip()


def render_extension(p: dict) -> str | None:
    d = p.get("design") or {}
    desc = describe_extension(p)
    title = f"Proposed follow-up: {p.get('title', '')}"
    body = [f'<text class="kicker" x="20" y="16">{_esc(title)}</text>']
    if p.get("kind") == "observational":
        ex, out = d.get("exposure") or "exposure", d.get("outcome") or "outcome"
        eh, oh = _box_h(ex, 300, kicker="Exposure"), _box_h(out, 300, kicker="Outcome")
        body.append(_box(20, 40, 300, eh, ex, cls="arm", gid="arm-exposure", data_text=ex, kicker="Exposure"))
        body.append(_box(640, 40, 300, oh, out, cls="box", kicker="Outcome"))
        body.append(_arrow(320, 40 + eh / 2, 636, 40 + oh / 2, assoc=True, label="association"))
        ds = "; ".join(d.get("data_sources") or []) or "data sources"
        y = max(eh, oh) + 60
        dh = _box_h(ds, 600, kicker="Data")
        body.append(_box(180, y, 600, dh, ds, cls="soft", kicker="Data"))
        return _svg("\n".join(body), int(y + dh) + 20, title, desc)
    conds = d.get("conditions") or []
    if len(conds) < 2:
        return None
    y, hs = 30, []
    for c in conds[:6]:
        h = _box_h(c.get("title", ""), 300, c.get("description"))
        hs.append((y, h))
        y += h + 12
    mid = (30 + y) / 2
    body.append(_box(20, mid - 30, 200, 60, d.get("population") or "sample", kicker="Population"))
    body.append(f'<polygon class="box" points="{280},{mid - 24} {316},{mid} {280},{mid + 24} {244},{mid}"/>')
    body.append(_arrow(220, mid, 242, mid, label="randomized"))
    for c, (y0, h) in zip(conds, hs):
        body.append(_box(360, y0, 300, h, c.get("title", ""), c.get("description"), cls="arm", gid=f"arm-{P.arm_str(c.get('id'))}",
                         data_text=c.get("description") or c.get("title", "")))
        body.append(_arrow(318, mid, 356, y0 + h / 2))
    out = d.get("primaryOutcome") or "primary outcome"
    oh = _box_h(out, 240, kicker="Primary outcome")
    body.append(_box(700, mid - oh / 2, 240, oh, out, cls="box", kicker="Primary outcome"))
    body.append(_arrow(660, mid, 696, mid))
    return _svg("\n".join(body), int(max(y, mid + oh / 2)) + 20, title, desc)


# ---- sanitizer for model-authored SVG ----------------------------------------------------------------------------

def sanitize(svg: str) -> str | None:
    """Keep only drawing elements; drop scripts, foreign objects, event handlers and external references. None when
    the input is not an SVG document or is too large."""
    if not svg or len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        return None
    m = re.search(r"<svg\b.*</svg>", svg, re.S)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    if local(root.tag) != "svg":
        return None

    def clean(el):
        for child in list(el):
            if local(child.tag) not in ALLOWED_TAGS:
                el.remove(child)
                continue
            clean(child)
        for k in list(el.attrib):
            kl = local(k).lower()
            v = el.attrib[k]
            if kl.startswith("on") or (kl == "href" and not v.startswith("#")) or "javascript:" in v.lower() or "url(http" in v.lower():
                del el.attrib[k]

    clean(root)
    if "{http://www.w3.org/2000/svg}" in root.tag or root.tag == "svg":
        root.set("xmlns", "http://www.w3.org/2000/svg")
    out = ET.tostring(root, encoding="unicode")
    out = out.replace("ns0:", "").replace(":ns0", "")
    return out if "<script" not in out.lower() else None


# ---- files -------------------------------------------------------------------------------------------------------

def write_study_diagram(study: Path, pap: dict, meta: dict | None = None, fallback=None) -> dict:
    """figures/design.svg + figures/design.txt. `fallback(description)` may return a model-authored SVG string when the
    template has nothing to draw; it is sanitized. Returns {"svg", "text", "source"} with paths relative to the study."""
    fig = study / "figures"
    fig.mkdir(exist_ok=True)
    text = describe(pap, meta)
    svg, source = render(pap, meta), "template"
    if svg is None and fallback is not None:
        try:
            svg = sanitize(fallback(text) or "")
        except Exception:
            svg = None
        source = "model" if svg else None
    (fig / "design.txt").write_text(text + "\n", encoding="utf-8")
    if svg:
        (fig / "design.svg").write_text(svg, encoding="utf-8")
        return {"svg": "figures/design.svg", "text": "figures/design.txt", "source": source}
    return {"svg": None, "text": "figures/design.txt", "source": None}


def write_extension_diagrams(study: Path, rows: list[dict]) -> list[dict]:
    """extensions/<id>.svg + <id>.txt for every proposal in extensions/; adds "svg" and "text" to each index row."""
    import json
    d = study / "extensions"
    for r in rows:
        pj = d / f"{r['id']}.json"
        if not pj.exists():
            continue
        p = json.loads(pj.read_text(encoding="utf-8"))
        text = describe_extension(p)
        (d / f"{r['id']}.txt").write_text(text + "\n", encoding="utf-8")
        r["text"] = f"extensions/{r['id']}.txt"
        svg = render_extension(p)
        if svg:
            (d / f"{r['id']}.svg").write_text(svg, encoding="utf-8")
            r["svg"] = f"extensions/{r['id']}.svg"
        else:
            r["svg"] = None
    if d.exists():
        (d / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return rows
