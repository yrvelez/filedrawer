"""R backend stub. The MVP generates Python; this records the interface an R backend must fill."""
from __future__ import annotations

from pathlib import Path


def rscript_command(script: Path) -> list[str]:
    raise NotImplementedError(
        "analysis.language = 'r' is not implemented in this version. Set analysis.language to "
        "'python', or implement rscript_command() and R script templates in analysis/templates.py."
    )
