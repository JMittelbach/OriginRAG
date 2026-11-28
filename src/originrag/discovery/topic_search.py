from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from html.parser import HTMLParser
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from originrag.cancellation import cancellable_lock, checkpoint


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
MAX_RESPONSE_BYTES = 2_000_000
_CROSSREF_INTERVAL = 1.05
_crossref_lock = threading.Lock()
_crossref_last_start = -float('inf')


class DiscoveryBusy(RuntimeError):
    pass


class DiscoveryUnavailable(RuntimeError):
    pass


def valid_issn(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{3}[0-9X]", value):
        return False
    digits = value.replace("-", "")
    return sum((10 if digit == "X" else int(digit)) * (8 - index) for index, digit in enumerate(digits)) % 11 == 0


def search_topic(query: str, *, since: str = "", order: str = "relevance", kind: str = "journal-article", limit: int = 12, issns=(), offset: int = 0, match_topic: bool = False, title_query: str = '') -> dict:
    if not isinstance(issns, (list, tuple)) or len(issns) > 20 or any(not isinstance(i, str) or not valid_issn(i) for i in issns):
        raise ValueError("Choose up to 20 valid journal ISSNs.")
    issns = sorted(set(issns))
    query = " ".join(str(query).split())
    if not (2 <= len(query) <= 240 or (not query and issns)):
        raise ValueError("Enter a topic between 2 and 240 characters.")
    if order not in {"relevance", "newest"}:
        raise ValueError("Choose relevance or newest.")
    if kind not in {"journal-article", "proceedings-article", "posted-content", "all"}:
        raise ValueError("Choose a supported publication type.")
    if since and (not re.fullmatch(r"[0-9]{4}", since) or not 1800 <= int(since) <= date.today().year):
        raise ValueError("Choose a starting year between 1800 and the current year.")
    if not 1 <= limit <= 20:
        raise ValueError("Choose between 1 and 20 results.")
    if not isinstance(offset, int) or not 0 <= offset <= 200:
        raise ValueError("Narrow the topic to continue searching.")
    if not isinstance(title_query, str) or len(title_query) > 140:
        raise ValueError('The title topic is too long.')
    params = {
        "query": query,
        "rows": str(20 if match_topic else limit),
        "sort": "published" if order == "newest" or not query else "relevance",
        "order": "desc",
        "select": "DOI,title,author,published,container-title,type,ISSN,abstract",
        "filter": f"until-pub-date:{date.today().isoformat()}",
    }
    if title_query:
        params['query.title'] = title_query
    if offset:
        params["offset"] = str(offset)
    if since:
        params["filter"] += f",from-pub-date:{since}-01-01"
    if kind != "all":
        params["filter"] += f",type:{kind}"
    for issn in issns:
        params["filter"] += f",issn:{issn}"
    works = _request_items(CROSSREF_WORKS_URL, params)
    items = []
    seen = set()
    consumed = 0
    for work in works[:20 if match_topic else limit]:
        consumed += 1
        if not isinstance(work, dict):
            continue
        work_issns = work.get("ISSN") or []
        if issns and (not isinstance(work_issns, list) or not any(isinstance(i, str) and i in issns for i in work_issns)):
            continue
        doi = str(work.get("DOI") or "").strip().lower()
        if not re.fullmatch(r"10\.\d{4,9}/[^\s<>\"?#]+", doi) or doi in seen:
            continue
        title = abstract_text(_first_text(work.get("title")))
        if not title or re.match(r'(?:retraction|correction|erratum|corrigendum)\b', title, re.I):
            continue
        abstract = abstract_text(work.get("abstract"))
        if match_topic and not topic_in_metadata(query, title + " " + abstract):
            continue
        authors = work.get("author") or []
        author = next((str(a.get("family") or a.get("name") or "") for a in authors if isinstance(a, dict)), "") if isinstance(authors, list) else ""
        published = work.get("published") or {}
        dates = published.get("date-parts") or [] if isinstance(published, dict) else []
        year = str(dates[0][0]) if dates and isinstance(dates[0], list) and dates[0] else ""
        if since and (not year.isdigit() or int(year) < int(since)):
            continue
        seen.add(doi)
        items.append({
            "title": title, "author": author[:200], "year": year[:4],
            "journal": _first_text(work.get("container-title")),
            "doi": doi, "url": "https://doi.org/" + quote(doi, safe="/"),
            "type": str(work.get("type") or "")[:80],
            "abstract": abstract,
        })
        if len(items) >= limit:
            break
    return {"items": items, "query": query, "source": "Crossref", "downloaded": False, "issns": issns,
            "next_offset": offset + consumed, "metadata_screened": match_topic}


def topic_in_metadata(query: str, text: str) -> bool:
    """Conservative keyword check, not a full-text relevance judgement."""
    stop = set("a an the in on at of for to and or with about research study studies paper papers methods method using use that which are is zu zur zum über und der die das ein eine von mit".split())
    words = lambda value: {word[:-1] if len(word) > 4 and word.endswith('s') else word
                           for word in re.findall(r"[^\W_]+", value.casefold()) if word not in stop}
    # Exclusion and disjunction queries need semantic review; do not treat them as AND.
    if re.search(r"\b(?:not|without|except|exclude|excluding|or|nicht|ohne|oder)\b", query, re.I):
        return True
    terms = words(query)
    return bool(terms) and terms.issubset(words(text))


def abstract_text(value: object) -> str:
    if not isinstance(value, str):
        return ""

    class PlainText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.hidden = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden += 1
            if tag.split(":")[-1] in {"p", "title", "br", "sec"}:
                self.parts.append(" ")

        def handle_endtag(self, tag):
            if tag in {"script", "style"}:
                self.hidden = max(0, self.hidden - 1)
            if tag.split(":")[-1] in {"p", "title", "sec"}:
                self.parts.append(" ")

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    parser = PlainText()
    parser.feed(value[:40000])
    text = " ".join("".join(parser.parts).split())
    return text[:8000] + ("…" if len(text) > 8000 else "")


def _request_items(endpoint: str, params: dict) -> list:
    global _crossref_last_start
    request = Request(
        endpoint + "?" + urlencode(params),
        headers={"User-Agent": "OriginRAG/0.1 (scientific literature discovery)", "Accept": "application/json"},
    )
    try:
        with cancellable_lock(_crossref_lock):
            while time.monotonic() - _crossref_last_start < _CROSSREF_INTERVAL:
                checkpoint()
                time.sleep(max(0, min(.1, _CROSSREF_INTERVAL - (time.monotonic() - _crossref_last_start))))
            _crossref_last_start = time.monotonic()
            with urlopen(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        payload = json.loads(raw)
        works = payload["message"]["items"]
        if not isinstance(works, list):
            raise ValueError("invalid response")
    except Exception as exc:
        raise DiscoveryUnavailable("The literature service is unavailable. Please try again shortly.") from exc
    return works


def search_journals(query: str) -> dict:
    query = " ".join(str(query).split())
    if not 2 <= len(query) <= 120:
        raise ValueError("Enter a journal name or ISSN between 2 and 120 characters.")
    records = _request_items("https://api.crossref.org/journals", {"query": query, "rows": "20"})
    items = []
    seen = set()
    for record in records[:20]:
        if not isinstance(record, dict):
            continue
        raw_issns = record.get("ISSN") or []
        if not isinstance(raw_issns, list):
            continue
        issns = sorted({i.upper() for i in raw_issns if isinstance(i, str) and valid_issn(i.upper())})[:4]
        title = str(record.get("title") or "")[:500]
        key = tuple(issns)
        if not issns or not title or key in seen:
            continue
        seen.add(key)
        items.append({"title": title, "publisher": str(record.get("publisher") or "")[:200], "issns": issns})
    key = query.casefold()
    items.sort(key=lambda item: (item["title"].casefold() != key and query.upper() not in item["issns"],
                                 not item["title"].casefold().startswith(key)))
    return {"items": items[:12], "source": "Crossref"}


def _first_text(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0])[:1000]
    return ""


class TopicDiscovery:
    def __init__(self) -> None:
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.last_requests: OrderedDict[str, float] = OrderedDict()
        self.journal_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def search(self, owner: str, **kwargs) -> dict:
        return self._run(owner, search_topic, 2, **kwargs)

    def research(self, owner: str, plan, issns=()) -> dict:
        from originrag.discovery.chat_engine import collect
        return self._run(owner, collect, 2, plan=plan, issns=issns)

    def journals(self, owner: str, query: str) -> dict:
        key = " ".join(query.split()).casefold()
        with self.lock:
            cached = self.journal_cache.get(key)
            if cached and time.monotonic() - cached[0] < 3600:
                self.journal_cache.move_to_end(key)
                return cached[1]
        result = self._run("journals:" + owner, search_journals, .4, query=query)
        with self.lock:
            self.journal_cache[key] = (time.monotonic(), result)
            self.journal_cache.move_to_end(key)
            while len(self.journal_cache) > 128:
                self.journal_cache.popitem(last=False)
        return result

    def _run(self, owner, function, interval, **kwargs) -> dict:
        with self.lock:
            now = time.monotonic()
            if now - self.last_requests.get(owner, -float("inf")) < interval:
                raise DiscoveryBusy("Please wait a moment before searching again.")
            self.last_requests[owner] = now
            self.last_requests.move_to_end(owner)
            while len(self.last_requests) > 128:
                self.last_requests.popitem(last=False)
        if not self.slots.acquire(blocking=False):
            raise DiscoveryBusy("Two literature searches are already running. Please try again shortly.")
        try:
            return function(**kwargs)
        finally:
            self.slots.release()
