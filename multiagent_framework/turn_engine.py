"""Reusable bounded multi-round tool-calling engine for simulator agents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .llm_client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]
TerminalPredicate = Callable[[str, dict[str, Any]], bool]


@dataclass(frozen=True)
class TurnBudget:
    """Hard limits for one logical agent turn."""

    max_rounds: int = 4
    max_tool_calls: int = 12
    max_tokens: int = 12_000
    max_seconds: float = 60.0
    no_progress_rounds: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "max_tool_calls", max(1, int(self.max_tool_calls)))
        object.__setattr__(self, "max_tokens", max(1, int(self.max_tokens)))
        object.__setattr__(self, "max_seconds", max(0.1, float(self.max_seconds)))
        object.__setattr__(self, "no_progress_rounds", max(1, int(self.no_progress_rounds)))


@dataclass
class TurnOutcome:
    """Result of a bounded multi-round conversation."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    terminal_tool: str = ""
    terminal_result: dict[str, Any] | None = None
    error: str | None = None
    rounds: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float = 0.0
    model_latency_ms: float = 0.0
    last_response: LLMResponse | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def completed(self) -> bool:
        return bool(self.terminal_tool)


class TurnEngine:
    """Run one traceable tool-calling turn under strict resource budgets.

    The engine is deliberately policy-free: callers provide tools, an executor,
    and the predicate that marks a tool result as terminal.  The same trace_id,
    turn_id, chain_id, and agent metadata are reused for every LLM round.
    """

    async def run(
        self,
        *,
        llm: LLMClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        terminal_predicate: TerminalPredicate,
        trace_context: Mapping[str, Any] | None = None,
        budget: TurnBudget | None = None,
        temperature: float = 0.7,
    ) -> TurnOutcome:
        budget = budget or TurnBudget()
        conversation = list(messages)
        outcome = TurnOutcome(messages=conversation)
        started = time.monotonic()
        deadline = started + budget.max_seconds
        previous_signature = ""
        stagnant_rounds = 0

        for round_idx in range(budget.max_rounds):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome.stop_reason = "time_budget"
                break

            round_context = dict(trace_context or {})
            round_context["round"] = round_idx

            try:
                response = await asyncio.wait_for(
                    llm.chat(
                        messages=conversation,
                        tools=tools,
                        temperature=temperature,
                        trace_context=round_context,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                outcome.stop_reason = "time_budget"
                outcome.error = "turn exceeded time budget"
                break
            except Exception as exc:
                logger.error("TurnEngine LLM call failed at round %d: %s", round_idx, exc)
                outcome.stop_reason = "llm_error"
                outcome.error = str(exc)
                break

            outcome.last_response = response
            outcome.rounds += 1
            outcome.input_tokens += max(0, int(response.input_tokens or 0))
            outcome.output_tokens += max(0, int(response.output_tokens or 0))
            outcome.model_latency_ms += max(0.0, float(response.latency_ms or 0.0))

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": tc.get("function", {}),
                    }
                    for tc in response.tool_calls
                ]
            conversation.append(assistant_message)

            if not response.tool_calls:
                outcome.trace.append({
                    "round": round_idx,
                    "assistant_content": response.content or "",
                    "calls": [],
                    "stop_reason": "no_tool_calls",
                })
                outcome.stop_reason = "no_tool_calls"
                break

            round_calls: list[dict[str, Any]] = []
            terminal_hit = False
            tool_budget_hit = False

            for tool_call in response.tool_calls:
                function = tool_call.get("function", {})
                name = str(function.get("name", "") or "")
                raw_arguments = function.get("arguments", "{}") or "{}"
                invalid_arguments = False
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    arguments = {}
                    invalid_arguments = True
                    tool_result: dict[str, Any] = {
                        "ok": False,
                        "accepted": False,
                        "error": f"invalid tool arguments: {exc}",
                    }

                if outcome.tool_calls >= budget.max_tool_calls:
                    tool_budget_hit = True
                    tool_result = {
                        "ok": False,
                        "accepted": False,
                        "error": "turn tool-call budget exhausted",
                    }
                elif not invalid_arguments:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        outcome.stop_reason = "time_budget"
                        tool_result = {
                            "ok": False,
                            "accepted": False,
                            "error": "turn time budget exhausted",
                        }
                    else:
                        try:
                            tool_result = await asyncio.wait_for(
                                execute_tool(name, arguments, tool_call),
                                timeout=remaining,
                            )
                        except asyncio.TimeoutError:
                            outcome.stop_reason = "time_budget"
                            tool_result = {
                                "ok": False,
                                "accepted": False,
                                "error": "tool execution exceeded turn time budget",
                            }
                        except Exception as exc:
                            logger.exception("TurnEngine tool %s failed: %s", name, exc)
                            tool_result = {
                                "ok": False,
                                "accepted": False,
                                "error": str(exc),
                            }
                    outcome.tool_calls += 1

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })
                round_calls.append({
                    "tool": name,
                    "args": arguments,
                    "result": tool_result,
                })

                if terminal_predicate(name, tool_result):
                    outcome.terminal_tool = name
                    outcome.terminal_result = tool_result
                    terminal_hit = True

            signature = json.dumps(
                {
                    "content": response.content or "",
                    "calls": round_calls,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if signature == previous_signature:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0
                previous_signature = signature

            round_stop = ""
            if terminal_hit:
                round_stop = "terminal_tool"
            elif outcome.stop_reason == "time_budget":
                round_stop = "time_budget"
            elif tool_budget_hit or outcome.tool_calls >= budget.max_tool_calls:
                round_stop = "max_tool_calls"
            elif outcome.total_tokens >= budget.max_tokens:
                round_stop = "token_budget"
            elif stagnant_rounds >= budget.no_progress_rounds:
                round_stop = "no_progress"

            trace_entry = {
                "round": round_idx,
                "assistant_content": response.content or "",
                "calls": round_calls,
            }
            if round_stop:
                trace_entry["stop_reason"] = round_stop
            outcome.trace.append(trace_entry)

            if round_stop:
                outcome.stop_reason = round_stop
                break
        else:
            outcome.stop_reason = "max_rounds"

        if not outcome.stop_reason:
            outcome.stop_reason = "max_rounds"
        outcome.elapsed_ms = (time.monotonic() - started) * 1000.0
        return outcome
