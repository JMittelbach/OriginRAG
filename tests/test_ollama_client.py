from __future__ import annotations

from originrag.analysis import ollama_client


def test_optional_keep_alive_does_not_change_existing_call_defaults(monkeypatch):
    requests = []
    def post(url, payload, timeout):
        requests.append(payload)
        return {'response': 'ok'}
    monkeypatch.setattr(ollama_client, 'post_json', post)
    ollama_client.call_ollama_generate('Test', model='qwen3:32b', keep_alive='60s')
    ollama_client.call_ollama_generate('Test', model='qwen3:32b')
    assert requests[0]['keep_alive'] == '60s'
    assert 'keep_alive' not in requests[1]


def test_qwen_structured_calls_disable_thinking_explicitly(monkeypatch) -> None:
    captured = {}

    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        captured.update(payload)
        assert timeout == 30
        return {"response": '{"ok": true}'}

    monkeypatch.setattr(ollama_client, "post_json", fake_post)
    result = ollama_client.generate_text(
        "Return JSON",
        model="qwen3:30b",
        timeout=30,
        format="json",
    )

    assert captured["think"] is False
    assert captured["format"] == "json"
    assert result.text == '{"ok": true}'


def test_non_qwen_call_does_not_force_thinking_option(monkeypatch) -> None:
    captured = {}

    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        captured.update(payload)
        return {"response": "ok"}

    monkeypatch.setattr(ollama_client, "post_json", fake_post)
    ollama_client.generate_text("Answer", model="llama3.1:8b")

    assert "think" not in captured


def test_qwen_thinking_trace_is_removed_even_when_server_ignores_flag(monkeypatch) -> None:
    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        return {"response": "<think>private reasoning</think>\nSupported answer."}

    monkeypatch.setattr(ollama_client, "post_json", fake_post)

    result = ollama_client.generate_text("Answer", model="qwen3:30b")

    assert result.text == "Supported answer."


def test_orphan_qwen_thinking_close_discards_reasoning_prefix(monkeypatch) -> None:
    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        return {"response": "private reasoning</think>\nSupported answer."}

    monkeypatch.setattr(ollama_client, "post_json", fake_post)

    result = ollama_client.generate_text("Answer", model="qwen3:30b")

    assert result.text == "Supported answer."


def test_qwen_assistant_prefix_uses_raw_final_answer_channel(monkeypatch) -> None:
    captured = {}

    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        captured.update(payload)
        return {"response": "Supported answer."}

    monkeypatch.setattr(ollama_client, "post_json", fake_post)
    result = ollama_client.generate_text(
        "Use the required format.",
        model="qwen3:30b",
        assistant_prefix="## Evidence-backed statements",
    )

    assert captured["raw"] is True
    assert captured["prompt"].endswith(
        "<|im_start|>assistant\n## Evidence-backed statements\n"
    )
    assert captured["options"]["stop"] == ["<|im_end|>", "<|im_start|>"]
    assert result.text == "## Evidence-backed statements\nSupported answer."


def test_forced_qwen_answer_fails_closed_on_unterminated_thinking_trace(monkeypatch) -> None:
    def fake_post(_url: str, payload: dict, timeout: int) -> dict:
        return {
            "response": (
                "<think>private reasoning without a closing tag\n"
                "## Cautious inference\nUnsafe answer."
            )
        }

    monkeypatch.setattr(ollama_client, "post_json", fake_post)

    result = ollama_client.generate_text(
        "Use the required format.",
        model="qwen3:30b",
        assistant_prefix="## Evidence-backed statements",
    )

    assert result.text is None
    assert result.error == "ollama_returned_empty_response"
