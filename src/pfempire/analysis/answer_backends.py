from __future__ import annotations

from dataclasses import dataclass

from pfempire.analysis.llm_backends import LLMResult
from pfempire.analysis.ollama_client import (
    DEFAULT_BASE_URL,
    answer_model,
    call_ollama_generate,
    env_value,
)


SUPPORTED_ANSWER_BACKENDS = {"none", "ollama", "qwen", "deepseek"}
DEFAULT_ANSWER_NUM_CTX = 65536
DEFAULT_ANSWER_NUM_PREDICT = 2200


@dataclass
class AnswerBackendResult:
    llm_result: LLMResult
    backend_requested: str
    backend_used: str
    model: str | None
    call_performed: bool
    blocked: bool = False
    block_reason: str | None = None


def generate_answer_with_backend(
    prompt: str,
    *,
    backend: str = "none",
    model: str | None = None,
    temperature: float = 0.0,
    timeout: int = 120,
    num_predict: int | None = None,
    seed: int | None = None,
    num_ctx: int | None = None,
    base_url: str | None = None,
    evidence_is_sufficient: bool = True,
    force_answer: bool = False,
) -> AnswerBackendResult:
    requested = (backend or "none").strip().lower()
    if requested not in SUPPORTED_ANSWER_BACKENDS:
        return AnswerBackendResult(
            llm_result=LLMResult(
                backend=requested,
                answer=None,
                error=(
                    f"Unsupported answer backend: {backend}. Supported: "
                    "none, ollama, qwen, deepseek."
                ),
            ),
            backend_requested=requested,
            backend_used="none",
            model=model,
            call_performed=False,
        )
    if requested == "none":
        return AnswerBackendResult(
            llm_result=LLMResult(backend="none", answer=None),
            backend_requested=requested,
            backend_used="none",
            model=model,
            call_performed=False,
        )
    resolved_model = model or model_for_backend(requested)
    if not evidence_is_sufficient and not force_answer:
        return AnswerBackendResult(
            llm_result=LLMResult(
                backend="none",
                answer=None,
                error="answer backend blocked because evidence grade is below threshold",
            ),
            backend_requested=requested,
            backend_used="none",
            model=resolved_model,
            call_performed=False,
            blocked=True,
            block_reason="insufficient_evidence",
        )
    result = call_ollama_generate(
        prompt,
        model=resolved_model,
        base_url=base_url or base_url_for_backend(requested),
        temperature=temperature,
        timeout=timeout,



        num_predict=answer_num_predict(num_predict),
        seed=seed,






        num_ctx=answer_num_ctx(num_ctx),
        assistant_prefix=required_answer_prefix(prompt, resolved_model),
    )
    return AnswerBackendResult(
        llm_result=LLMResult(
            backend=requested,
            answer=result.text,
            error=result.error,
            usage=ollama_usage(result.metadata),
        ),
        backend_requested=requested,
        backend_used=requested,
        model=result.model,
        call_performed=result.call_performed,
    )


def required_answer_prefix(prompt: str, model: str) -> str | None:
    """Return the first mandated heading used to bypass Qwen3 thinking output."""
    if not str(model or "").startswith("qwen3"):
        return None
    for heading in (
        "## Evidence-backed statements",
        "## Short Answer",
        "## Hypothesis Card",
    ):
        if heading in str(prompt or ""):
            return heading
    return None


def ollama_usage(metadata: dict | None) -> dict[str, int] | None:
    if not metadata:
        return None
    usage = {
        key: int(metadata[key])
        for key in (
            "prompt_eval_count",
            "eval_count",
            "prompt_eval_duration",
            "eval_duration",
            "load_duration",
            "total_duration",
        )
        if metadata.get(key) is not None
    }
    return usage or None


def model_for_backend(backend: str) -> str:
    if backend == "qwen":
        return env_value("PFEMPIRE_QWEN_MODEL", "HYPORAG_QWEN_MODEL") or answer_model()
    if backend == "deepseek":
        return (
            env_value("PFEMPIRE_DEEPSEEK_MODEL", "HYPORAG_DEEPSEEK_MODEL")
            or "deepseek-r1:8b"
        )
    return answer_model()


def base_url_for_backend(backend: str) -> str:
    names = {
        "qwen": ("PFEMPIRE_QWEN_BASE_URL", "HYPORAG_QWEN_BASE_URL"),
        "deepseek": (
            "PFEMPIRE_DEEPSEEK_BASE_URL",
            "HYPORAG_DEEPSEEK_BASE_URL",
        ),
    }.get(backend, ())
    return (
        env_value(*names)
        or env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or DEFAULT_BASE_URL
    )


def answer_num_ctx(explicit: int | None = None) -> int:
    """Resolve the answer context window without falling back to Ollama's 4k default."""
    if explicit is not None:
        return max(int(explicit), 2048)
    raw_value = (
        env_value("PFEMPIRE_OLLAMA_NUM_CTX", "HYPORAG_OLLAMA_NUM_CTX")
        or str(DEFAULT_ANSWER_NUM_CTX)
    )
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_ANSWER_NUM_CTX
    return max(value, 2048)


def answer_num_predict(explicit: int | None = None) -> int:
    """Resolve the answer token budget from an override or the project environment."""
    if explicit is not None:
        return max(int(explicit), 256)
    raw_value = (
        env_value("PFEMPIRE_OLLAMA_NUM_PREDICT", "HYPORAG_OLLAMA_NUM_PREDICT")
        or str(DEFAULT_ANSWER_NUM_PREDICT)
    )
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_ANSWER_NUM_PREDICT
    return max(value, 256)
