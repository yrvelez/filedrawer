"""Configuration: config.yaml (+ config.local.yaml overrides) + environment.

The OpenRouter key is only ever read from the environment (OPENROUTER_API_KEY).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PKG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parent.parent

DEFAULTS: dict[str, Any] = {
    "provider": "openrouter",
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "zero_data_retention": True,
        "check_models_on_start": True,
        "timeout_s": 120,
        "reasoning": {"effort": "low"},
    },
    "models": {"strong": "anthropic/claude-sonnet-5.5", "fast": "qwen/qwen3.8-27b"},
    # provider "local": an OpenAI-compatible server on this machine (Ollama by default); these models replace `models`
    "local": {"base_url": "http://localhost:11434/v1", "timeout_s": 3600, "check_models_on_start": True, "reasoning_effort": "low",
              "models": {"strong": "qwen3:32b", "fast": "qwen3:8b"}},
    "analysis": {"language": "python", "script_timeout_s": 300},
    "budgets": {
        "papreader": {"turns": 3, "max_tokens": 8000, "tier": "strong"},
        "cleaner": {"turns": 6, "max_tokens": 16000, "tier": "fast"},
        "registered": {"turns": 4, "max_tokens": 16000, "tier": "fast"},
        "exploratory": {"turns": 10, "max_tokens": 16000, "tier": "fast"},
        "litreview": {"turns": 2, "max_tokens": 8000, "tier": "fast"},
        "writer": {"turns": 2, "max_tokens": 12000, "tier": "strong"},
        "reviewer": {"turns": 1, "max_tokens": 4000, "tier": "strong"},
        "responder": {"turns": 2, "max_tokens": 4000, "tier": "strong"},
        "extensions": {"turns": 1, "max_tokens": 16000, "tier": "strong"},
        "review_orchestrator": {"turns": 1, "max_tokens": 8000, "tier": "strong"},
        "advanced": {"turns": 1, "max_tokens": 6000, "tier": "fast"},
        "review_import": {"turns": 1, "max_tokens": 8000, "tier": "fast"},
    },
    "advanced": {"prompts": None},
    "coarse": {"command": None, "model": None, "timeout_s": 3600},
    "litreview": {"enabled": True, "mailto": "", "max_works": 10},
    "pii": {"allow_pii": False, "keep": [], "model": False},
    "extensions": {"autoexperiment_dir": ""},
    "report": {"exploratory_suffix": "(EXPLORATORY — not pre-registered)"},
}


def _deep_update(base: dict, new: dict) -> dict:
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict[str, Any]:
    """Merge DEFAULTS <- config.yaml <- config.local.yaml <- explicit path <- overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    candidates = [REPO_ROOT / "config.yaml", REPO_ROOT / "config.local.yaml",
                  Path.cwd() / "config.yaml", Path.cwd() / "config.local.yaml",
                  Path.cwd() / "filedrawer.setup.yaml"]                     # the author's choices (filedrawer configure)
    if path:
        candidates.append(Path(path))
    seen = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not rp.exists():
            continue
        seen.add(rp)
        with open(rp, encoding="utf-8") as fh:
            _deep_update(cfg, yaml.safe_load(fh) or {})
    if overrides:
        _deep_update(cfg, overrides)
    cfg["openrouter"]["api_key"] = os.environ.get("OPENROUTER_API_KEY", "")
    if cfg.get("provider") == "local":                # local runs use the local model names for every tier
        cfg["models"] = dict((cfg.get("local") or {}).get("models") or cfg["models"])
    return cfg


def budget(cfg: dict, agent: str) -> dict:
    b = dict(DEFAULTS["budgets"].get(agent, {"turns": 2, "max_tokens": 3000, "tier": "fast"}))
    b.update(cfg.get("budgets", {}).get(agent, {}))
    b["model"] = cfg["models"][b["tier"]]
    return b
