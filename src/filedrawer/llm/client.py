"""LLM provider protocol, OpenRouter implementation, request logging and usage accounting."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0, "cost": 0.0})
    model: str = ""
    finish_reason: str = ""

    @property
    def empty(self) -> bool:
        return not (self.content or "").strip() and not self.tool_calls

    def to_message(self) -> dict:
        m: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            m["tool_calls"] = [{"id": tc.id, "type": "function",
                                "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                               for tc in self.tool_calls]
        return m


class Usage:
    def __init__(self):
        self.by_agent: dict[str, dict] = {}

    def add(self, agent: str, usage: dict, model: str = ""):
        u = self.by_agent.setdefault(agent, {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "models": []})
        u["calls"] += 1
        u["in"] += int(usage.get("in", 0) or 0)
        u["out"] += int(usage.get("out", 0) or 0)
        u["cost"] += float(usage.get("cost", 0.0) or 0.0)
        if model and model not in u["models"]:
            u["models"].append(model)

    def totals(self) -> dict:
        t = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
        for u in self.by_agent.values():
            for k in t:
                t[k] += u[k]
        t["cost"] = round(t["cost"], 6)
        return t


class RequestLog:
    """Append-only JSONL of every request/response. By design it never contains raw data,
    which the canary test verifies."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        if not self.path:
            return
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class Provider(Protocol):
    name: str
    usage: Usage
    log: RequestLog

    def chat(self, *, agent: str, messages: list[dict], model: str, max_tokens: int,
             tools: list[dict] | None = None, temperature: float = 0.0,
             meta: dict | None = None) -> LLMResponse: ...


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1",
                 zero_data_retention: bool = True, timeout_s: int = 120,
                 log_path: str | Path | None = None, app_title: str = "filedrawer",
                 reasoning: dict | None = None):
        import httpx
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY is not set. Export it (bring your own token) or use --provider mock.")
        self.base_url = base_url.rstrip("/")
        self.zdr = zero_data_retention
        self.reasoning = reasoning or None
        self.client = httpx.Client(timeout=timeout_s, headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/yrvelez/filedrawer",
            "X-Title": app_title,
        })
        self.usage = Usage()
        self.log = RequestLog(log_path)

    def check_models(self, slugs: list[str]) -> None:
        r = self.client.get(f"{self.base_url}/models")
        r.raise_for_status()
        ids = {m.get("id") for m in r.json().get("data", [])}
        missing = [s for s in slugs if s not in ids]
        if missing:
            raise SystemExit(f"Model slug(s) not found on OpenRouter: {missing}. "
                             f"Fix models.strong / models.fast in config.yaml (see https://openrouter.ai/models).")

    def chat(self, *, agent, messages, model, max_tokens, tools=None, temperature=0.0, meta=None) -> LLMResponse:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                                "temperature": temperature, "usage": {"include": True}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.zdr:
            body["provider"] = {"data_collection": "deny"}
        if self.reasoning:
            body["reasoning"] = self.reasoning
        body.update(getattr(self, "extra_body", None) or {})
        last_err = None
        for attempt in range(3):
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:  # network errors
                last_err = str(e)
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"{self.name} request failed after retries: {last_err}")
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append(ToolCall(id=tc.get("id", f"call_{len(calls)}"), name=fn.get("name", ""), args=args))
        u = data.get("usage", {}) or {}
        usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0), "cost": u.get("cost", 0.0)}
        finish = data["choices"][0].get("finish_reason") or data["choices"][0].get("native_finish_reason") or ""
        resp = LLMResponse(content=msg.get("content") or "", tool_calls=calls, usage=usage, model=data.get("model", model),
                           finish_reason=finish)
        self.usage.add(agent, usage, resp.model)
        self.log.write({"agent": agent, "model": model, "meta": meta, "request": body,
                        "response": {"content": resp.content, "tool_calls": [tc.__dict__ for tc in calls], "usage": usage,
                                     "finish_reason": finish}})
        return resp


class LocalProvider(OpenRouterProvider):
    """An OpenAI-compatible model server on this machine (Ollama, LM Studio, llama.cpp, vLLM). No API key, no
    network beyond localhost: nothing leaves the machine. Costs are zero; tokens are still counted."""
    name = "local"

    def __init__(self, base_url: str = "http://localhost:11434/v1", timeout_s: int = 3600,
                 log_path: str | Path | None = None, reasoning_effort: str | None = "low"):
        import httpx
        self.base_url = base_url.rstrip("/")
        self.zdr, self.reasoning = False, None
        # reasoning models (Qwen 3.x) otherwise spend the output budget thinking; LM Studio and Ollama honour this
        self.extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        self.client = httpx.Client(timeout=timeout_s)
        self.usage = Usage()
        self.log = RequestLog(log_path)

    def check_models(self, slugs: list[str]) -> None:
        try:
            r = self.client.get(f"{self.base_url}/models")
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"No local model server at {self.base_url} ({type(e).__name__}). Start one, e.g. `ollama serve`, "
                             f"then `ollama pull {slugs[0] if slugs else 'qwen3:32b'}`; or set local.base_url in config.yaml "
                             f"(LM Studio: http://localhost:1234/v1).")
        ids = {m.get("id") for m in r.json().get("data", [])}
        missing = [s for s in slugs if s not in ids]
        if missing:
            raise SystemExit(f"Local model(s) not available: {missing}. Pull them first, e.g. `ollama pull {missing[0]}` "
                             f"(available: {', '.join(sorted(i for i in ids if i)) or 'none'}).")


def make_provider(cfg: dict, log_path: str | Path | None, fixtures_dir: str | Path | None = None):
    if cfg.get("provider") == "mock":
        from .mock import MockProvider
        return MockProvider(fixtures_dir, log_path=log_path)
    if cfg.get("provider") == "local":
        lc = cfg.get("local") or {}
        p = LocalProvider(base_url=lc.get("base_url", "http://localhost:11434/v1"), timeout_s=lc.get("timeout_s", 3600),
                          log_path=log_path, reasoning_effort=lc.get("reasoning_effort", "low"))
        if lc.get("check_models_on_start", True):
            p.check_models(sorted(set(cfg["models"].values())))
        return p
    o = cfg["openrouter"]
    p = OpenRouterProvider(api_key=o.get("api_key") or os.environ.get("OPENROUTER_API_KEY", ""),
                           base_url=o.get("base_url", "https://openrouter.ai/api/v1"),
                           zero_data_retention=o.get("zero_data_retention", True),
                           timeout_s=o.get("timeout_s", 120), log_path=log_path,
                           reasoning=o.get("reasoning"))
    if o.get("check_models_on_start", True):
        p.check_models(sorted(set(cfg["models"].values())))
    return p
