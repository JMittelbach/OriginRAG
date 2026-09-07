from pfempire.analysis.chat import ChatTurnContext, answer_context, build_chat_retrieval_question, conversation_prompt_block
from pfempire.analysis.prompts import build_answer_repair_prompt, build_citation_answer_prompt


def test_recent_context_reaches_initial_and_repair_prompts_not_evidence_ids():
    history = answer_context([ChatTurnContext("Earlier question", answer_summary="Earlier answer (Old et al.; E999)",
                                             source_titles=("Earlier paper",))])
    pack = {"chunks": [{"evidence_id": "E001", "citation_label": "Current et al.", "text": "Current evidence"}],
            "conversation_context": history}
    initial = build_citation_answer_prompt("Explain that", pack)
    repair = build_answer_repair_prompt("Explain that", pack, "answer", "Draft", {})
    for prompt in (initial, repair):
        assert "Earlier question" in prompt
        assert "Earlier answer" in prompt
        assert "not evidence" in prompt
        assert "CURRENT evidence pack" in prompt
        assert "E999" not in prompt
        assert "Current evidence" in prompt
        assert "E001" in prompt
    assert pack["chunks"] == [{"evidence_id": "E001", "citation_label": "Current et al.", "text": "Current evidence"}]


def test_context_is_bounded_and_older_turns_are_dropped():
    history = [{"user": f"QUESTION-{n}: " + "q" * 5000, "assistant": "a" * 20_000, "papers": "p" * 5000}
               for n in range(20)]
    block = conversation_prompt_block(history)
    assert "QUESTION-19:" in block
    assert "QUESTION-0:" not in block
    assert len(block) < 7000
    assert conversation_prompt_block([]) == ""
    assert conversation_prompt_block("invalid") == ""


def test_retrieval_followups_do_not_recursively_embed_previous_histories():
    turns = []
    for _ in range(20):
        query = build_chat_retrieval_question("Why?", turns)
        turns.append(ChatTurnContext("Why?", retrieval_question=query, answer_summary="Short answer"))
    assert len(query) < 1000
