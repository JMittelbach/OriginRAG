"""Small, explicit literature-search commands, separate from private corpus retrieval."""

from dataclasses import asdict, dataclass, replace
from datetime import date
import re

from originrag.analysis.chat_commands import acknowledgement, controls, without_conversational_decline
from originrag.retrieval.quick_list import more_list_request
from originrag.discovery import search_plan


_PAPERS = r"(?:papers?|articles?|publications?|studies|preprints?|artikel|studien|publikationen)"
_WEB = r"(?:web|internet|online|crossref|pubmed)"
_LOCAL = r"(?:library|corpus|catalogue|catalog|bibliothek|korpus|bestand)"
_ACTION = r"(?:search|look|find|show|give|scrape|web[ -]?scrape|webscraping|suche?|finde|zeig[e]?|gib|scrape[n]?)"
_RECENT = r"(?:new|recent|latest|newest|newly published|recently published|neu[eernst]*|aktuell[eensrt]*)"
_COUNTS = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))
_COUNTS.update(zip("ein zwei drei vier fünf sechs sieben acht neun zehn".split(), range(1, 11)))


@dataclass(frozen=True)
class WebSearch:
    query: str = ""
    limit: int = 8
    since: str = ""
    order: str = "relevance"
    kind: str = "journal-article"
    offset: int = 0
    prompt: str = ""
    scope_pending: bool = False
    needs_planning: bool = False
    journal: str = ""
    journal_preference: bool = False
    next_offset: int = 0
    journal_pending: bool = False
    queries: tuple[str, ...] = ()
    journal_mode: str = "any"
    recent: bool = False
    pending_message: str = ""
    intent: str = "web_search"
    planning_error: str = ""
    subject: str = ""
    topics: tuple[str, ...] = ()
    topic_mode: str = "any"

    def public(self):
        return asdict(self)

    def parameters(self):
        return {key: value for key, value in self.public().items()
                if key in {"query", "limit", "since", "order", "kind", "offset"}}


def _previous(prior):
    data = (prior or {}).get("web_search") or {}
    if not data:
        return None
    return WebSearch(**{k: data[k] for k in WebSearch.__dataclass_fields__ if k in data})


def _literal_request(message: str, prior: dict | None = None) -> WebSearch | None:
    """Only explicit external discovery leaves the host; ordinary searches stay local.

    Reuse only a previous, already-public search topic, never a draft, paper or
    conversation summary. A missing topic is a question, not a guessed upload.
    """
    text = without_conversational_decline(" ".join(message.split())).strip(" .?!")
    text = re.sub(r"^(?:i (?:only )?meant?|ich meinte?)\s+", "", text, flags=re.I)
    command = controls(text)
    last = _previous(prior)
    if acknowledgement(text) or re.fullmatch(r"stop|cancel|nevermind|never mind|abbrechen|lass es", command, re.I):
        return None
    divider = re.search(rf"\b(?:about|covering|zu|über|ueber|on(?!\s+(?:the\s+)?{_WEB}(?:\s+for\b|\s*$)))\s+", text, re.I)
    scope = controls(text[:divider.start()]) if divider else command
    original_scope = text[:divider.start()] if divider else text
    suffix = re.search(rf"\b(?:in|on|from|im|aus|auf)\s+(?:(?:my|our|the|meiner|unserer|der|dem)\s+)?(?:{_WEB}|{_LOCAL})\b[^.!?]*$", text, re.I)
    if suffix and divider and suffix.start() > divider.end():
        scope += " " + suffix[0]
        original_scope += " " + suffix[0]
    local = bool(re.search(rf"\b{_LOCAL}\b", scope, re.I))
    outside = bool(re.search(rf"\b(?:outside|außerhalb|ausserhalb)\b.{{0,25}}\b{_LOCAL}\b", scope, re.I))
    web = outside or bool(re.search(
        r"\b(?:internet|crossref|pubmed)\b|\bwebscrap\w*\b|\bweb[ -]scrape\b"
        r"|\b(?:the|im|das|on|in|search|suche?)\s+web\b|^web$"
        r"|\bonline(?=\s*(?:$|for\b|nach\b|papers?\b|articles?\b|preprints?\b|sind\b|finden\b|available\b))", original_scope + "\n" + scope, re.I))
    negative = bool(re.search(
        rf"\b(?:no|not|never|don't|dont|do not|without|excluding|except|nicht|kein\w*|ohne)\b[^.!?;]{{0,40}}\b{_WEB}\b", scope, re.I))
    if negative or (local and not web):
        return None
    more = more_list_request(text)
    if last and last.query and not last.prompt and more:
        count = min(20, more[1] or last.limit)
        offset = last.offset if (prior or {}).get("web_search_failed") else last.next_offset or last.offset + last.limit
        return replace(last, limit=count, offset=offset, next_offset=0,
                       prompt="Please narrow the topic or year to continue this search." if offset > 200 else "")
    if last and last.query and not last.prompt and re.fullmatch(r"(?:please |bitte )?(?:retry|try again|nochmal|erneut)(?: the web search| suchen)?", command, re.I):
        return replace(last, prompt="", scope_pending=False)
    if last and last.query and not last.prompt:
        if re.fullmatch(r"(?:include|search|also search|try|auch|suche auch|durchsuche) (?:all|other|alle|andere|anderen) (?:journals|zeitschriften)(?: please| bitte)?", command, re.I):
            return replace(last, journal="", journal_mode="any", journal_preference=False, journal_pending=False, offset=0, next_offset=0)
        year_filter = re.fullmatch(r"(?:only |nur )?(?:since|from|seit|ab)\s+((?:19|20)\d{2})(?: please| bitte)?", text, re.I)
        if year_filter:
            return replace(last, since=year_filter[1], offset=0, next_offset=0,
                           prompt="That starting year is in the future. Which publication year should I search from?"
                           if int(year_filter[1]) > date.today().year else "")
        if re.fullmatch(r"(?:only |nur )?preprints?(?: only| please| bitte)?", command, re.I):
            return replace(last, kind="posted-content", offset=0, next_offset=0)
        if last.journal_pending:
            return replace(last, needs_planning=True, prompt="", offset=0, next_offset=0)
    if last and last.scope_pending and re.fullmatch(r"(?:the |im |das )?(?:web|internet|online)(?: please| bitte)?", command, re.I):
        return replace(last, prompt="" if last.query else "What topic should I search the web for?", scope_pending=False)
    if last and last.prompt and not last.scope_pending and not web and not local:
        # Accept a short topic after asking for it, not an unrelated instruction.
        if len(text) <= 240 and not re.search(r"\b(?:summarize|explain|why|write|fass|erklär|schreib|warum)\b", command, re.I):
            if last.since and int(last.since) > date.today().year:
                if re.fullmatch(r"(?:19|20)\d{2}", text) and int(text) <= date.today().year:
                    return replace(last, since=text, prompt="")
                return last
            return replace(last, needs_planning=True, offset=0, prompt="")
    recent = bool(re.search(rf"\b{_RECENT}\s+{_PAPERS}\b", command, re.I))
    action = bool(re.match(rf"(?:(?:hey|hi)[, ]+)?(?:(?:can|could|would) you |kannst du )?(?:please |bitte )?{_ACTION}\b", command, re.I))
    conversational = bool(re.search(r"\b(?:(?:can|could|would) you|(?:kannst|könntest|magst|würdest) du|i(?:'d| would) like|i need|ich (?:suche|brauche|möchte))\b", command, re.I)
                          and re.search(rf"\b{_PAPERS}\b", command, re.I))
    anchored_action = action
    conversational = conversational and bool(re.search(
        rf"\b(?:{_ACTION}|finden|looking|need|brauche|möchte)\b", command, re.I))
    action = action or conversational
    new_search = bool(re.search(r"\b(?:search|find|look|suche?|finde)\b", command, re.I))
    if not ((web and action) or (recent and action and new_search) or
            (web and last and re.fullmatch(r"(?:now |instead |doch |jetzt |lieber )?(?:im |the )?(?:web|internet|online)(?: instead| bitte| please)?", command, re.I))):
        return None
    if re.match(r"(?:what is|how does|explain|was ist|erkläre?)\b", command, re.I):
        return None
    ambiguous = local and web and not outside
    count = re.search(rf"\b(\d{{1,4}}|{'|'.join(_COUNTS)})\s+(?:(?:{_RECENT}|online)\s+)?{_PAPERS}\b", text, re.I)
    number = int(count[1]) if count and count[1].isdigit() else _COUNTS.get(count[1].lower(), 8) if count else 8
    limit = max(1, min(20, number))
    year = re.search(r"\b(?:since|from|after|seit|ab)\s+((?:19|20)\d{2})\b", text, re.I)
    since = year[1] if year else str(date.today().year - 2) if recent else ""
    kind = "posted-content" if re.search(r"\bpreprints?\b", command, re.I) else "journal-article"
    # Strip only the request wrapper. Keep spelling, numbers and negation in the topic.
    topic = text[divider.end():] if divider else text
    if suffix and divider and suffix.start() > divider.end():
        topic = text[divider.end():suffix.start()]
    topic = re.sub(r"\b(?:since|from|after|seit|ab)\s+(?:19|20)\d{2}\b", "", topic, flags=re.I)
    topic = re.sub(rf"\b(?:in|on|from|im|aus|auf)\s+(?:the |dem |der )?{_WEB}\b", "", topic, flags=re.I)
    topic = re.sub(rf"\b(?:outside|außerhalb|ausserhalb)\s+(?:(?:my|our|the|meiner|unserer|der)\s+)?{_LOCAL}\b", "", topic, flags=re.I)
    if not divider:
        topic = re.sub(r"^(?:(?:hey|hi)[, ]+)?(?:(?:can|could|would) you |kannst du )?", "", topic, flags=re.I)
        prefix = rf"^(?:(?:please|bitte|me|mir|for|nach|nach neuen|neuen|the|die|das|some|einige|ein paar|im|on|in|the|and|und)\s+|(?:{_ACTION}|{_WEB}|{_RECENT}|{_PAPERS}|\d{{1,4}}|{'|'.join(_COUNTS)})\b[ :,-]*)"
        previous = None
        while previous != topic:
            previous, topic = topic, re.sub(prefix, "", topic, flags=re.I).strip()
        topic = re.sub(rf"\s+{_PAPERS}$", "", topic, flags=re.I)
    topic = re.sub(r"(?:[, ]+)(?:please|bitte)$", "", topic, flags=re.I)
    topic = " ".join(topic.split()).strip(" :,.?!\"“”")
    if topic.casefold() in {"", "it", "this", "that", "them", "dazu", "darüber", "hierzu", "das", "instead", "stattdessen"}:
        topic = last.query if last else ""
    prompt = ""
    if ambiguous:
        prompt = "Should I search your library or the web?"
    elif len(topic) < 2:
        prompt = "What topic should I search the web for? Only your search terms are sent, not your papers or chat."
    elif len(topic) > 240:
        topic = ""
        prompt = "What are the main search terms? Please use a topic under 240 characters; your chat and documents stay private."
    elif since and int(since) > date.today().year:
        prompt = "That starting year is in the future. Which publication year should I search from?"
    natural = (conversational and not anchored_action) or bool(re.search(
        r"\b(?:published in|preferably|preferably in|journal|journals|am besten|am liebsten|erschienen|veröffentlicht|zeitschrift)\b"
        r"|\b(?:papers?|articles?)\b.{0,80}\b(?:in|from)\s+[A-Z]", text))
    if natural and not ambiguous and not (since and int(since) > date.today().year):
        return WebSearch(limit=limit, since=year[1] if year else "", kind=kind, recent=recent,
                         needs_planning=True, pending_message=text[:6000])
    return WebSearch(topic, limit, since, kind=kind, prompt=prompt, scope_pending=ambiguous)


SEARCH_SCHEMA = search_plan.SCHEMA
search_plan_prompt = search_plan.prompt


def web_search_request(message: str, prior: dict | None = None) -> WebSearch | None:
    request = _literal_request(message, prior)
    if request:
        last = _previous(prior)
        if request.query and not request.prompt and not request.needs_planning and (not last or request.query != last.query):
            return replace(request, needs_planning=True, pending_message=message[:6000])
        return request
    text = controls(without_conversational_decline(message))
    if acknowledgement(message) or re.fullmatch(r"stop|cancel|nevermind|never mind|abbrechen|lass es", text.strip(), re.I):
        return None
    if re.search(r"\b(?:don't|dont|do not|no|not|never|without|excluding|except|nicht|kein\w*|ohne)\b.{0,40}\b(?:web|online|internet|pubmed|crossref)\b", text, re.I):
        return None
    if re.search(r"\b(?:my|our|the|meine\w*|unser\w*|im)\s+(?:library|corpus|bibliothek|korpus)\b", text, re.I):
        return None
    last = _previous(prior)
    if last:
        if re.fullmatch(r"(?:in |im |the |my )?(?:library|corpus|bibliothek|korpus)(?: instead| bitte| please)?[.!?]*", text.strip(), re.I):
            return None
        from originrag.analysis.chat_commands import discussion_number
        if discussion_number(message) or re.search(r"\b(?:summari[sz]e|explain|fass|erklär|what did|was hat)\b", text, re.I):
            return None
        return replace(last, needs_planning=True, prompt="", offset=0, next_offset=0)
    # Broad intent gate, not a topic extractor. The model decides the actual action.
    if re.search(r"\b(?:web|internet|online|pubmed|crossref|webscrap\w*)\b", text, re.I):
        if re.search(r"\b(?:find|search|scrape|look|suche?|finde|holen|zieh\w*|neu\w*|new|recent)\b", text, re.I) and not re.match(r"(?:what is|how does|explain|was ist|erkläre?)\b", text, re.I):
            if not re.search(r"\b(?:about|on|über|zu)\s+(?:online|web|internet)\b|\b(?:online learning|web security|web scraping)\b", text, re.I):
                return WebSearch(needs_planning=True, pending_message=message[:6000])
    return None


def resolved_search_plan(request: WebSearch, message: str, response: str) -> WebSearch:
    try:
        plan = search_plan.parse(response)
        for topic in request.topics:
            if topic.casefold() in plan['subject'].casefold() and topic.casefold() not in [t.casefold() for t in plan['topics']]:
                plan['topics'].append(topic)
        if plan['topic_mode'] == 'any' and len(plan['topics']) == 2:
            a, b = (re.escape(t) for t in plan['topics'])
            if re.search(rf'\b(?:{a}\s+(?:and|und)\s+{b}|{b}\s+(?:and|und)\s+{a})\b', message, re.I):
                plan['topic_mode'] = 'all'
        plan = search_plan.finalize(plan)
    except (TypeError, ValueError) as exc:
        return replace(request, needs_planning=False, planning_error=str(exc)[:80],
                       pending_message=request.pending_message or message[:6000],
                       prompt="I could not prepare this web search just now. Your request is kept; please retry.")
    count = re.search(rf"\b(\d{{1,4}}|{'|'.join(_COUNTS)})[ -]+(?:online[ -]+)?{_PAPERS}\b", message, re.I)
    count = count or re.search(rf"\b(?:only|just|nur|genau)\s+(\d{{1,4}}|{'|'.join(_COUNTS)})\b", message, re.I)
    limit = ((int(count[1]) if count[1].isdigit() else _COUNTS[count[1].lower()]) if count else
             request.limit if request.limit != 8 and plan['limit'] == 8 else plan['limit'])
    return replace(request, query=plan["topic"], queries=tuple(plan["queries"]),
                   subject=plan['subject'], topics=tuple(plan['topics']), topic_mode=plan['topic_mode'],
                   limit=min(20, max(1, limit)), since=plan["since"], recent=plan["recent"],
                   journal=plan["journal"], journal_mode=plan["journal_mode"], journal_preference=plan["journal_mode"] == "prefer",
                   intent=plan["intent"], needs_planning=False, planning_error="", journal_pending=False,
                   pending_message=message[:6000] if plan["intent"] == "clarify" else "",
                   prompt=plan["clarification"] or "What topic should I search online?" if plan["intent"] == "clarify" else "")


def resolve_journal(journal: str, records: list) -> dict | None:
    key = lambda text: " ".join(re.findall(r"\w+", text.casefold()))
    exact = [record for record in records if key(record.get("title", "")) == key(journal)]
    return exact[0] if len(exact) == 1 else None
