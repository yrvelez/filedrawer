"""Outside reviewers, in one place. The File Drawer is reviewer-agnostic: any referee report, from a person or a model,
comes back through `filedrawer review-import <study> <source> FILE` and is structured into review.json issues the
same way. Adding a reviewer is one entry here, plus the matching entry in REVIEWERS in docs/index.html (a test keeps
the two sets in step). Order matters here: review modes are sorted by it, so add new reviewers at the end.

Fields: label (shown to people), prefix (issue ids: ED1, ED2, ...), home (the reviewer's site), link (an optional
deep link that opens this study's report in the reviewer, with {report_url} = the report's GitHub page, URL-encoded),
local (run on the author's machine; coarse can also run inside the pipeline), and how (one line on getting the review).
"""
from __future__ import annotations

REVIEWERS: dict[str, dict] = {
    "coarse": {
        "label": "Coarse", "prefix": "C", "home": "https://github.com/Davidvandijcke/coarse", "local": True,
        "how": ("run the open-source reviewer on your machine (about $1-2, your OpenRouter key): "
                "uvx --python 3.12 coarse-ink review report.md, which writes report_review.md"),
    },
    "refine": {
        "label": "Refine", "prefix": "RF", "home": "https://www.refine.ink",
        "how": "upload report.md (or a PDF of it) at refine.ink, wait for the review, and save it (PDF, Markdown or text)",
    },
    "openaireview": {
        "label": "OpenAIReview", "prefix": "OAR", "home": "https://github.com/ChicagoHAI/OpenAIReview",
        "cmd": "uvx openaireview review report.md",
        "how": ("run ChicagoHAI's open-source reviewer on your machine with your own model key (OpenRouter, OpenAI, "
                "Anthropic or Gemini; the cost depends on the model you pick): uvx openaireview review report.md, which "
                "writes its comments to review_results/"),
    },
    "edyth": {
        "label": "Edyth", "prefix": "ED", "home": "https://edyth.org",
        "link": "https://edyth.org/import?url={report_url}&code=FILEDRAWER",
        "how": ("open the report in Edyth (the Check with Edyth button on the study's filedrawer.org page): with the code FILEDRAWER the "
                "reference check and reading suggestions are free, and an optional deep review is paid from your own "
                "Edyth balance; save the Markdown letter it returns"),
    },
    "other": {
        "label": "Any other referee report", "prefix": "X",
        "how": "save any referee report, from a person or a model (PDF, Markdown or text)",
    },
}

SOURCES: tuple[str, ...] = tuple(REVIEWERS)
# reviews the pipeline never runs itself: they stay "pending" in review.json until imported
IMPORTED_ONLY: tuple[str, ...] = tuple(k for k, v in REVIEWERS.items() if not v.get("local"))


def import_command(source: str, study: str = ".") -> str:
    return f"filedrawer review-import {study} {source} FILE"


def steps(source: str, study: str = ".") -> str:
    """How to get a review from this reviewer and bring it back, for review.md and the report."""
    r = REVIEWERS[source]
    return f"1. {r['how'][0].upper() + r['how'][1:]}.\n2. Run: {import_command(source, study)}"
