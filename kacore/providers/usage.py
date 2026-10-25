"""供应商调用的用量记账（每次真实 HTTP 尝试记一次，不保存 prompt 或密钥）。"""

from __future__ import annotations

import time

from kacore.domain.llm_generation import GenerationResult
from kacore.managers.token_budget import default_tokenizer
from kacore.utils.usage_ledger import current_usage, usage_stage


def record_llm_usage(
    result: GenerationResult | None,
    system: str,
    prompt: str,
    provider_id: str,
    started: float,
    status: str,
) -> None:
    """result 为 None 表示这次尝试失败：消费记为 unknown，绝不当作零。"""
    ledger = current_usage.get()
    if ledger is None:
        return
    counter = default_tokenizer()
    known = result is not None
    actual = known and result.prompt_tokens is not None and result.completion_tokens is not None
    unknown_output = known and result.reasoning_present and result.completion_tokens is None
    if known:
        input_tokens = (
            result.prompt_tokens
            if result.prompt_tokens is not None
            else counter.count(system + prompt)
        )
        output_tokens = (
            None
            if unknown_output
            else result.completion_tokens
            if result.completion_tokens is not None
            else counter.count(result.text)
        )
    else:
        input_tokens = output_tokens = None
    ledger.calls.append({
        "call_id": f"{ledger.request_id}:{len(ledger.calls) + 1}",
        "stage": usage_stage.get(),
        "attempt": len(ledger.calls) + 1,
        "provider": provider_id,
        "model": result.model if known else "",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "measurement": (
            "unknown" if unknown_output or not known else "actual" if actual else "estimated"
        ),
        "status": status,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    })


__all__ = ["record_llm_usage"]
