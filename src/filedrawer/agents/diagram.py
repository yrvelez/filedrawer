"""Model-authored design diagram, used only when the template renderer has nothing to draw (a non-standard flow,
or design.nonstandard: true in the plan). One fast-tier call; the output is sanitized before it is kept."""
from __future__ import annotations

import json

from .base import make_agent
from .. import diagram as D

SYSTEM = """You draw one clear SVG diagram of a study design for a research report. You get the design as JSON and a one-paragraph description. Return ONLY the SVG document, nothing else.

Rules: a standalone <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 H" width="100%" role="img"> with a <title> and a <desc> (the description you were given). Use only rect, line, path, polygon, circle, text, tspan, g, defs, marker and style. No script, no foreignObject, no images, no external references. Colours only through CSS variables with fallbacks, declared in one <style> block exactly like this and nothing else: :root{--fd-ink:#1c1b19;--fd-muted:#8a877f;--fd-rule:#d9d5cc;--fd-accent:#8b1e2d;--fd-bg:#ffffff} @media (prefers-color-scheme: dark){:root{--fd-ink:#ecebe6;--fd-muted:#b4b1a9;--fd-rule:#4a4a52;--fd-accent:#e08a96;--fd-bg:#1d1d23}}. Text in Inter, Helvetica, Arial, sans-serif, 14 px for labels and 12 px for captions, fill var(--fd-ink) or var(--fd-muted). Wrap each arm, condition, wave or level in <g id="arm-<short id>" data-text="one sentence about it">. Read left to right: who is studied, what varies and how it is assigned (a diamond marked R for random assignment; a dashed arrow labelled "observed" when nothing is randomized), what is measured, in what order or wave. Keep it under 40 KB and legible at half size: few words per box, no paragraphs."""


def run(ctx: dict, design: dict, description: str) -> str | None:
    agent = make_agent("diagram", ctx, SYSTEM, None)
    agent.max_turns = 1
    res = agent.run(f"## Design (JSON)\n{json.dumps(design, ensure_ascii=False)[:12000]}\n\n## Description\n{description}")
    return D.sanitize(res.content or "")
