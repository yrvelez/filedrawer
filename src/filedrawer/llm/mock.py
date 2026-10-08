"""Deterministic offline provider for tests and dry runs.

Fixtures: <fixtures_dir>/<agent>/<turn>.json, an assistant message of the form
  {"content": "...", "tool_calls": [{"name": "write_file", "args": {"path": "...", "content": {"$file": "scripts/02_clean.py"}}}]}
`{"$file": "..."}` values are replaced by the content of that file relative to fixtures_dir,
so script bodies stay readable .py files. A missing fixture yields an empty assistant
message, which ends an agent loop.
"""
from __future__ import annotations

import json
from pathlib import Path

from .client import LLMResponse, ToolCall, Usage, RequestLog

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mock"


class MockProvider:
    name = "mock"

    def __init__(self, fixtures_dir: str | Path | None = None, log_path: str | Path | None = None):
        self.fixtures = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES
        self.turns: dict[str, int] = {}
        self.usage = Usage()
        self.log = RequestLog(log_path)

    def _resolve(self, v):
        if isinstance(v, dict) and "$file" in v:
            return (self.fixtures / v["$file"]).read_text(encoding="utf-8")
        if isinstance(v, dict) and "$json" in v:
            return json.loads((self.fixtures / v["$json"]).read_text(encoding="utf-8"))
        if isinstance(v, dict):
            return {k: self._resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._resolve(x) for x in v]
        return v

    def reset(self, agent: str | None = None):
        if agent:
            self.turns.pop(agent, None)
        else:
            self.turns.clear()

    def chat(self, *, agent, messages, model, max_tokens, tools=None, temperature=0.0, meta=None) -> LLMResponse:
        turn = self.turns.get(agent, 0)
        self.turns[agent] = turn + 1
        usage = {"in": sum(len(json.dumps(m)) // 4 for m in messages), "out": 0, "cost": 0.0}
        fx = self.fixtures / agent / f"{turn}.json"
        if fx.exists():
            d = self._resolve(json.loads(fx.read_text(encoding="utf-8")))
            calls = [ToolCall(id=f"call_{agent}_{turn}_{i}", name=tc["name"], args=tc.get("args", {}))
                     for i, tc in enumerate(d.get("tool_calls", []))]
            resp = LLMResponse(content=d.get("content", ""), tool_calls=calls, usage=usage, model="mock")
        else:
            resp = LLMResponse(content="", usage=usage, model="mock")
        usage["out"] = len(resp.content) // 4
        self.usage.add(agent, usage, "mock")
        self.log.write({"agent": agent, "model": model, "meta": meta,
                        "request": {"messages": messages, "tools": tools, "max_tokens": max_tokens},
                        "response": {"content": resp.content, "tool_calls": [tc.__dict__ for tc in resp.tool_calls]}})
        return resp
