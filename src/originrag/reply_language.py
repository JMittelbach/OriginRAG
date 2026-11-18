import re


def reply_language(question: str) -> str:
    """Choose display language independently of query understanding and retrieval."""
    text = str(question or "").casefold()
    if re.search(r"\b(?:not in german|nicht auf deutsch|auf englisch|in english)\b", text):
        return "en"
    return "de" if re.search(r"\b(?:auf deutsch|in german)\b", text) else "en"
