from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
MAX_RESPONSE_BYTES = 2_000_000


class DiscoveryBusy(RuntimeError):
    pass


class DiscoveryUnavailable(RuntimeError):
    pass


def search_topic(query: str, *, since: str = "", order: str = "relevance", kind: str = "journal-article", limit: int = 12) -> dict:
    query = " ".join(str(query).split())
    if not 2 <= len(query) <= 240:
        raise ValueError("Enter a topic between 2 and 240 characters.")
    if order not in {"relevance", "newest"}:
        raise ValueError("Choose relevance or newest.")
    if kind not in {"journal-article", "proceedings-article", "posted-content", "all"}:
        raise ValueError("Choose a supported publication type.")
    if since and (not re.fullmatch(r"[0-9]{4}", since) or not 1800 <= int(since) <= date.today().year):
        raise ValueError("Choose a starting year between 1800 and the current year.")
    if not 1 <= limit <= 20:
        raise ValueError("Choose between 1 and 20 results.")
    params = {
        "query": query,
        "rows": str(limit),
        "sort": "published" if order == "newest" else "relevance",
        "order": "desc",
        "select": "DOI,title,author,published,container-title,type",
        "filter": f"until-pub-date:{date.today().isoformat()}",
    }
    if since:
        params["filter"] += f",from-pub-date:{since}-01-01"
    if kind != "all":
        params["filter"] += f",type:{kind}"
    request = Request(
        CROSSREF_WORKS_URL + "?" + urlencode(params),
        headers={"User-Agent": "OriginRAG/0.1 (scientific literature discovery)", "Accept": "application/json"},
    )
    try:
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
    items = []
    seen = set()
    for work in works[:limit]:
        if not isinstance(work, dict):
            continue
        doi = str(work.get("DOI") or "").strip().lower()
        if not re.fullmatch(r"10\.\d{4,9}/[^\s<>]+", doi) or doi in seen:
            continue
        title = _first_text(work.get("title"))
        if not title:
            continue
        authors = work.get("author") or []
        author = next((str(a.get("family") or a.get("name") or "") for a in authors if isinstance(a, dict)), "") if isinstance(authors, list) else ""
        published = work.get("published") or {}
        dates = published.get("date-parts") or [] if isinstance(published, dict) else []
        year = str(dates[0][0]) if dates and isinstance(dates[0], list) and dates[0] else ""
        seen.add(doi)
        items.append({
            "title": title, "author": author[:200], "year": year[:4],
            "journal": _first_text(work.get("container-title")),
            "doi": doi, "url": "https://doi.org/" + quote(doi, safe="/"),
            "type": str(work.get("type") or "")[:80],
        })
    return {"items": items, "query": query, "source": "Crossref", "downloaded": False}


def _first_text(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0])[:1000]
    return ""


class TopicDiscovery:
    def __init__(self) -> None:
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.last_requests: OrderedDict[str, float] = OrderedDict()

    def search(self, owner: str, **kwargs) -> dict:
        with self.lock:
            now = time.monotonic()
            if now - self.last_requests.get(owner, -float("inf")) < 2:
                raise DiscoveryBusy("Please wait a moment before searching again.")
            self.last_requests[owner] = now
            self.last_requests.move_to_end(owner)
            while len(self.last_requests) > 128:
                self.last_requests.popitem(last=False)
        if not self.slots.acquire(blocking=False):
            raise DiscoveryBusy("Two literature searches are already running. Please try again shortly.")
        try:
            return search_topic(**kwargs)
        finally:
            self.slots.release()
