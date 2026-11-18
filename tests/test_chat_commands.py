import pytest

from originrag.analysis.chat_commands import reading_selection, ReadingSelection
from originrag.analysis.chat import resolve_chat_task
from originrag.analysis.chat import model_requests_bibliography
from originrag.summaries.reading_card import summary_request
from originrag.library_presence import presence_language
from originrag.retrieval.quick_list import paper_list_request


@pytest.mark.parametrize("message,topic,n,lang", [
    ("Hey, could u pls show me 3 papres about ABC-123", "ABC-123", 3, "en"),
    ("Ey, zeig mir drei Papre über Rate-X", "Rate-X", 3, "de"),
    ("Montre-moi trois articles sur LAMP2", "LAMP2", 3, "fr"),
    ("¿Dame dos artículos sobre Paper-01?", "Paper-01", 2, "es"),
    ("gimme 2 papers on papres", "papres", 2, "en"),
])
def test_only_the_command_is_corrected(message, topic, n, lang):
    req = paper_list_request(message)
    assert req and (req.topic, req.limit, req.language) == (topic, n, lang)
    assert resolve_chat_task(message) == "sources"


@pytest.mark.parametrize("text,result", [
    ("Hey, summraize paper #12", ReadingSelection(12, "en")),
    ("Nee, den dritten!", ReadingSelection(3, "de", True)),
    ("No, the 4th one", ReadingSelection(4, "en", True)),
    ("Résume le troisième article", ReadingSelection(3, "fr")),
    ("Resume el segundo artículo", ReadingSelection(2, "es")),
    ("Don't summarize the third paper", None),
    ("Summarize paper #2 and compare it with #4", None),
    ("Was the third paper replicated?", None),
])
def test_reference_is_not_a_scientific_question(text, result):
    assert reading_selection(text) == result


@pytest.mark.parametrize("text", ["Dame dos artículos sobre sensores y dime quién los financió",
    "Montre-moi trois articles sur la calibration puis explique leurs résultats",
    "Hey, give me ten papers on sensors and tell me who funded them"])
def test_mixed_request_is_not_just_a_list(text):
    assert not paper_list_request(text)
    assert resolve_chat_task(text) == "answer"


def test_typo_in_presence_check_does_not_swallow_content_questions():
    assert presence_language("Hey, can you see my papre?") == "en"
    assert presence_language("¿Puedes ver mi artículo?") == "es"
    assert presence_language("Tu vois mon article ?") == "fr"
    assert presence_language("Can you see my papre's conclusions?") is None


@pytest.mark.parametrize("question,label,expected", [
    ("What publications are available on temperature monitoring?", "paper_list", True),
    ("Which sensors were used in studies to measure temperature?", "paper_list", False),
    ("Which papers about sensors are available, and tell me who funded them?", "paper_list", False),
    ("Which articles address temperature?", "answer", False),
])
def test_model_label_requires_agreement_with_requested_output(question,label,expected):
    assert model_requests_bibliography(dict(status="ready", normalized_question=question, deliverable=label)) == expected


@pytest.mark.parametrize("q,target,lang", [
    ("Hey, could u pls summraize this paper: 10.1234/ABC-X.1", "10.1234/ABC-X.1", "en"),
    ("Résume l’article intitulé « Les papres X-12 »", "Les papres X-12", "fr"),
    ("Resume el artículo titulado «GIVe-19»", "GIVe-19", "es"),
])
def test_named_reading_does_not_correct_the_title(q,target,lang):
    assert summary_request(q) == (target,lang)
