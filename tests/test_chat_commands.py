import pytest

from originrag.analysis.chat_commands import reading_selection, ReadingSelection
from originrag.analysis.chat import resolve_chat_task
from originrag.analysis.chat import model_requests_bibliography
from originrag.summaries.reading_card import summary_request
from originrag.library_presence import presence_language
from originrag.retrieval.quick_list import more_list_request, paper_list_request


@pytest.mark.parametrize("text,expected", [
    ("Five more", ("en", 5)), ("Show me 7 more papers, please", ("en", 7)),
    ("Zeig mir fünf weitere", ("de", 5)), ("Noch mehr!", ("de", None)),
    ("Dame dos más", ("es", 2)), ("Trois autres articles", ("fr", 3)),
    ("Gib mir noch fünf", ("de", 5)), ("Another 12 papers", ("en", 12)),
    ("Tell me more", None), ("five more recent papers", None),
    ("Find more papers on a different topic", None), ("give me 0 more", None),
])
def test_more_only_means_pagination_for_a_standalone_request(text, expected):
    assert more_list_request(text) == expected


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


@pytest.mark.parametrize("message,since", [
    ("Show me the six newest papers on microscopy", 0),
    ("Nenn mir die sechs neuesten Paper zu microscopy", 0),
    ("Find six papers on microscopy since 2022", 2022),
    ("Find six papers on microscopy after 2022", 2023),
    ("Gib mir sechs Studien zu microscopy nach 2022", 2023),
])
def test_temporal_lists_keep_the_topic_separate(message, since):
    req = paper_list_request(message)
    assert req and (req.newest, req.since, req.limit, req.topic) == (True, since, 6, "microscopy")


def test_paper_identifier_is_not_the_deliverable():
    doi = "10.1234/test"
    assert resolve_chat_task(f"Which clinical groups were compared in {doi}?") == "answer"
    assert resolve_chat_task(f"Welche Methode wurde im Artikel {doi} verwendet?") == "answer"
    assert resolve_chat_task(f"Find the paper with DOI {doi}") == "sources"
    assert not paper_list_request("Show me six recent papers on microscopy and tell me which method they used")


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
