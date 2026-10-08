"""Hand-rolled tool-use loop with turn and output-token budgets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import Provider
from ..tools import ToolRegistry


# Agents whose failure degrades the package instead of stopping the run.
OPTIONAL_AGENTS = {"litreview", "exploratory",
                   # review add-ons and follow-ups: an empty answer is logged as a gap, the draft and the other reviews stand
                   "advanced_methodology", "advanced_statistics", "review_orchestrator", "review_signoff", "writer_revise",
                   "responder", "potential", "extensions", "extensions_obs"}


class EmptyResponseError(SystemExit):
    def __init__(self, agent: str, model: str, max_tokens: int, finish_reason: str, provider: str = ""):
        why = ("it used the whole output budget (finish_reason=length), most likely on hidden reasoning"
               if finish_reason == "length" else f"finish_reason={finish_reason or 'unknown'}")
        fix = (f"Fix: use a local model that supports tool calling and long outputs (the default qwen3:32b / qwen3:8b; "
               f"`ollama pull qwen3:32b`), or raise budgets.{agent}.max_tokens." if provider == "local" else
               f"Fix: raise budgets.{agent}.max_tokens, set openrouter.reasoning: {{effort: low}}, "
               f"or switch that tier to another model in config.yaml.")
        super().__init__(f"Agent '{agent}' got an empty response from {model} twice (last budget {max_tokens} tokens): {why}. {fix}")


@dataclass
class AgentResult:
    content: str = ""
    records: dict[str, Any] = field(default_factory=dict)
    turns: int = 0
    stopped: str = "done"          # done | final | max_turns | error | empty
    error: str = ""
    transcript: list[dict] = field(default_factory=list)


class Agent:
    def __init__(self, name: str, provider: Provider, model: str, system: str,
                 tools: ToolRegistry | None, max_turns: int = 4, max_tokens: int = 4000,
                 temperature: float = 0.0):
        self.name, self.provider, self.model, self.system = name, provider, model, system
        self.tools, self.max_turns, self.max_tokens, self.temperature = tools, max_turns, max_tokens, temperature
        self.finish_turn = False        # reserve the last turn for record_result (agents whose partial work is usable)

    def run(self, task: str, meta: dict | None = None) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system}, {"role": "user", "content": task}]
        res = AgentResult()
        schemas = self.tools.schemas() if self.tools else None
        finish = bool(self.finish_turn and self.tools and "record_result" in self.tools.allowed and self.max_turns > 1)
        for turn in range(self.max_turns):
            res.turns = turn + 1
            if finish and turn == self.max_turns - 1 and not self.tools.final:
                # Last turn: only record_result is offered, so work done so far is handed over instead of lost.
                schemas = [s for s in schemas if s["function"]["name"] == "record_result"]
                messages.append({"role": "user", "content": "This is your last turn. Call record_result now with final=true, "
                                 "listing only the results that succeeded."})
            try:
                resp = self.provider.chat(agent=self.name, messages=messages, model=self.model,
                                          max_tokens=self.max_tokens, tools=schemas,
                                          temperature=self.temperature, meta=meta)
            except RuntimeError as e:            # a timeout or a dead server: optional agents degrade, the rest stop the run
                if self.name not in OPTIONAL_AGENTS:
                    raise
                res.stopped, res.error = "error", f"{self.name}: {e}"
                break
            if getattr(resp, "empty", False) and turn == 0 and (
                    getattr(resp, "finish_reason", "") == "length" or getattr(self.provider, "name", "") != "mock"):
                # Reasoning models can spend the whole output budget thinking and return nothing.
                # Retry once with double the budget before giving up.
                resp = self.provider.chat(agent=self.name, messages=messages, model=self.model,
                                          max_tokens=self.max_tokens * 2, tools=schemas,
                                          temperature=self.temperature, meta=meta)
                if getattr(resp, "empty", False):
                    err = EmptyResponseError(self.name, self.model, self.max_tokens * 2,
                                             getattr(resp, "finish_reason", ""), getattr(self.provider, "name", ""))
                    if self.name not in OPTIONAL_AGENTS:
                        raise err
                    res.stopped, res.error = "empty", str(err)
                    break
            messages.append(resp.to_message())
            res.content = resp.content or res.content
            if not resp.tool_calls or not self.tools:
                res.stopped = "done"
                break
            for tc in resp.tool_calls:
                out = self.tools.dispatch(tc.name, tc.args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": out})
            left = self.max_turns - turn - 1
            if finish and messages[-1]["role"] == "tool" and 0 < left <= 3:
                messages[-1]["content"] += f"\n[{left} turn{'s' if left > 1 else ''} left]"
            if self.tools.final:
                res.stopped = "final"
                break
        else:
            res.stopped = "max_turns"
        if self.tools:
            res.records = dict(self.tools.records)
        res.transcript = messages
        return res
