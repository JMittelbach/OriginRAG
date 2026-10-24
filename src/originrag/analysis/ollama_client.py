from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from originrag.paths import load_project_env
from originrag.cancellation import urlopen
from originrag.model_runtime import CURRENT_RUNTIME, colibri_generate


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:30b"
MODEL_CONTROL_TOKENS = (
    "<|im_start|>", "<|im_end|>", "<|endoftext|>",
    "<|begin_of_text|>", "<|end_of_text|>", "<|start_header_id|>",
    "<|end_header_id|>", "<|eot_id|>", "<|eom_id|>",
    "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>",
    "<start_of_turn>", "<end_of_turn>",
    "<｜begin▁of▁sentence｜>", "<｜end▁of▁sentence｜>",
    "<｜User｜>", "<｜Assistant｜>",
)
MODEL_CONTROL_PATTERN = re.compile("|".join(re.escape(token) for token in MODEL_CONTROL_TOKENS))
MODEL_CONTROL_ESCAPES = str.maketrans({
    "<": r"\u003c", ">": r"\u003e", "[": r"\u005b", "]": r"\u005d",
})


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate runner-context key")
        result[key] = value
    return result


def runner_num_ctx(num_ctx: int | None, *, model: str, base_url: str) -> int | None:
    """Set a scoped default when absent, otherwise raise to an opt-in context floor."""
    raw = os.environ.get("ORIGINRAG_OLLAMA_RUNNER_CONTEXTS", "")
    if not raw or len(raw) > 8192:
        return num_ctx
    try:
        configured = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError):
        return num_ctx
    if not isinstance(configured, dict) or len(configured) > 32:
        return num_ctx
    contexts: dict[str, dict[str, int]] = {}
    model_count = 0
    for endpoint, models in configured.items():
        if not endpoint or len(endpoint) > 2048 or not isinstance(models, dict) or len(models) > 32:
            return num_ctx
        endpoint = endpoint.rstrip("/")
        if not endpoint or endpoint in contexts:
            return num_ctx
        model_count += len(models)
        if model_count > 128:
            return num_ctx
        for name, context in models.items():
            if (
                not name or len(name) > 200 or type(context) is not int
                or not 1 <= context <= 1048576
            ):
                return num_ctx
        contexts[endpoint] = models
    floor = contexts.get(base_url.rstrip("/"), {}).get(model)
    if floor is None:
        return num_ctx
    return floor if num_ctx is None else max(int(num_ctx), floor)


@dataclass
class OllamaResult:
    text: str | None
    model: str
    base_url: str
    call_performed: bool
    error: str | None = None
    raw_response: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def generate_text(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    timeout: int = 120,
    num_predict: int = 1200,
    seed: int | None = None,
    num_ctx: int | None = None,
    format: str | dict[str, Any] | None = None,
    assistant_prefix: str | None = None,
    keep_alive: str | int | None = None,
) -> OllamaResult:
    """Generate text with a local Ollama model.

    This function never calls cloud APIs. Failures are returned explicitly in
    `error` and are not silently swallowed.
    """
    prompt = escape_model_control_tokens(prompt)

    if model is None or base_url is None:
        load_project_env()
    resolved_base_url = (
        base_url
        or env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or DEFAULT_BASE_URL
    )
    resolved_model = model or env_value("PFEMPIRE_OLLAMA_MODEL", "HYPORAG_OLLAMA_MODEL") or DEFAULT_MODEL
    runtime = CURRENT_RUNTIME.get()
    if runtime:
        resolved_model = runtime["model"]
        resolved_base_url = runtime["base_url"]
        if runtime["provider"] == "colibri":
            try:
                text, metadata = colibri_generate(
                    prompt, runtime, temperature=temperature, timeout=timeout,
                    num_predict=num_predict, format=format,
                )
                return OllamaResult(text=text, model=resolved_model, base_url=resolved_base_url,
                                    call_performed=True, metadata=metadata)
            except Exception as exc:
                return error_result(resolved_model, resolved_base_url, str(exc))
    num_ctx = runner_num_ctx(num_ctx, model=resolved_model, base_url=resolved_base_url)
    response_prefix = ""
    use_forced_qwen_answer = bool(
        resolved_model.startswith("qwen3") and str(assistant_prefix or "").strip()
    )
    if use_forced_qwen_answer:
        response_prefix = escape_model_control_tokens(str(assistant_prefix).strip()) + "\n"
        prompt = qwen_raw_chat_prompt(prompt, response_prefix)
    else:
        prompt = maybe_disable_qwen_thinking(prompt, resolved_model)
    payload: dict[str, Any] = {
        "model": resolved_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": float(temperature),
            "num_predict": int(num_predict),
        },
    }
    if seed is not None:
        payload["options"]["seed"] = int(seed)
    if num_ctx is not None:
        payload["options"]["num_ctx"] = int(num_ctx)
    if use_forced_qwen_answer:
        payload["raw"] = True
        payload["options"]["stop"] = ["<|im_end|>", "<|im_start|>"]
    if resolved_model.startswith("qwen3"):
        payload["think"] = False
    if format:
        payload["format"] = format
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    try:
        response = post_json(
            f"{resolved_base_url.rstrip('/')}/api/generate",
            payload,
            timeout=max(int(timeout), 1),
        )
    except TimeoutError as exc:
        return error_result(resolved_model, resolved_base_url, f"ollama_timeout: {exc}")
    except Exception as exc:
        return error_result(resolved_model, resolved_base_url, str(exc))

    if not isinstance(response, dict):
        return error_result(
            resolved_model, resolved_base_url,
            "ollama_invalid_response: expected a JSON object",
        )
    if response.get("error"):
        detail = response["error"]
        if not isinstance(detail, str):
            detail = "backend reported an error"
        return error_result(
            resolved_model, resolved_base_url, f"ollama_backend_error: {detail[:512]}",
        )
    if "done" in response and type(response["done"]) is not bool:
        return error_result(
            resolved_model, resolved_base_url,
            "ollama_invalid_response: completion flag must be boolean",
        )
    if response.get("done") is False:
        return error_result(resolved_model, resolved_base_url, "ollama_incomplete_response")
    generated_text = response.get("response")
    if generated_text is not None and not isinstance(generated_text, str):
        return error_result(
            resolved_model, resolved_base_url,
            "ollama_invalid_response: generated text must be a string",
        )
    generated_text = strip_qwen_thinking_trace(generated_text or "")
    text = response_prefix + generated_text if generated_text else ""
    if response_prefix:
        prefix_line = response_prefix.removesuffix("\n")
        if generated_text == prefix_line:
            text = ""
        elif generated_text.startswith((response_prefix, prefix_line + "\r\n")):
            text = generated_text
    metadata = {
        key: value
        for key, value in response.items()
        if key not in {"response", "context"}
    }
    if runtime:
        duration = response.get("eval_duration")
        count = response.get("eval_count")
        if valid_usage_counter(duration) and valid_usage_counter(count) and duration > 0:
            runtime["manager"].record(runtime, count, duration / 1e9, "decode")
    return OllamaResult(
        text=text or None,
        model=resolved_model,
        base_url=resolved_base_url,
        call_performed=True,
        error=None if text else "ollama_returned_empty_response",
        raw_response=response,
        metadata=metadata,
    )


def valid_usage_counter(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def call_ollama_generate(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    timeout: int = 120,
    num_predict: int = 1200,
    seed: int | None = None,
    num_ctx: int | None = None,
    format: str | dict[str, Any] | None = None,
    assistant_prefix: str | None = None,
    keep_alive: str | int | None = None,
) -> OllamaResult:
    return generate_text(
        prompt,
        model=model,
        base_url=base_url,
        temperature=temperature,
        timeout=timeout,
        num_predict=num_predict,
        seed=seed,
        num_ctx=num_ctx,
        format=format,
        assistant_prefix=assistant_prefix,
        keep_alive=keep_alive,
    )


def check_server(base_url: str | None = None, timeout: int = 5) -> tuple[bool, str | None]:
    load_project_env()
    resolved_base_url = (
        base_url
        or env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or DEFAULT_BASE_URL
    )
    try:
        get_json(f"{resolved_base_url.rstrip('/')}/api/tags", timeout=max(int(timeout), 1))
    except Exception as exc:
        return False, str(exc)
    return True, None


def list_models(base_url: str | None = None, timeout: int = 5) -> tuple[list[str], str | None]:
    load_project_env()
    resolved_base_url = (
        base_url
        or env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or DEFAULT_BASE_URL
    )
    try:
        payload = get_json(f"{resolved_base_url.rstrip('/')}/api/tags", timeout=max(int(timeout), 1))
    except Exception as exc:
        return [], str(exc)
    models = []
    for item in payload.get("models", []):
        name = str(item.get("name") or item.get("model") or "").strip()
        if name:
            models.append(name)
    return sorted(models), None


def model_available(model: str, models: list[str]) -> bool:
    wanted = (model or "").strip()
    if not wanted:
        return False
    return wanted in models or any(name.split(":", 1)[0] == wanted for name in models)


def planner_model(default: str = DEFAULT_MODEL) -> str:
    if CURRENT_RUNTIME.get():
        return CURRENT_RUNTIME.get()["model"]
    load_project_env()
    return env_value(
        "PFEMPIRE_OLLAMA_PLANNER_MODEL",
        "HYPORAG_OLLAMA_PLANNER_MODEL",
        "PFEMPIRE_OLLAMA_MODEL",
        "HYPORAG_OLLAMA_MODEL",
    ) or default


def answer_model(default: str = DEFAULT_MODEL) -> str:
    if CURRENT_RUNTIME.get():
        return CURRENT_RUNTIME.get()["model"]
    load_project_env()
    return env_value(
        "PFEMPIRE_OLLAMA_ANSWER_MODEL",
        "HYPORAG_OLLAMA_ANSWER_MODEL",
        "PFEMPIRE_OLLAMA_MODEL",
        "HYPORAG_OLLAMA_MODEL",
    ) or default


def maybe_disable_qwen_thinking(prompt: str, model: str) -> str:
    clean = str(prompt or "")
    stripped = clean.lstrip()
    if not model.startswith("qwen3"):
        return clean
    if stripped.startswith("/no_think") or stripped.startswith("/think"):
        return clean
    return "/no_think\n" + clean


def strip_qwen_thinking_trace(text: str) -> str:
    """Remove reasoning markup that older Ollama/Qwen builds may still return.

    OriginRAG requests ``think=false`` and adds ``/no_think``, but some local
    model builds ignore one or both controls. Internal reasoning must never be
    passed to answer QC, repair prompts, logs intended as answers, or the web
    interface.
    """
    clean = str(text or "")
    clean = re.sub(
        r"<think\s*>.*?</think\s*>\s*",
        "",
        clean,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if re.search(r"</think\s*>", clean, flags=re.IGNORECASE):
        clean = re.split(r"</think\s*>\s*", clean, flags=re.IGNORECASE)[-1]



    if re.search(r"<think\s*>", clean, flags=re.IGNORECASE):
        return ""
    return clean.strip()


def escape_model_control_tokens(text: str) -> str:
    """Keep known chat delimiters as readable data, never serialized role tokens.

    This preserves ordinary HTML, mathematical notation and instruction-related
    prose. It is a serialization boundary, not a semantic injection detector.
    """
    return MODEL_CONTROL_PATTERN.sub(
        lambda match: match.group().translate(MODEL_CONTROL_ESCAPES), str(text or ""),
    )


def qwen_raw_chat_prompt(prompt: str, assistant_prefix: str) -> str:
    """Start Qwen directly in the final-answer channel, bypassing its think template."""
    return (
        "<|im_start|>user\n"
        + escape_model_control_tokens(prompt)
        + "<|im_end|>\n<|im_start|>assistant\n"
        + escape_model_control_tokens(assistant_prefix)
    )


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: int) -> dict[str, Any]:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def error_result(model: str, base_url: str, error: str) -> OllamaResult:
    return OllamaResult(
        text=None,
        model=model,
        base_url=base_url,
        call_performed=True,
        error=error,
        raw_response=None,
        metadata={},
    )


def env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None
