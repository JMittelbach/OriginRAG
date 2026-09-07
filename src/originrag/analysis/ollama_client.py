from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from originrag.paths import load_project_env


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:30b"


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
) -> OllamaResult:
    """Generate text with a local Ollama model.

    This function never calls cloud APIs. Failures are returned explicitly in
    `error` and are not silently swallowed.
    """



    if model is None or base_url is None:
        load_project_env()
    resolved_base_url = (
        base_url
        or env_value("PFEMPIRE_OLLAMA_BASE_URL", "HYPORAG_OLLAMA_BASE_URL")
        or DEFAULT_BASE_URL
    )
    resolved_model = model or env_value("PFEMPIRE_OLLAMA_MODEL", "HYPORAG_OLLAMA_MODEL") or DEFAULT_MODEL
    response_prefix = ""
    use_forced_qwen_answer = bool(
        resolved_model.startswith("qwen3") and str(assistant_prefix or "").strip()
    )
    if use_forced_qwen_answer:
        response_prefix = str(assistant_prefix).strip() + "\n"
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

    text = strip_qwen_thinking_trace(
        response_prefix + str(response.get("response") or "")
    )
    metadata = {
        key: value
        for key, value in response.items()
        if key not in {"response", "context"}
    }
    return OllamaResult(
        text=text or None,
        model=resolved_model,
        base_url=resolved_base_url,
        call_performed=True,
        error=None if text else "ollama_returned_empty_response",
        raw_response=response,
        metadata=metadata,
    )


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
    load_project_env()
    return env_value(
        "PFEMPIRE_OLLAMA_PLANNER_MODEL",
        "HYPORAG_OLLAMA_PLANNER_MODEL",
        "PFEMPIRE_OLLAMA_MODEL",
        "HYPORAG_OLLAMA_MODEL",
    ) or default


def answer_model(default: str = DEFAULT_MODEL) -> str:
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


def qwen_raw_chat_prompt(prompt: str, assistant_prefix: str) -> str:
    """Start Qwen directly in the final-answer channel, bypassing its think template."""
    return (
        "<|im_start|>user\n"
        + str(prompt or "")
        + "<|im_end|>\n<|im_start|>assistant\n"
        + assistant_prefix
    )


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
