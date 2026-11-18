"""Answer simple visibility questions from the catalogue, not from a model."""

import re
from originrag.analysis.chat_commands import controls


_PAPER_DE = r"(?:(?:mein(?:e[ns]?)?|das|den|die|ein(?:en)?|dieses?) )?(?:hochgeladene[nrs]? )?(?:paper[s]?|artikel|publikation(?:en)?|dokument[e]?)"
_PLACE_DE = r"(?:(?:hier )?(?:(?:im|in (?:der|dem|meine[mr]|deine[mr])) (?:korpus|corpus|bibliothek|library)))"
_PAPER_EN = r"(?:(?:my|the|this|any|a) )?(?:uploaded )?(?:papers?|articles?|documents?)"
_PLACE_EN = r"in (?:my|your|the) (?:library|corpus|catalogue|catalog)"


def presence_language(message: str) -> str | None:
    raw = " ".join(message.casefold().strip(" \t\r\n.!?").split())
    return _match_presence(raw) or (_match_presence(controls(raw)) if len(raw) <= 180 else None)


def _match_presence(text: str) -> str | None:
    if re.fullmatch(r"(?:puedes ver|ves) (?:mi|mis|el|los) (?:artículos?|articulos?|papers?)(?: en (?:mi|la) biblioteca)?", text):
        return "es"
    if re.fullmatch(r"(?:vois-tu|tu vois|peux-tu voir) (?:mon|mes|les) (?:articles?|papers?)(?: dans ma bibliothèque)?", text):
        return "fr"
    if re.fullmatch(rf"(?:siehst du (?:hier )?{_PAPER_DE}(?: {_PLACE_DE}|,? (?:das|was|den) ich (?:gerade )?hochgeladen habe)?"
                    rf"|kannst du (?:hier )?{_PAPER_DE}(?: {_PLACE_DE})? sehen"
                    rf"|hast du zugriff auf {_PAPER_DE}(?: {_PLACE_DE})?"
                    rf"|(?:ist|sind) {_PAPER_DE} {_PLACE_DE})", text):
        return "de"
    if re.fullmatch(rf"(?:(?:can|do) you see {_PAPER_EN}(?: here| {_PLACE_EN}| (?:that )?i (?:just )?uploaded)?"
                    rf"|(?:is|are) {_PAPER_EN} {_PLACE_EN})", text):
        return "en"
    return None


def presence_answer(catalogue, language: str) -> str:
    de = language == "de"
    if language in {"fr", "es"}:
        fr = language == "fr"
        if not catalogue:
            return "Je ne vois aucun article dans le catalogue." if fr else "No veo ningún artículo en el catálogo."
        if len(catalogue) == 1:
            paper = catalogue[0]
            state = ("Le texte intégral est consultable." if paper["searchable"] else "Le texte intégral n’est pas encore indexé.") if fr else (
                "El texto completo está indexado." if paper["searchable"] else "El texto completo aún no está indexado.")
            return (f"Oui : « {paper['title']} ». " if fr else f"Sí: «{paper['title']}». ") + state
        total, ready = len(catalogue), sum(bool(p["searchable"]) for p in catalogue)
        return (f"Je vois {total} articles, dont {ready} avec texte intégral indexé. De quel titre s’agit-il ?" if fr else
                f"Veo {total} artículos, {ready} con texto completo indexado. ¿A qué título te refieres?")
    if not catalogue:
        return ("Ich sehe noch kein Paper im Katalog. Über Add papers kannst du ein PDF hinzufügen." if de else
                "I don’t see a paper in the catalogue yet. Use Add papers to import a PDF.")
    if len(catalogue) == 1:
        paper = catalogue[0]
        title = paper["title"]
        if de:
            state = "Der Volltext ist durchsuchbar." if paper["searchable"] else "Der Volltext ist noch nicht durchsuchbar."
            return f"Ja, ich sehe ein Paper im Katalog: „{title}“. {state}"
        state = "Its full text is searchable." if paper["searchable"] else "Its full text is not searchable yet."
        return f"Yes, I see one paper in the catalogue: “{title}”. {state}"
    total = len(catalogue)
    ready = sum(bool(p["searchable"]) for p in catalogue)
    return (f"Ich sehe {total} Paper im Katalog. Bei {ready} davon ist der Volltext durchsuchbar. Welchen Titel meinst du?" if de else
            f"I see {total} papers in the catalogue, with searchable full text for {ready}. Which title do you mean?")
