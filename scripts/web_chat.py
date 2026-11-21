from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import webbrowser
from collections import Counter, deque
from http.cookies import SimpleCookie
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlencode, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from originrag.app_profiles import app_profile, PROFILES, COLLECTION_ACTIONS, CHAT_ROUTES, LazyModule, lazy_function  # noqa: E402
from originrag.cancellation import Cancellation, TurnCancelled, cancellation_scope, cancellable_lock, checkpoint, commit_answer  # noqa: E402
from originrag.analysis.chat import ChatTurnContext, build_chat_retrieval_question, resolve_chat_task  # noqa: E402
from originrag.analysis.faithfulness import citation_claim_blocks, direct_evidence_text  # noqa: E402
from originrag.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402
from originrag.web_local_access import local_browser_request  # noqa: E402
from originrag.model_runtime import manager_for, model_download_allowed, model_manage_allowed  # noqa: E402
from originrag.library_topics import assign_library_topics  # noqa: E402
from originrag.library_presence import presence_answer, presence_language  # noqa: E402
from originrag.analysis.chat_commands import controls, reading_selection, ReadingSelection  # noqa: E402
from originrag.personal_shelf import PersonalShelf  # noqa: E402
from originrag.reading_history import ReadingHistory, reading_query  # noqa: E402
from originrag.reader_notes import ReaderNotes, NoteConflict, document_key, notebook_paper_key, validate_notebook  # noqa: E402
from originrag.web_reader import HTML as READER_HTML  # noqa: E402
from originrag.web_workspace import HTML as WORKSPACE_HTML, PAGES as WORKSPACE_PAGES, handle_writing  # noqa: E402
from originrag.web_folio import HTML as FOLIO_HTML, handle_folio  # noqa: E402
from originrag.writing_collaboration import WritingCollaboration  # noqa: E402
from originrag.provenance.library import (  # noqa: E402
    LibraryStatusResolver,
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
    normalize_title,
)
from originrag.provenance.requests import queue_missing_sources  # noqa: E402
from originrag.provenance.source_type import classify_source_document  # noqa: E402
from originrag.utils.io import write_json  # noqa: E402
from originrag.web_appearance import with_appearance, APPEARANCE_SCRIPT_HASH  # noqa: E402
from originrag.web_navigation import with_navigation, navigation_for_profile  # noqa: E402
from originrag.collection_uploads import CollectionUploads  # noqa: E402
from originrag.web_onboarding import with_onboarding  # noqa: E402
from originrag.web_shelf import with_shelf  # noqa: E402
from originrag.library_pdf import open_registered_pdf, byte_range  # noqa: E402
from originrag.discovery.topic_search import TopicDiscovery, DiscoveryBusy, DiscoveryUnavailable  # noqa: E402
from originrag.discovery.oa_import import PaperImports, ImportBusy  # noqa: E402
from originrag.web_auth import (  # noqa: E402
    AuthStore,
    AuthenticatedUser,
    AuthenticationError,
    MAX_SAVED_CHATS,
    QuotaExceededError,
)

ask_corpus = LazyModule('ask_corpus')
import_paper_inbox = LazyModule('import_paper_inbox')
inventory_markdown = lazy_function('originrag.analysis.corpus_inventory', 'inventory_markdown')
enrich_reference_online = lazy_function('originrag.provenance.resolver', 'enrich_reference_online')
resolve_reference_against_library = lazy_function('originrag.provenance.resolver', 'resolve_reference_against_library')
classify_paper_search_mode = lazy_function('originrag.retrieval.query_plan', 'classify_paper_search_mode')
has_sufficient_curated_source_expansions = lazy_function('originrag.retrieval.query_plan', 'has_sufficient_curated_source_expansions')
configured_cache_path = lazy_function('originrag.retrieval.index_cache', 'configured_cache_path')
prime_read_index_cache = lazy_function('originrag.retrieval.index_cache', 'prime_read_index_cache')


TITLE_FILENAME = "session_title.txt"
TITLE_MAX_CHARS = 48
MAX_LIBRARY_UPLOAD_BYTES = 100 * 1024 * 1024
TITLE_STOPWORDS = {
    "about",
    "answer",
    "and",
    "any",
    "are",
    "auf",
    "bitte",
    "can",
    "das",
    "den",
    "der",
    "die",
    "eine",
    "einen",
    "find",
    "for",
    "frage",
    "gaps",
    "gibt",
    "give",
    "hieß",
    "hiess",
    "hypothesis",
    "ist",
    "mach",
    "make",
    "malaria",
    "mir",
    "mit",
    "paper",
    "papers",
    "quelle",
    "quellen",
    "research",
    "show",
    "source",
    "sources",
    "the",
    "there",
    "was",
    "what",
    "which",
    "wie",
    "with",
    "zum",
}
PUBLIC_SOURCE_LIMIT = 30
AUTO_TRACE_MAX_CLAIMS = 12
AUTO_TRACE_MAX_CARRIERS = 16
WEB_EVIDENCE_SELECTORS = {"current", "trace_flow"}



AUTHENTICATED_TURN_SETTING_FIELDS = frozenset(
    {
        "profile_instruction",
    }
)
CHAT_TASK_MODES = {
    "answer": "evidence_qa",
    "gaps": "gap_finder",
    "hypothesis": "evidence_qa",
    "sources": "evidence_qa",
}


class SessionNotFoundError(ValueError):
    """Raised when a client refers to a saved chat that no longer exists."""


TITLE_TOKEN_OVERRIDES = {
    "ai": "AI",
    "dna": "DNA",
    "gwas": "GWAS",
    "ml": "ML",
    "pfemp1": "PfEMP1",
    "rna": "RNA",
    "scrna": "scRNA",
    "seq": "Seq",
    "var": "Var",
}


@dataclass
class WebChatSettings:
    app_profile: str = 'full'
    backend: str = "qwen"
    planner_backend: str = "ollama"
    planner_model: str | None = None
    answer_model: str | None = None
    preset: str = "pro"
    task: str = "auto"
    repair_attempts: int = 2
    template_fallback: bool = True
    chunks_dir: str = "data/chunks"
    papers: str = "data/metadata/papers.tsv"
    category: str | None = None
    query_mode: str = "evidence"
    include_low_quality: bool = False
    exclude_tables: bool = False
    exclude_figures: bool = False
    exclude_supplement: bool = False
    allow_missing_doi_sources: bool = False
    limit: int | None = None
    candidate_multiplier: int | None = None
    max_per_paper: int | None = None
    max_chars_per_chunk: int | None = None
    min_score: float = 0.0
    profile_instruction: str | None = None
    evidence_selector: str = "trace_flow"
    retriever: str = "bm25"
    retrieval_index: str = "data/indexes/pfempire_fts.sqlite"
    trace_lineage_graph: str | None = None



    trace_token_budget: int | None = None


class WebChatApp:
    def __init__(
        self, settings: WebChatSettings, out_dir: str = "outputs/web_chat_sessions"
    ) -> None:
        self.settings = settings
        self.out_dir = Path(out_dir)
        self.session_dir = make_session_dir(self.out_dir)
        self.previous_turns: list[ChatTurnContext] = []
        self.review_conversation_id = secrets.token_urlsafe(12)
        self.turn_index = 1
        self.lock = threading.Lock()
        self.session_locks: dict[str, threading.Lock] = {}
        write_json(self.session_dir / "session_config.json", {"settings": settings.__dict__})
        append_session_line(
            self.session_dir,
            f"# OriginRAG Web Chat Session\n\nStarted: {datetime.now().isoformat(timespec='seconds')}\n",
        )

    def reset(self) -> dict:
        with self.lock:
            self.session_dir = make_session_dir(self.out_dir)
            self.previous_turns = []
            self.turn_index = 1
            write_json(
                self.session_dir / "session_config.json", {"settings": self.settings.__dict__}
            )
            append_session_line(
                self.session_dir,
                f"# OriginRAG Web Chat Session\n\nStarted: {datetime.now().isoformat(timespec='seconds')}\n",
            )
        return {"session_dir": relative_path(self.session_dir)}

    def run_turn(
        self,
        message: str,
        settings_update: dict,
        progress_callback=None,
    ) -> dict:
        with self.lock:
            session_dir = self.session_dir
        return self.run_turn_for_session(
            session_dir,
            message,
            settings_update,
            progress_callback=progress_callback,
        )

    def run_turn_for_session(
        self,
        session_dir: Path,
        message: str,
        settings_update: dict,
        progress_callback=None,
    ) -> dict:
        """Run a turn in a captured session, independent of UI navigation."""

        clean_message = " ".join((message or "").split())
        if not clean_message:
            raise ValueError("Message is empty.")
        started_at = time.monotonic()
        target_session = safe_session_dir(self.out_dir, str(session_dir))
        target_key = str(target_session.resolve())
        with self.lock:
            session_lock = self.session_locks.setdefault(target_key, threading.Lock())




        with cancellable_lock(session_lock):
            settings = merge_settings(self.settings, settings_update)
            saved_turns = read_session_turns(target_session)
            previous_turns = [
                turn_context_from_saved_turn(turn)
                for turn in saved_turns[-2:]
                if turn.get("user")
            ]
            retrieval_question = build_chat_retrieval_question(
                clean_message,
                previous_turns,
            )
            previous_task = previous_turns[-1].resolved_task if previous_turns else ""
            resolved_task = resolve_chat_task(
                clean_message,
                settings.task,
                previous_task=previous_task,
            )
            turn_index = next_session_turn_index(target_session)
            turn_dir = (
                target_session
                / f"turn_{turn_index:03d}_{ask_corpus.slugify(clean_message)}"
            )
            turn_args = build_ask_args(
                settings,
                clean_message,
                resolved_task,
                retrieval_question=retrieval_question,
            )
            ensure_session_title(
                target_session,
                infer_session_title(clean_message, resolved_task),
            )
            from originrag.retrieval.quick_list import more_list_request, paper_list_request

            language = presence_language(clean_message)
            listing = paper_list_request(clean_message) if resolved_task == "sources" else None
            selection = reading_selection(clean_message)
            command = controls(clean_message)
            relative = re.fullmatch(
                r"(?:(?:can|could|would) you )?(?:please )?summari[sz]e (?:my|this|the|that|the first|the second) (?:paper|article)"
                r"|(?:bitte )?fass(?:e)? (?:mir )?(?:bitte )?(?:mein|dieses|das|das erste|das zweite) paper zusammen",
                command, re.I)
            direct = None
            seen_ids = set()
            more = more_list_request(clean_message) if settings.task in {"auto", "sources"} else None
            if more:
                listing, seen_ids = resume_quick_list(more, saved_turns, settings)
                if listing is None and command not in {"more", "mehr"}:
                    direct = {"resolved_task": "library_status", "answer": {
                        "en": "Which topic should I find more papers on?",
                        "de": "Zu welchem Thema soll ich weitere Paper suchen?",
                        "fr": "Sur quel sujet voulez-vous d’autres articles ?",
                        "es": "¿Sobre qué tema buscas más artículos?",
                    }[more[0]]}
            group = re.fullmatch(
                r"(?:(?:can|could|would) you )?(?:please )?summari[sz]e (?:these|those|both|the first two) (?:papers|articles|studies)"
                r"|(?:bitte )?fass(?:e)? (?:mir )?(?:bitte )?(?:diese|beide|die beiden|die ersten zwei) (?:papers?|artikel|studien) zusammen",
                command, re.I)
            go_ahead = command.replace(", ", " ") in {"do that", "go ahead", "yes do that", "mach das", "ja mach das", "vas-y", "hazlo"}
            affirmative = go_ahead or command in {"yes", "yep", "ja", "oui", "sí"}
            prior = saved_turn_result(saved_turns[-1]) if saved_turns and (relative or group or selection or affirmative) else {}
            # A correction refers to the displayed list, not the one-item summary of it.
            last_list = prior.get("reading_choices") or prior.get("sources") or []
            pending = prior.get("confirm_reading") if affirmative else None
            confirm_reading = None
            if pending and prior.get("task") == "library_status":
                selection = ReadingSelection(pending["number"], pending["language"])
            elif go_ahead:
                direct = {"resolved_task": "library_status", "answer":
                          "What would you like me to do: find papers, summarize a paper, or answer a question about the studies?"}
            if selection and selection.correction and not (
                prior.get("task") in {"paper_summary", "paper_summaries"} or prior.get("reading_choices")
            ):
                if 1 <= selection.number <= len(last_list):
                    confirm_reading = {"number": selection.number, "language": selection.language}
                    title = last_list[selection.number - 1].get("title", "")
                    answer = (f"Soll ich Paper {selection.number} zusammenfassen: „{title}“?" if selection.language == "de"
                              else f"Should I summarize paper {selection.number}: “{title}”?")
                else:
                    answer = "Which paper should I summarize? Give me a title, DOI, or a number from the last list."
                direct = {"resolved_task": "library_status", "sources": last_list, "answer": answer}
            if group:
                refs = prior.get("sources") or []
                if re.search(r"first two|ersten zwei", group[0], re.I):
                    refs = last_list[:2]
                if (not 1 <= len(refs) <= 3 or not all(p.get("paper_id") for p in refs)
                    or (re.search(r"\b(both|beide|beiden|two|zwei)\b", group[0], re.I) and len(refs) != 2)):
                    direct = {"resolved_task": "library_status", "sources": refs, "answer":
                              "Which papers should I summarize? Choose up to three from the last list, "
                              "for example: “Summarize the first two papers.”"}
                else:
                    turn_args.reading_group = [(p["paper_id"], "de" if "zusammen" in group[0].lower() else "en") for p in refs]
            if direct is None and (language or listing or relative or selection):
                papers = REPO_ROOT / settings.papers
                catalogue = library_catalog(str(papers.with_name("paper_registry.tsv")), str(papers),
                                            chunks_dir=str(REPO_ROOT / settings.chunks_dir))
                if language:
                    direct = {"resolved_task": "library_status", "answer": presence_answer(catalogue, language)}
                elif listing:
                    direct = quick_paper_list(listing, catalogue, settings, seen_ids=seen_ids)
                    direct["list_page"] = {"topic": listing.topic, "limit": listing.limit,
                                           "scope": list_scope(settings), "first": not more}
                else:
                    refs = last_list if selection else prior.get("sources") or []
                    n = selection.number - 1 if selection else 0
                    choices = refs if refs else catalogue if len(catalogue) == 1 and not prior else ()
                    if 0 <= n < len(choices) and (selection or len(choices) == 1) and choices[n].get("paper_id"):
                        lang = selection.language if selection else "de" if "zusammen" in command else "en"
                        turn_args.reading_request = (choices[n]["paper_id"], lang)
                        last_list = choices
                    else:
                        direct = {"resolved_task": "library_status", "sources": refs, "answer":
                                  (f"The list has {len(refs)} papers, not a paper #{n+1}. Choose a listed number or give its title or DOI."
                                   if refs and selection else "Which paper should I summarize? Give me its title or DOI.")}
            if direct is not None:
                turn_dir.mkdir(parents=True, exist_ok=True)
                answer_path = turn_dir / "answer.md"
                answer_path.write_text(direct["answer"], encoding="utf-8")
                result = {**direct, "retrieval_question": clean_message, "answer_path": answer_path}
            else:
                with manager_for(REPO_ROOT).session() as model_selection:
                    if model_selection:
                        turn_args.answer_model = model_selection["model"]
                        turn_args.planner_model = model_selection["model"]
                    result = ask_corpus.run_question(
                        turn_args,
                        turn_dir,
                        progress_callback=progress_callback,
                    )
                    if model_selection:
                        result["model_runtime"] = model_selection
            commit_answer()
            resolved_task = result.get("resolved_task", resolved_task)
            retrieval_question = result.get("retrieval_question", retrieval_question)
            append_turn_summary(
                session_dir=target_session,
                user_text=clean_message,
                retrieval_question=retrieval_question,
                resolved_task=resolved_task,
                result=result,
                turn_dir=turn_dir,
            )
            public_result = serialize_turn_result(
                result=result,
                turn_dir=turn_dir,
                retrieval_question=retrieval_question,
                resolved_task=resolved_task,
            )
            public_result["elapsed_seconds"] = round(time.monotonic() - started_at, 3)
            if result.get("list_page"):
                public_result["list_page"] = result["list_page"]
            if confirm_reading:
                public_result["confirm_reading"] = confirm_reading
            if last_list and (selection or relative or group):
                public_result["reading_choices"] = [
                    {"paper_id": p["paper_id"], "title": p.get("title", "")}
                    for p in last_list[:100] if p.get("paper_id")]
            if public_result.get("claim_source_chains"):
                trace_path = turn_dir / "automatic_claim_source_trace.json"
                write_json(
                    trace_path,
                    {
                        "schema_version": "automatic_claim_source_trace_v2",
                        "max_claims": AUTO_TRACE_MAX_CLAIMS,
                        "max_carriers": AUTO_TRACE_MAX_CARRIERS,
                        "chains": public_result["claim_source_chains"],
                    },
                )
                public_result["claim_source_trace_path"] = relative_path(trace_path)
            write_json(turn_dir / "public_result.json", public_result)
            with self.lock:
                if self.session_dir.resolve() == target_session.resolve():
                    latest = read_session_turns(target_session)[-1]
                    self.previous_turns = previous_turns[-1:] + [turn_context_from_saved_turn(latest)]
                    self.turn_index = turn_index + 1
            return public_result

    def load_session(self, session_dir_text: str) -> dict:
        with self.lock:
            session_dir = safe_session_dir(self.out_dir, session_dir_text)
            turns = read_session_turns(session_dir)
            self.session_dir = session_dir
            self.previous_turns = [
                turn_context_from_saved_turn(turn) for turn in turns[-2:] if turn.get("user")
            ]
            self.turn_index = len(turns) + 1
            return {
                "session_dir": relative_path(self.session_dir),
                "title": session_title(self.session_dir),
                "messages": session_messages(self.session_dir),
            }

    def read_session(self, session_dir_text: str) -> dict:
        """Read one saved chat without changing browser-session navigation state."""

        with self.lock:
            session_dir = safe_session_dir(self.out_dir, session_dir_text)
            return {
                "session_dir": relative_path(session_dir),
                "title": session_title(session_dir),
                "messages": session_messages(session_dir),
            }

    def delete_session(self, session_dir_text: str) -> dict:
        with self.lock:
            session_dir = safe_session_dir(self.out_dir, session_dir_text)
            deleting_current = session_dir.resolve() == self.session_dir.resolve()
            shutil.rmtree(session_dir)
            if deleting_current:
                self.session_dir = make_session_dir(self.out_dir)
                self.previous_turns = []
                self.turn_index = 1
                write_json(
                    self.session_dir / "session_config.json", {"settings": self.settings.__dict__}
                )
                append_session_line(
                    self.session_dir,
                    f"# OriginRAG Web Chat Session\n\nStarted: {datetime.now().isoformat(timespec='seconds')}\n",
                )
            return {
                "session_dir": relative_path(self.session_dir),
                "sessions": list_saved_sessions(self.out_dir, self.session_dir),
            }

    def trace_source(self, paper_id: str, claim_text: str) -> dict:
        return build_public_source_trace(paper_id=paper_id, claim_text=claim_text)

    def queue_source(self, reference: dict) -> dict:
        raw_identifiers = {
            field: str(reference.get(field) or "").strip() for field in ("doi", "pmid", "pmcid")
        }
        normalizers = {"doi": normalize_doi, "pmid": normalize_pmid, "pmcid": normalize_pmcid}
        identifiers = {field: normalizers[field](value) for field, value in raw_identifiers.items()}
        invalid_fields = [
            field for field, value in raw_identifiers.items() if value and not identifiers[field]
        ]
        if invalid_fields:
            raise ValueError(
                f"Invalid persistent identifier field(s): {', '.join(invalid_fields)}."
            )
        if not any(identifiers.values()):
            raise ValueError("A DOI, PMID, or PMCID is required for a source request.")
        safe_reference = {
            **identifiers,
            "title": " ".join(str(reference.get("title") or "").split()),
            "year": str(reference.get("year") or "").strip(),
        }
        resolver = default_library_resolver()
        verified = resolve_reference_against_library(safe_reference, resolver)
        verified_fields = verified.get("verified_identifiers") or {}
        has_verified_identifier = any(verified_fields.get(field) for field in identifiers)
        if not has_verified_identifier:
            online_candidate = dict(verified)
            online_candidate["bibliographic_resolution_status"] = "identifier_extracted_unverified"
            verified = enrich_reference_online(online_candidate)
            if verified.get("bibliographic_resolution_status") == "exact_id":
                verified = resolve_reference_against_library(verified, resolver)
        verified_fields = verified.get("verified_identifiers") or {}
        has_verified_identifier = any(verified_fields.get(field) for field in identifiers)
        if (
            verified.get("bibliographic_resolution_status") not in {"exact_id", "exact_title"}
            or not has_verified_identifier
        ):
            raise ValueError(
                "The identifier could not be verified against the local registry or an exact online record."
            )
        status = verified.get("library_status") or resolver.resolve(verified)
        if not status.get("can_queue"):
            raise ValueError("The verified source is already local or is not safe to queue.")
        queue_reference = dict(verified)
        for field in identifiers:
            queue_reference[field] = str(verified_fields.get(field) or "")
        return queue_missing_sources(
            [queue_reference],
            REPO_ROOT / "data/metadata/paper_requests.tsv",
            category=str(reference.get("category") or "general_malaria"),
            note="web_claim_source_trace",
        )


class WorkspaceApp:
    def __init__(self, settings: WebChatSettings, out_dir: str | Path):
        self.settings = settings
        self.out_dir = Path(out_dir)


class AuthenticatedWebChatRegistry:
    """Keep chat navigation state separate for every authenticated browser session."""

    def __init__(self, settings: WebChatSettings, out_dir: str | Path) -> None:
        self.settings = settings
        self.out_dir = Path(out_dir)
        self.apps: dict[tuple[str, str], WebChatApp] = {}
        self.lock = threading.Lock()

    def for_session(self, user: AuthenticatedUser, session_token: str) -> WebChatApp:
        session_key = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        key = (user.user_id, session_key)
        with self.lock:
            if key not in self.apps:
                user_out_dir = self.out_dir / "users" / user.user_id
                factory = WebChatApp if app_profile(self.settings.app_profile).rag else WorkspaceApp
                self.apps[key] = factory(self.settings, out_dir=str(user_out_dir))
            return self.apps[key]

    def discard(self, user: AuthenticatedUser, session_token: str) -> None:
        session_key = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        with self.lock:
            self.apps.pop((user.user_id, session_key), None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-hosted local OriginRAG web chat.")
    parser.add_argument('--profile', choices=PROFILES, default=os.environ.get('ORIGINRAG_APP_PROFILE', 'full'),
                        help='Run the full workspace, RAG tools or Quill without chat/model services.')
    parser.add_argument("--host", default=os.environ.get("PFEMPIRE_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PFEMPIRE_WEB_PORT", "8765")))
    parser.add_argument(
        "--strict-port",
        action="store_true",
        default=environment_flag("PFEMPIRE_WEB_STRICT_PORT", default=False),
        help="Fail instead of selecting another port when the requested port is occupied.",
    )
    parser.add_argument(
        "--max-concurrent-turns",
        type=int,
        default=int(os.environ.get("PFEMPIRE_MAX_CONCURRENT_TURNS", "1")),
        help="Maximum simultaneous chat pipelines. One is safest for a single local model.",
    )
    parser.add_argument(
        "--backend",
        choices=["template", "queue", "deepseek", "qwen", "ollama", "none"],
        default="qwen",
    )
    parser.add_argument(
        "--planner-backend",
        choices=["rule_based", "ollama"],
        default="ollama",
    )
    parser.add_argument("--planner-model")
    parser.add_argument("--answer-model")
    parser.add_argument("--preset", choices=['balanced', 'deep', 'pro', 'quick', 'wide'], default="pro")
    parser.add_argument(
        "--task",
        choices=["auto", "answer", "gaps", "hypothesis", "sources"],
        default="auto",
    )
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument(
        "--evidence-selector",
        choices=sorted(WEB_EVIDENCE_SELECTORS),
        default="trace_flow",
        help="Use the conservative lineage-aware audit by default or request the standard baseline explicitly.",
    )
    parser.add_argument(
        "--trace-lineage-graph",
        help="Optional adjudicated/frozen lineage graph used by lineage-aware audit mode.",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("PFEMPIRE_WEB_DATA_DIR", "outputs/web_chat_sessions"),
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        default=environment_flag("PFEMPIRE_WEB_AUTH", default=False),
        help="Require local pilot accounts and isolate chats per user.",
    )
    parser.add_argument(
        "--auth-db",
        default=os.environ.get(
            "PFEMPIRE_AUTH_DB", "data/local_auth/pfempire_web_auth.sqlite"
        ),
        help="Local SQLite account/session database used only by --auth mode.",
    )
    parser.add_argument(
        "--secure-cookies",
        action="store_true",
        default=environment_flag("PFEMPIRE_WEB_SECURE_COOKIES", default=False),
        help="Mark session cookies Secure. Required behind HTTPS, unusable over plain HTTP.",
    )
    parser.add_argument(
        "--open", action="store_true", help="Open the browser after the server starts."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_project_env(REPO_ROOT / ".env")
    args = parse_args(argv)
    profile = app_profile(args.profile)
    settings = WebChatSettings(
        app_profile=profile.name,
        backend=args.backend,
        planner_backend=args.planner_backend,
        planner_model=args.planner_model,
        answer_model=args.answer_model,
        preset=args.preset,
        task=args.task,
        repair_attempts=max(0, args.repair_attempts),
        evidence_selector=args.evidence_selector,
        trace_lineage_graph=args.trace_lineage_graph,
    )
    auth_store = AuthStore(args.auth_db) if args.auth else None
    if auth_store and args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "The local password pilot may bind only to loopback. Do not expose it on the LAN; "
            "Use an SSH tunnel for remote access."
        )
    factory = WebChatApp if profile.rag else WorkspaceApp
    app = None if auth_store else factory(settings=settings, out_dir=args.out_dir)
    registry = AuthenticatedWebChatRegistry(settings, args.out_dir) if auth_store else None
    corpus_lock = threading.RLock()
    library_manager = LibrarySyncManager(
        REPO_ROOT / "bib" / "inbox",
        corpus_lock=corpus_lock,
        metadata_only=not profile.rag,
    )
    handler = build_handler(
        app,
        auth_store=auth_store,
        app_registry=registry,
        secure_cookies=args.secure_cookies,
        max_concurrent_turns=max(1, args.max_concurrent_turns),
        library_manager=library_manager,
        profile=profile.name,
    )
    server = create_server(
        args.host,
        args.port,
        handler,
        attempts=1 if args.strict_port else 20,
    )
    bound_host, bound_port = server.server_address[:2]
    url = os.environ.get("PFEMPIRE_PUBLIC_URL") or f"http://{bound_host}:{bound_port}"
    print(f"OriginRAG {profile.name} workspace running at {url}")
    if app and profile.rag:
        print(f"Session dir: {relative_path(app.session_dir)}")
    elif auth_store:
        print(f"Authenticated user chats: {relative_path(Path(args.out_dir) / 'users')}")
        print("Account-name registration: enabled")
    if profile.rag and settings.backend == "queue":
        print(
            "Queue backend selected. Keep `python scripts/ollama_worker.py` running in another terminal."
        )
    if args.open:
        webbrowser.open(url)
    import signal
    def stop_web_service(*_):
        raise KeyboardInterrupt
    old_termination_handler = signal.signal(signal.SIGTERM, stop_web_service)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        if handler.turn_job_manager:
            handler.turn_job_manager.shutdown()
        handler.paper_import_manager.shutdown()
        manager_for(REPO_ROOT).close()
        server.server_close()
        signal.signal(signal.SIGTERM, old_termination_handler)
    return 0


def environment_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_login_target(value):
    return value if isinstance(value, str) and re.fullmatch(r'/(?:write\?id=[0-9a-f]{32}|folio(?:\?id=[0-9a-f]{32})?)', value) else '/'


@dataclass
class TurnJob:
    job_id: str
    owner_key: str
    app: WebChatApp | None
    session_dir: Path
    message: str
    settings_update: dict
    created_at: float
    state: str = "queued"
    stage: str = "Waiting for the shared model"
    progress: int = 2
    started_at: float = 0.0
    completed_at: float = 0.0
    result: dict | None = None
    result_path: Path | None = None
    result_output_dir: str = ''
    error: str = ""
    completion_callback: object | None = None
    cancellation: Cancellation = dataclass_field(default_factory=Cancellation)


def write_json_atomically(path: Path, payload: object, *, mode: int | None = None) -> None:
    """Replace a JSON snapshot without exposing a partially written file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class TurnJobManager:
    """Own browser-independent chat jobs and admit them in global FIFO order."""

    terminal_states = {"complete", "failed", "cancelled", "interrupted"}

    def __init__(
        self,
        capacity: int = 1,
        *,
        corpus_lock: threading.RLock | None = None,
        auto_worker: bool = True,
    ) -> None:
        self.capacity = max(1, int(capacity))
        self.corpus_lock = corpus_lock or threading.RLock()
        self.condition = threading.Condition()
        self.waiting: deque[str] = deque()
        self.active: set[str] = set()
        self.jobs: dict[str, TurnJob] = {}
        self.recovered_scopes: set[tuple[str, str]] = set()
        self.stopping = False
        self.auto_worker = auto_worker
        self.workers: list[threading.Thread] = []

    def enqueue(
        self,
        *,
        owner_key: str,
        app: WebChatApp,
        session_dir: Path,
        message: str,
        settings_update: dict,
        completion_callback=None,
    ) -> dict:
        clean_message = " ".join(str(message or "").split())
        if not clean_message:
            raise ValueError("Message is empty.")
        target_session = safe_session_dir(app.out_dir, str(session_dir))
        settings = merge_settings(app.settings, settings_update)
        saved_turns = read_session_turns(target_session)
        previous_task = str(saved_turns[-1].get("task") or "") if saved_turns else ""
        resolved_task = resolve_chat_task(
            clean_message,
            settings.task,
            previous_task=previous_task,
        )
        ensure_session_title(
            target_session,
            infer_session_title(clean_message, resolved_task),
        )
        job = TurnJob(
            job_id=secrets.token_hex(16),
            owner_key=str(owner_key),
            app=app,
            session_dir=target_session,
            message=clean_message,
            settings_update=dict(settings_update),
            created_at=time.time(),
            completion_callback=completion_callback,
        )
        with self.condition:
            if self.stopping:
                raise RuntimeError("The chat job queue is shutting down.")
            self._start_workers_locked()
            self._prune_locked()
            self.jobs[job.job_id] = job
            self.waiting.append(job.job_id)
            self._persist_locked(job)
            snapshot = self._snapshot_locked(job)
            self.condition.notify_all()
        return snapshot

    def global_status(self) -> dict[str, int]:
        with self.condition:
            self._prune_locked()
            return {
                "queued": len(self.waiting),
                "running": len(self.active),
                "capacity": self.capacity,
            }

    def recover_persisted(self, owner_key: str, app: WebChatApp) -> None:
        """Expose an interrupted prior-process job without ever rerunning it."""

        base = app.out_dir if app.out_dir.is_absolute() else REPO_ROOT / app.out_dir
        owner_hash = hashlib.sha256(str(owner_key).encode("utf-8")).hexdigest()
        scope = (owner_hash, str(base.resolve()))
        with self.condition:
            if scope in self.recovered_scopes:
                return
            self.recovered_scopes.add(scope)
        for path in base.glob("*/turn_jobs/*.json"):
            try:
                if not re.fullmatch(r'[0-9a-f]{32}\.json', path.name):
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(payload.get("job_id") or "")
                state = str(payload.get("state") or "")
                if (
                    not re.fullmatch(r"[0-9a-f]{32}", job_id)
                    or payload.get("owner_scope_hash") != owner_hash
                    or state not in {"queued", "running", "cancelling", "cancelled", "failed", "interrupted"}
                ):
                    continue
                session_dir = path.parents[1]
                safe_session_dir(app.out_dir, str(session_dir))
                completed_at = float(payload.get("completed_at") or 0)
                if state in {"failed", "interrupted", "cancelled"} and completed_at < time.time() - 86400:
                    continue
                recovered = TurnJob(
                    job_id=job_id,
                    owner_key=str(owner_key),
                    app=None,
                    session_dir=session_dir,
                    message=str(payload.get("message") or "Submitted question"),
                    settings_update={},
                    created_at=float(payload.get("created_at") or path.stat().st_mtime),
                    state=("interrupted" if state in {"queued", "running", "cancelling"} else state),
                    stage=(
                        "Interrupted by server restart"
                        if state in {"queued", "running", "cancelling"}
                        else str(payload.get("stage") or "Failed")
                    ),
                    progress=int(payload.get("progress") or 0),
                    started_at=float(payload.get("started_at") or 0),
                    completed_at=(completed_at or time.time()),
                    error=(
                        "The server stopped before this job completed. Please submit it again."
                        if state in {"queued", "running", "cancelling"}
                        else str(payload.get("error") or "")
                    ),
                )
                with self.condition:
                    if job_id not in self.jobs:
                        self.jobs[job_id] = recovered
                        self._persist_locked(recovered)
                        self._prune_locked()
            except (OSError, ValueError, TypeError):
                continue

    def owner_jobs(self, owner_key: str) -> list[dict]:
        with self.condition:
            self._prune_locked()
            recent_completion_cutoff = time.time() - 300
            jobs = [
                self._snapshot_locked(job)
                for job in self.jobs.values()
                if hmac.compare_digest(job.owner_key, str(owner_key))
                and (
                    job.state != "complete"
                    or job.completed_at >= recent_completion_cutoff
                )
            ]
        return sorted(jobs, key=lambda item: float(item["created_at"]))

    def queue_details(self, owner_key: str | None = None) -> list[dict]:
        """Return active queue entries, optionally restricted to one owner.

        The owner key is included only for the authenticated HTTP layer to map
        administrator-visible jobs to account display names. It must not be
        returned directly to browser clients.
        """

        with self.condition:
            details = []
            for job in self.jobs.values():
                if job.state not in {"queued", "running", "cancelling"}:
                    continue
                if owner_key is not None and not hmac.compare_digest(
                    job.owner_key, str(owner_key)
                ):
                    continue
                snapshot = self._snapshot_locked(job)
                snapshot["owner_key"] = job.owner_key
                details.append(snapshot)
        priority = {"running": 0, "cancelling": 0, "queued": 1}
        return sorted(
            details,
            key=lambda item: (
                priority.get(str(item.get("state") or ""), 9),
                int(item.get("queue_position") or 0),
                float(item.get("created_at") or 0),
            ),
        )

    def session_jobs(
        self,
        owner_key: str,
        session_dir: Path,
        *,
        include_complete: bool = False,
    ) -> list[dict]:
        target = session_dir.resolve()
        with self.condition:
            self._prune_locked()
            jobs = [
                self._snapshot_locked(job)
                for job in self.jobs.values()
                if hmac.compare_digest(job.owner_key, str(owner_key))
                and job.session_dir.resolve() == target
                and (include_complete or job.state != "complete")
            ]
        return sorted(jobs, key=lambda item: float(item["created_at"]))

    def job(self, owner_key: str, job_id: str) -> dict | None:
        with self.condition:
            self._prune_locked()
            job = self.jobs.get(str(job_id))
            if not job or not hmac.compare_digest(job.owner_key, str(owner_key)):
                return None
            snapshot = self._snapshot_locked(job)
            if job.state == "complete" and job.result is not None:
                snapshot["result"] = job.result
            result_path = job.result_path if job.state == 'complete' and job.result is None else None
            session_dir = job.session_dir
        if result_path is not None:
            result = self._read_result(result_path, str(owner_key), str(job_id), session_dir)
            if result is not None:
                snapshot['result'] = result
            else:
                snapshot['result_unavailable'] = True
                snapshot['error'] = 'The saved job response is unavailable. Reopen the chat to read its saved answer.'
        return snapshot

    def session_busy(self, owner_key: str, session_dir: Path) -> bool:
        target = session_dir.resolve()
        with self.condition:
            return any(
                hmac.compare_digest(job.owner_key, str(owner_key))
                and job.session_dir.resolve() == target
                and job.state in {"queued", "running", "cancelling"}
                for job in self.jobs.values()
            )

    def cancel(self, owner_key: str, job_id: str) -> dict | None:
        with self.condition:
            job = self.jobs.get(job_id)
            if not job or not hmac.compare_digest(job.owner_key, str(owner_key)):
                return None
            if job.state in self.terminal_states or job.state == "cancelling":
                return self._snapshot_locked(job)
            if not job.cancellation.cancel():
                job.stage = "Finishing saved answer"
                return self._snapshot_locked(job)
            if job.state == "queued":
                self.waiting.remove(job_id)
                job.state = "cancelled"
                job.stage = "Stopped before starting"
                job.completed_at = time.time()
                job.app = None
                job.completion_callback = None
                job.settings_update = {}
            else:
                job.state = "cancelling"
                job.stage = "Stopping this request…"
            self._persist_locked(job)
            self.condition.notify_all()
            return self._snapshot_locked(job)

    def wait_for_terminal(self, job_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                job = self.jobs[job_id]
                if job.state in self.terminal_states:
                    return self._snapshot_locked(job)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._snapshot_locked(job)
                self.condition.wait(timeout=remaining)

    def shutdown(self, timeout: float = 2.0) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        for worker in self.workers:
            worker.join(timeout=timeout)

    def _worker_loop(self) -> None:
        while True:
            with self.condition:
                while not self.waiting and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                job_id = self.waiting.popleft()
                job = self.jobs[job_id]
                job.state = "running"
                job.stage = "Starting evidence search"
                job.progress = 6
                job.started_at = time.time()
                self.active.add(job_id)
                self._persist_locked(job)
                self.condition.notify_all()
            try:
                result = None
                with cancellation_scope(job.cancellation), cancellable_lock(self.corpus_lock):
                    result = job.app.run_turn_for_session(
                        job.session_dir,
                        job.message,
                        job.settings_update,
                        progress_callback=lambda stage, current=job_id: self._set_progress(
                            current, stage
                        ),
                    )
                    with self.condition:
                        job.cancellation.commit()
                        job.result = result
                        job.state = "complete"
                        job.stage = "Complete"
                        job.progress = 100
                        job.completed_at = time.time()
            except TurnCancelled:
                with self.condition:
                    job.result = None
                    job.state = "cancelled"
                    job.stage = "Stopped by you"
                    job.completed_at = time.time()
            except Exception as exc:
                with self.condition:
                    cancelled = job.cancellation.event.is_set()
                    job.error = "" if cancelled else str(exc)
                    job.state = "cancelled" if cancelled else "failed"
                    job.stage = "Stopped by you" if cancelled else "Failed"
                    job.completed_at = time.time()
            finally:
                with self.condition:
                    self.active.discard(job_id)
                    self._persist_locked(job)
                    self.condition.notify_all()
                callback = job.completion_callback
                if callable(callback) and job.state != "cancelled":
                    try:
                        callback(job.result, job.error)
                    except Exception:


                        pass
                with self.condition:
                    self._release_terminal_locked(job)
                    callback = None
                    result = None
                    self.condition.notify_all()

    def _start_workers_locked(self) -> None:
        if not self.auto_worker or self.workers:
            return
        for worker_index in range(self.capacity):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"pfempire-turn-worker-{worker_index + 1}",
                daemon=True,
            )
            worker.start()
            self.workers.append(worker)

    def _set_progress(self, job_id: str, stage: str) -> None:
        checkpoint()
        with self.condition:
            job = self.jobs.get(job_id)
            if not job or job.state != "running":
                return
            job.stage = " ".join(str(stage).split())[:160]
            job.progress = progress_percent(job.stage)
            self._persist_locked(job)
            self.condition.notify_all()

    def _snapshot_locked(self, job: TurnJob) -> dict:
        jobs_ahead = 0
        queue_position = None
        if job.state == "queued":
            try:
                waiting_index = list(self.waiting).index(job.job_id)
            except ValueError:
                waiting_index = 0
            jobs_ahead = len(self.active) + waiting_index
            queue_position = jobs_ahead + 1
        return {
            "job_id": job.job_id,
            "session_dir": relative_path(job.session_dir),
            "message": job.message,
            "state": job.state,
            "stage": job.stage,
            "progress": job.progress,
            "jobs_ahead": jobs_ahead,
            "queue_position": queue_position,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "error": job.error,
            "can_cancel": job.state in {"queued", "running"} and not job.cancellation.committed,
        }

    def _persist_locked(self, job: TurnJob) -> None:
        job_dir = job.session_dir / "turn_jobs"
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            payload = self._snapshot_locked(job)
            payload.update(
                schema_version="pfempire_turn_job_v1",
                owner_scope_hash=hashlib.sha256(job.owner_key.encode("utf-8")).hexdigest(),
                result_output_dir=(
                    str((job.result or {}).get("output_dir") or job.result_output_dir)
                    if job.state == "complete"
                    else ""
                ),
            )
            write_json_atomically(job_dir / f"{job.job_id}.json", payload)
        except OSError:


            pass

    def _release_terminal_locked(self, job: TurnJob) -> None:
        if job.state not in self.terminal_states:
            return
        if job.state == 'complete' and job.result is not None:
            try:
                directory = job.session_dir / 'turn_jobs'
                if directory.is_symlink() or directory.resolve().parent != job.session_dir.resolve():
                    raise OSError('Unsafe job result directory.')
                result_path = directory / f'{job.job_id}.result.json'
                write_json_atomically(result_path, {'schema_version': 'originrag_job_result_v1', 'job_id': job.job_id,
                    'owner_scope_hash': hashlib.sha256(job.owner_key.encode()).hexdigest(), 'result': job.result}, mode=0o600)
                job.result_output_dir = str(job.result.get('output_dir') or '')
                job.result_path = result_path
                job.result = None
            except (OSError, ValueError, TypeError):
                pass
        job.app = None
        job.completion_callback = None
        job.settings_update = {}

    @staticmethod
    def _read_result(path: Path, owner_key: str, job_id: str, session_dir: Path) -> dict | None:
        expected = session_dir / 'turn_jobs' / f'{job_id}.result.json'
        try:
            if path != expected or path.is_symlink() or path.parent.is_symlink():
                return None
            if path.resolve().parent.parent != session_dir.resolve():
                return None
            with path.open(encoding='utf-8') as handle:
                payload = json.load(handle)
            if (payload.get('schema_version') != 'originrag_job_result_v1' or payload.get('job_id') != job_id
                    or payload.get('owner_scope_hash') != hashlib.sha256(owner_key.encode()).hexdigest()
                    or not isinstance(payload.get('result'), dict)):
                return None
            return payload['result']
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def _prune_locked(self) -> None:
        """Bound in-memory terminal history while preserving on-disk job records."""

        cutoff = time.time() - 24 * 60 * 60
        removable = [
            job
            for job in self.jobs.values()
            if job.state in self.terminal_states
            and job.completed_at
            and job.completed_at < cutoff
        ]
        if len(self.jobs) - len(removable) > 2000:
            retained_terminal = sorted(
                (
                    job
                    for job in self.jobs.values()
                    if job.state in self.terminal_states and job not in removable
                ),
                key=lambda item: item.completed_at or item.created_at,
            )
            removable.extend(retained_terminal[: len(self.jobs) - len(removable) - 2000])
        for job in removable:
            self.jobs.pop(job.job_id, None)


class LibrarySyncManager:
    """Safely stage web uploads and serialize atomic library refreshes."""

    def __init__(
        self,
        inbox: Path,
        *,
        importer=None,
        corpus_lock: threading.RLock | None = None,
        read_cache_refresh=None,
        refresh_callback=None,
        auto_worker: bool = True,
        metadata_only: bool = False,
    ) -> None:
        self.inbox = Path(inbox)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.collection_uploads = CollectionUploads(self.inbox / '.collection-uploads.sqlite')
        self.metadata_only = metadata_only
        self.importer = importer or self._import
        self.corpus_lock = corpus_lock or threading.RLock()
        self.read_cache_refresh = (
            read_cache_refresh or (lambda: None) if metadata_only else read_cache_refresh or refresh_configured_retrieval_index_cache
        )
        self.refresh_callback = refresh_callback or clear_runtime_library_caches
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.state: dict[str, object] = {
            "state": "idle",
            "stage": "",
            "progress": 0,
            "total": 0,
            "processed": 0,
            "imported": 0,
            "duplicates": 0,
            "review_required": 0,
            "rejected": 0,
            "current_file": "",
            "error": "",
            "completed_at": 0.0,
        }
        self.worker: threading.Thread | None = None
        if auto_worker:
            self.worker = threading.Thread(
                target=self._worker_loop,
                name="pfempire-library-sync",
                daemon=True,
            )
            self.worker.start()
            if any(self.inbox.glob("*.pdf")):
                self.schedule()

    def _import(self, inbox, **kwargs):
        return import_paper_inbox.import_inbox(inbox, build_retrieval=not self.metadata_only, **kwargs)

    @property
    def busy(self) -> bool:
        with self.lock:
            return str(self.state.get("state")) in {"queued", "running"}

    def status(self) -> dict[str, object]:
        with self.lock:
            payload = dict(self.state)
        if (
            str(payload.get("state")) == "complete"
            and time.time() - float(payload.get("completed_at") or 0) > 10
        ):
            payload.update(state="idle", stage="", progress=0)
        payload["pending_files"] = len(list(self.inbox.glob("*.pdf")))
        return payload

    def store_upload(
        self,
        stream,
        *,
        content_length: int,
        filename: str,
        doi: str = "",
        uploaded_by: str = "",
        schedule_import: bool = True,
        collection_directory: Path | None = None,
        collection: str = '',
    ) -> dict[str, object]:
        if content_length < 5:
            raise ValueError("Uploaded PDF is empty.")
        if content_length > MAX_LIBRARY_UPLOAD_BYTES:
            raise ValueError("PDF exceeds the 100 MB web-upload limit.")
        original = Path(str(filename or "paper.pdf")).name
        if not original.lower().endswith(".pdf"):
            raise ValueError("Only PDF files can be uploaded.")
        clean_doi = normalize_doi(doi)
        if doi and not clean_doi:
            raise ValueError("The supplied DOI is invalid.")
        upload_id = secrets.token_hex(10)
        if collection:
            if collection_directory is None:
                raise ValueError('Collection owner is required.')
            self.collection_uploads.add(upload_id, collection_directory, collection)
        safe_stem = import_paper_inbox.safe_filename(Path(original).stem)
        target = self.inbox / f"{upload_id}__{safe_stem}.pdf"
        temporary = target.with_suffix(".pdf.uploading")
        remaining = content_length
        try:
            with temporary.open("xb") as handle:
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("PDF upload ended before Content-Length bytes arrived.")
                    handle.write(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            with temporary.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("Uploaded file does not have a PDF signature.")
            temporary.replace(target)
            if clean_doi:
                target.with_suffix(target.suffix + ".doi.txt").write_text(
                    clean_doi + "\n", encoding="utf-8"
                )
            target.with_suffix(target.suffix + ".upload.json").write_text(
                json.dumps(
                    {
                        "schema_version": "pfempire_web_upload_v1",
                        "upload_id": upload_id,
                        "original_filename": original,
                        "uploaded_by_user_id": str(uploaded_by or ""),
                        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            self.collection_uploads.discard(upload_id)
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            target.with_suffix(target.suffix + ".doi.txt").unlink(missing_ok=True)
            target.with_suffix(target.suffix + ".upload.json").unlink(missing_ok=True)
            raise
        if schedule_import:
            self.schedule()
        return {
            "upload_id": upload_id,
            "filename": original,
            "stored_filename": target.name,
            "bytes": content_length,
            "doi": clean_doi,
            "status": "queued" if schedule_import else "staged",
        }

    def schedule(self) -> None:
        with self.lock:
            if str(self.state.get("state")) != "running":
                self.state.update(
                    state="queued",
                    stage="Waiting to update the shared library",
                    progress=2,
                    total=0,
                    processed=0,
                    imported=0,
                    duplicates=0,
                    review_required=0,
                    rejected=0,
                    batch_id="",
                    results=[],
                    error="",
                    completed_at=0.0,
                )
        self.wake.set()

    def run_pending_now(self) -> dict[str, object]:
        """Synchronous entrypoint used by the worker and deterministic tests."""
        with self.corpus_lock:
            self._update(state="running", stage="Starting library update", progress=5)
            try:
                summary = self.importer(
                    self.inbox,
                    timeout=30,
                    progress=self._import_progress,
                )
                if int(summary.get("imported") or 0) > 0:
                    self._update(stage="Preparing the fast search cache", progress=94)
                    self.read_cache_refresh()
                self._update(stage="Refreshing the library and linking collections", progress=98)
                self.refresh_callback()
                if self.collection_uploads.path.exists():
                    self.collection_uploads.finish(summary.get('results', []), library_catalog())
            except Exception as exc:
                self._update(
                    state="error",
                    stage="Library update failed",
                    error=" ".join(str(exc).split())[:500],
                )
                raise
        summary_values = {
            key: summary.get(key, 0)
            for key in (
                "scanned",
                "imported",
                "duplicates",
                "review_required",
                "rejected",
                "batch_id",
                "results",
            )
        }
        with self.lock:
            more_waiting = any(self.inbox.glob("*.pdf"))
            if more_waiting:
                self.state.update(
                    state="queued",
                    stage="More uploaded papers are waiting",
                    progress=2,
                    current_file="",
                    completed_at=0.0,
                    **summary_values,
                )
                self.wake.set()
            else:
                self.state.update(
                    state="complete",
                    stage="Library updated",
                    progress=100,
                    current_file="",
                    completed_at=time.time(),
                    **summary_values,
                )
        return self.status()

    def _worker_loop(self) -> None:
        while True:
            self.wake.wait()

            while True:
                self.wake.clear()
                time.sleep(0.8)
                if not self.wake.is_set():
                    break
            try:
                self.run_pending_now()
            except Exception:
                pass

    def _import_progress(self, event: dict[str, object]) -> None:
        total = max(1, int(event.get("total") or 1))
        processed = int(event.get("processed") or 0)
        stage = str(event.get("stage") or "Updating library")
        percent = 10 + round(70 * min(processed, total) / total)
        if "index" in stage.lower():
            percent = 88
        self._update(state="running", progress=percent, **event)

    def _update(self, **values: object) -> None:
        with self.lock:
            self.state.update(values)


def clear_runtime_library_caches() -> None:
    library_catalog.cache_clear()
    paper_metadata_by_id.cache_clear()
    paper_design_context.cache_clear()
    loaded = sys.modules.get('ask_corpus')
    if loaded is not None:
        loaded._cached_document_lineage_context.cache_clear()


def refresh_configured_retrieval_index_cache() -> Path | None:
    """Refresh the derived read cache after a committed corpus/index update."""

    if configured_cache_path() is None:
        return None
    return prime_read_index_cache(
        REPO_ROOT / "data/indexes/pfempire_fts.sqlite",
        REPO_ROOT / "data/chunks",
        REPO_ROOT / "data/metadata/papers.tsv",
    )


def _catalog_text(value: object) -> str:
    return " ".join(str(value or "").split())


_WORD_DASH = re.compile(r"(?<=[^\W\d_])[-‐‑‒–—](?=[^\W\d_])")


@lru_cache(maxsize=8192)
def _catalog_words(value: str) -> str:
    folded = value.casefold()
    if not folded.isascii():
        folded = "".join(c for c in unicodedata.normalize("NFD", folded)
                         if not unicodedata.combining(c))
    return _WORD_DASH.sub(" ", folded)


@lru_cache(maxsize=4)
def library_catalog(
    registry_path: str = str(REPO_ROOT / "data/metadata/paper_registry.tsv"),
    papers_path: str = str(REPO_ROOT / "data/metadata/papers.tsv"),
    bib_dir: str = "",
    chunks_dir: str = str(REPO_ROOT / "data/chunks"),
) -> tuple[dict[str, object], ...]:
    """Return the local paper catalogue without exposing filesystem paths."""
    registry_file = Path(registry_path)
    papers_file = Path(papers_path)
    if bib_dir:
        resolved_bib_dir = Path(bib_dir)
    else:
        load_project_env(REPO_ROOT / ".env")
        try:
            resolved_bib_dir = get_bib_dir()
        except OriginRAGConfigError:
            resolved_bib_dir = REPO_ROOT / "bib"
    resolved_chunks_dir = Path(chunks_dir)

    records: dict[str, dict[str, str]] = {}
    if papers_file.is_file():
        with papers_file.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                paper_id = _catalog_text(row.get("paper_id"))
                if paper_id:
                    records[paper_id] = {key: _catalog_text(value) for key, value in row.items()}
    if registry_file.is_file():
        with registry_file.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                paper_id = _catalog_text(row.get("paper_id"))
                if not paper_id:
                    continue
                current = records.setdefault(paper_id, {})
                for key, value in row.items():
                    clean = _catalog_text(value)
                    if clean:
                        current[key] = clean

    catalogue = []
    for paper_id, row in records.items():
        title = _catalog_text(row.get("title")) or paper_id
        author = _catalog_text(row.get("author"))
        year = _catalog_text(row.get("year"))
        journal = _catalog_text(row.get("journal"))
        doi = normalize_doi(row.get("doi"))
        local_relpath = _catalog_text(row.get("local_pdf_relpath"))
        pdf_exists = bool(local_relpath and (resolved_bib_dir / local_relpath).is_file())


        if local_relpath and not pdf_exists:
            pdf_exists = (REPO_ROOT / "bib" / local_relpath).is_file()
        chunks = resolved_chunks_dir / f"{paper_id}.jsonl"
        try:
            searchable = chunks.is_file() and chunks.stat().st_size > 0
        except FileNotFoundError:
            searchable = False
        catalogue.append(
            {
                "paper_id": paper_id,
                "title": title,
                "author": author,
                "year": year,
                "journal": journal,
                "doi": doi,
                **{field: _catalog_text(row.get(field)) for field in ("authors", "full_authors", "volume", "issue", "pages", "publisher", "document_type") if row.get(field)},
                "doi_url": f"https://doi.org/{doi}" if doi else "",
                "collection": _catalog_text(row.get("collection")),
                "category": _catalog_text(row.get("category") or row.get("domain")),
                "local_pdf": pdf_exists,
                "searchable": searchable,
                "summary_available": searchable or bool(_catalog_text(row.get("abstract"))),
            }
        )
    return tuple(
        sorted(
            assign_library_topics(catalogue),
            key=lambda item: (
                str(item["author"]).casefold() or "~",
                str(item["year"]),
                str(item["title"]).casefold(),
            ),
        )
    )


def list_scope(settings):
    fields = (settings.papers, settings.chunks_dir, settings.retrieval_index, settings.category)
    return hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()


def resume_quick_list(more, saved_turns, settings):
    from originrag.retrieval.quick_list import PaperList

    lang, count = more
    request = None
    seen = set()
    scope = list_scope(settings)
    for turn in reversed(saved_turns):
        if turn.get("task") != "sources":
            break
        result = saved_turn_result(turn)
        page = result.get("list_page")
        if not result.get("quick_list") or not page or page.get("scope") != scope:
            break
        if request is None:
            request = PaperList(page["topic"], count or page["limit"], lang)
        if page["topic"] != request.topic:
            break
        seen.update(p["paper_id"] for p in result["sources"])
        if page["first"]:
            return request, seen
    return None, set()


def quick_paper_list(request, catalogue, settings, *, seen_ids=frozenset()):
    if settings.category:
        catalogue = tuple(p for p in catalogue if p.get("category") == settings.category)
    total_catalogue = len(catalogue)
    if seen_ids:
        catalogue = tuple(p for p in catalogue if p["paper_id"] not in seen_ids)
    page = search_library_catalog(request.topic, limit=request.limit, catalogue=catalogue)
    rows = page["items"]
    ids = {p["paper_id"] for p in rows}
    de = request.language == "de"
    scope = "Katalog" if de else "catalogue"
    if request.topic and len(rows) < request.limit:
        from originrag.retrieval.quick_list import indexed_list_ids

        available = {p["paper_id"]: p for p in catalogue if p["searchable"] and p["paper_id"] not in ids}
        ranked = indexed_list_ids(request, REPO_ROOT / settings.retrieval_index, REPO_ROOT / settings.chunks_dir,
                                  REPO_ROOT / settings.papers, eligible=available, limit=request.limit - len(rows)) if available else None
        if ranked is not None:
            scope = "Katalog und Volltextindex" if de else "catalogue + full-text index"
            rows.extend(available[k] for k in ranked)
    count = len(rows)
    answer = (f"{count} Treffer zu „{request.topic}“" if de else
              f"{count} {'match' if count == 1 else 'matches'} for “{request.topic}”")
    if request.topic:
        if request.language == "fr":
            answer = f"{count} résultat{'s' if count != 1 else ''} pour « {request.topic} »"
            scope = "catalogue et index du texte intégral" if "+" in scope else "catalogue"
        elif request.language == "es":
            answer = f"{count} resultado{'s' if count != 1 else ''} para «{request.topic}»"
            scope = "catálogo e índice de texto completo" if "+" in scope else "catálogo"
        answer += f" ({scope}):" if count else f" ({scope})."
    else:
        total = total_catalogue
        answer = (f"Dein Katalog enthält {total} Paper. Hier siehst du {count}." if de else
                  f"Your catalogue contains {total} {'paper' if total == 1 else 'papers'}. Showing {count}.")
    if seen_ids and count == 0:
        answer = {"de": f"Keine weiteren Treffer in dieser Suche ({scope}).",
                  "en": f"No more matches in this search ({scope}).",
                  "fr": f"Aucun autre résultat pour cette recherche ({scope}).",
                  "es": f"No hay más resultados para esta búsqueda ({scope})."}[request.language]
    answer += "".join(f"\n- {p['title']}" + (f" ({p['year']})" if p.get("year") else "") for p in rows)
    return {"resolved_task": "sources", "quick_list": True, "answer": answer,
            "sources": [{k: p.get(k) for k in ("paper_id", "title", "year", "doi", "local_pdf")} for p in rows]}


def search_library_catalog(
    query: str = "",
    *,
    offset: int = 0,
    limit: int = 40,
    scope: str = "",
    catalogue: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    """Search author/title metadata deterministically and paginate the result."""
    clean_query = _catalog_text(query).casefold()
    # Keep punctuation literal for identifiers even when words in a title use different dashes.
    clean_query = " ".join(t if "/" in t or "_" in t else _catalog_words(t) for t in clean_query.split())
    query_tokens = clean_query.split()
    source = catalogue if catalogue is not None else library_catalog()
    clean_scope = _catalog_text(scope).casefold()
    if clean_scope in {"p_falciparum", "falciparum", "plasmodium_falciparum"}:
        source = tuple(item for item in source if item.get("falciparum_scope"))
    elif clean_scope.startswith("pf:"):
        topic_key = clean_scope[3:]
        source = tuple(
            item
            for item in source
            if str(item.get("falciparum_topic") or "").casefold() == topic_key
        )

    def match_score(item):
        title = _catalog_words(str(item.get("title") or ""))
        author = _catalog_words(str(item.get("author") or ""))
        other = " ".join(
            str(item.get(field) or "").casefold()
            for field in (
                "journal", "year", "doi", "paper_id", "category", "collection", "topic",
                "topic_label", "falciparum_topic", "falciparum_topic_label",
            )
        )
        coauthors = _catalog_words(str(item.get("full_authors") or item.get("authors") or ""))
        searchable_text = " ".join((title, author, coauthors, other))
        if not all(token in searchable_text for token in query_tokens):
            return None
        rank = 0
        if clean_query == title or clean_query == author:
            rank += 1000
        if title.startswith(clean_query):
            rank += 500
        if author.startswith(clean_query):
            rank += 480
        if clean_query in title:
            rank += 250
        if clean_query in author:
            rank += 230
        rank += 40 * sum(token in title for token in query_tokens)
        rank += 35 * sum(token in author for token in query_tokens)
        rank += 20 * sum(token in coauthors and token not in author for token in query_tokens)
        rank += 10 * sum(token in other for token in query_tokens)
        return (-rank, author or "~", title)

    if clean_query:
        ranked = [(score, item) for item in source if (score := match_score(item)) is not None]
        ranked.sort(key=lambda row: row[0])
        matches = [item for _, item in ranked]
    else:
        matches = list(source)
    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(100, int(limit)))
    page = matches[safe_offset : safe_offset + safe_limit]
    return {
        "items": page,
        "total": len(matches),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": safe_offset + len(page) < len(matches),
    }


def library_catalog_stats(
    catalogue: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    """Summarize the shared catalogue for the persistent header tracker."""

    source = catalogue if catalogue is not None else library_catalog()
    category_counts = Counter(
        _catalog_text(item.get("category") or item.get("collection"))
        or "uncategorized"
        for item in source
    )
    assigned = source if all(item.get("topic") for item in source) else assign_library_topics(source)
    topic_counts = Counter((item["topic"], item["topic_label"]) for item in assigned)
    groups = [{"group": key, "label": label, "count": count}
              for (key, label), count in sorted(topic_counts.items(), key=lambda pair: (-pair[1], pair[0]))]
    falciparum_topics = Counter(
        (item["falciparum_topic"], item["falciparum_topic_label"])
        for item in assigned
        if item.get("falciparum_scope")
    )
    return {
        "total": len(source),
        "searchable": sum(bool(item.get("searchable")) for item in source),
        "local_pdf": sum(bool(item.get("local_pdf")) for item in source),
        "categories": [
            {"category": category, "count": count}
            for category, count in sorted(
                category_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "groups": groups,
        "falciparum_scope_count": sum(falciparum_topics.values()),
        "falciparum_topics": [
            {"topic": key, "label": label, "count": count}
            for (key, label), count in sorted(
                falciparum_topics.items(), key=lambda item: (-item[1], item[0][1])
            )
        ],
    }


def progress_percent(stage: str) -> int:
    normalized = str(stage or "").lower()
    phases = (
        ("waiting", 4),
        ("planning", 10),
        ("searching", 25),
        ("selecting", 43),
        ("checking", 58),
        ("explaining", 68),
        ("writing", 72),
        ("validating", 92),
    )
    return next((percent for marker, percent in phases if marker in normalized), 6)


def create_server(
    host: str,
    port: int,
    handler: type[BaseHTTPRequestHandler],
    attempts: int = 20,
) -> ThreadingHTTPServer:
    if port == 0:
        return ThreadingHTTPServer((host, port), handler)
    last_error: OSError | None = None
    for candidate_port in range(port, port + max(1, attempts)):
        try:
            return ThreadingHTTPServer((host, candidate_port), handler)
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"Could not bind {host}:{port}-{port + attempts - 1}: {last_error}")


def build_handler(
    app: WebChatApp | None,
    *,
    auth_store: AuthStore | None = None,
    app_registry: AuthenticatedWebChatRegistry | None = None,
    secure_cookies: bool = False,
    max_concurrent_turns: int = 1,
    library_manager: LibrarySyncManager | None = None,
    profile: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    runtime_profile = app_profile(profile if profile is not None else (
        app_registry.settings.app_profile if app_registry else app.settings.app_profile if app else None))
    if auth_store and not app_registry:
        raise ValueError("Authenticated web chat requires an app registry.")
    if not auth_store and not app:
        raise ValueError("Web chat requires an app.")

    anonymous_review_lock = threading.Lock()
    writing_collaboration = WritingCollaboration(REPO_ROOT / app_registry.out_dir, auth_store) if auth_store and runtime_profile.write else None
    collaboration_routes = {'/api/writing/collaboration', '/api/writing/collaborators'}
    topic_discovery = TopicDiscovery()
    paper_imports = PaperImports(library_manager)
    local_import_token = secrets.token_urlsafe(32)
    corpus_lock = library_manager.corpus_lock if library_manager else threading.RLock()
    writing_slots = threading.BoundedSemaphore(2)
    turn_jobs = TurnJobManager(
        max_concurrent_turns,
        corpus_lock=corpus_lock,
    ) if runtime_profile.rag else None

    class OriginRAGWebHandler(BaseHTTPRequestHandler):
        server_version = "OriginRAGWebChat/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not runtime_profile.permits(parsed.path):
                self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if parsed.path in collaboration_routes and writing_collaboration is None:
                self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/login" and auth_store:
                if self.authenticated_context(send_error=False):
                    targets = parse_qs(parsed.query).get('next', [])
                    self.respond_redirect(safe_login_target(targets[0] if len(targets) == 1 else None))
                else:
                    self.respond_text(LOGIN_HTML, content_type="text/html; charset=utf-8")
                return
            if parsed.path == "/api/auth-status" and auth_store:
                context = self.authenticated_context(send_error=False)
                if not context:
                    self.respond_json({"authenticated": False}, status=HTTPStatus.UNAUTHORIZED)
                    return
                _, user, _ = context
                self.respond_json({"authenticated": True, "user": public_user(user)})
                return
            context = self.request_context()
            if not context:
                return
            current_app, user, _ = context
            if parsed.path == '/api/document-sync':
                self.handle_document_sync(current_app)
                return
            if parsed.path == "/api/models":
                local = not auth_store and local_browser_request(
                    self.server.server_address, self.client_address, self.headers)
                if not user and not local:
                    self.respond_json({"error": "Use a local browser or sign in."}, status=HTTPStatus.FORBIDDEN)
                    return
                can_manage = bool(local or model_manage_allowed(user))
                manager = manager_for(REPO_ROOT)
                search = parse_qs(parsed.query).get('search', [None])[0]
                try:
                    search_rows = manager.search(search) if search is not None else None
                    snapshot = manager.snapshot(can_manage, search_rows=search_rows)
                except (ValueError, OSError) as exc:
                    self.respond_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                snapshot["can_use"] = bool(local or user)
                snapshot["can_download"] = bool(local or model_download_allowed(user))
                snapshot["local_token"] = local_import_token if local else ""
                self.respond_json(snapshot)
                return
            owner_key = user.user_id if user else "anonymous"
            if turn_jobs is not None and not isinstance(current_app, WorkspaceApp):
                turn_jobs.recover_persisted(owner_key, current_app)
            if parsed.path == '/api/app-profile':
                self.respond_json({**runtime_profile.public(), 'authenticated': bool(user),
                                   'local_document_sync': runtime_profile.write and not auth_store and
                                   local_browser_request(self.server.server_address, self.client_address, self.headers),
                                   'web_port': self.server.server_address[1],
                                   'user': public_user(user) if user else None})
                return
            if parsed.path == '/api/library-access':
                self.respond_json({'library_access': self.library_access(user)})
                return
            if parsed.path == '/' and not runtime_profile.rag:
                self.respond_redirect(runtime_profile.home)
                return
            if parsed.path in {"/", "/chat"}:
                self.respond_text(navigation_for_profile(INDEX_HTML, runtime_profile, accounts_enabled=bool(auth_store)), content_type="text/html; charset=utf-8")
                return
            if parsed.path in {'/folio', '/api/folio', '/api/folio/export'}:
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({'error': 'Use the local browser or sign in to open your documents.'}, status=HTTPStatus.FORBIDDEN)
                    return
                if parsed.path == '/folio':
                    self.respond_text(navigation_for_profile(FOLIO_HTML, runtime_profile, accounts_enabled=bool(auth_store)), content_type='text/html; charset=utf-8')
                else:
                    handle_folio(self, parsed.path, parsed.query, REPO_ROOT / current_app.out_dir)
                return
            if parsed.path in WORKSPACE_PAGES or parsed.path in {"/api/writing", "/api/writing/export", "/api/writing/bibliography"} | collaboration_routes:
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to open your workspace."}, status=HTTPStatus.FORBIDDEN)
                    return
                if parsed.path in WORKSPACE_PAGES:
                    self.respond_text(navigation_for_profile(WORKSPACE_HTML, runtime_profile, accounts_enabled=bool(auth_store)), content_type="text/html; charset=utf-8")
                else:
                    if not runtime_profile.write:
                        self.handle_collection_request(parsed, current_app)
                        return
                    handle_writing(self, parsed.path, parsed.query, REPO_ROOT / current_app.out_dir, library_catalog,
                                   collaboration=writing_collaboration, user=user)
                return
            if parsed.path in {"/reader", "/api/reader/paper", "/api/reader/notes", "/api/reader/notebook"}:
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to read papers."}, status=HTTPStatus.FORBIDDEN)
                    return
                if parsed.path == "/reader":
                    self.respond_text(navigation_for_profile(READER_HTML, runtime_profile, accounts_enabled=bool(auth_store)), content_type="text/html; charset=utf-8")
                    return
                try:
                    notebook = parsed.path == "/api/reader/notebook"
                    params = (parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                                       max_num_fields=8, errors="strict") if notebook else parse_qs(parsed.query))
                    expected = {"paper_id", "fingerprint"} if parsed.path.endswith("/notes") else {"paper_id"}
                    if set(params) != expected or any(len(value) != 1 for value in params.values()):
                        raise ValueError("Invalid reader request.")
                    paper_id = params["paper_id"][0]
                    if notebook:
                        notebook_paper_key(paper_id)
                    paper = next((p for p in library_catalog() if p["paper_id"] == paper_id), None)
                    if not paper:
                        self.respond_json({"error": "Paper not found in the library."}, status=HTTPStatus.NOT_FOUND)
                        return
                    if parsed.path.endswith("/paper"):
                        self.respond_json({"paper_id": paper_id, "title": paper.get("title", "")})
                    elif notebook:
                        self.respond_json(ReaderNotes(REPO_ROOT / current_app.out_dir).notebook_snapshot(paper_id))
                    else:
                        self.respond_json(ReaderNotes(REPO_ROOT / current_app.out_dir).snapshot(paper_id, params["fingerprint"][0]))
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your notes are temporarily unavailable. Please retry."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path.startswith("/assets/"):
                self.handle_asset_request(parsed.path)
                return
            if parsed.path == "/api/status":
                if not runtime_profile.rag:
                    self.respond_json({**runtime_profile.public(), 'library_access': self.library_access(user),
                                       'user': public_user(user) if user else None})
                    return
                owner_key = user.user_id if user else "anonymous"
                payload = {
                    "session_dir": relative_path(current_app.session_dir),
                    "settings": current_app.settings.__dict__,
                    "queue": turn_jobs.global_status(),
                    "jobs": turn_jobs.owner_jobs(owner_key),
                    "library_access": self.library_access(user),
                }
                if user and auth_store:
                    payload["user"] = public_user(user)
                    payload["quota"] = auth_store.quota_status(user.user_id).__dict__
                    payload["quota"]["saved_chats_used"] = len(
                        list_saved_sessions(current_app.out_dir, current_app.session_dir)
                    )
                    payload["quota"]["saved_chats_limit"] = MAX_SAVED_CHATS
                self.respond_json(payload)
                return
            if parsed.path == "/api/progress":
                request_id = (parse_qs(parsed.query).get("request_id") or [""])[0]
                owner_key = user.user_id if user else "anonymous"
                progress = turn_jobs.job(owner_key, request_id)
                self.respond_json(progress or {"stage": "", "progress": 0})
                return
            if parsed.path == "/api/jobs":
                owner_key = user.user_id if user else "anonymous"
                payload = {
                    "queue": turn_jobs.global_status(),
                    "jobs": turn_jobs.owner_jobs(owner_key),
                }
                if (parse_qs(parsed.query).get("details") or [""])[0] == "1":
                    scope, queue_jobs = public_queue_details(
                        turn_jobs, user, auth_store
                    )
                    payload["queue_scope"] = scope
                    payload["queue_jobs"] = queue_jobs
                self.respond_json(payload)
                return
            if parsed.path == "/api/job":
                job_id = (parse_qs(parsed.query).get("job_id") or [""])[0]
                owner_key = user.user_id if user else "anonymous"
                job = turn_jobs.job(owner_key, job_id)
                if not job:
                    self.respond_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                self.respond_json(job)
                return
            if parsed.path == "/api/library-status":
                if not library_manager:
                    self.respond_json({"state": "unavailable"})
                else:
                    self.respond_json(library_manager.status())
                return
            if parsed.path == "/api/paper-imports":
                self.respond_json({"jobs": paper_imports.snapshot(owner_key)})
                return
            if parsed.path == "/api/library":
                params = parse_qs(parsed.query)
                try:
                    offset = int((params.get("offset") or ["0"])[0])
                    limit = int((params.get("limit") or ["40"])[0])
                except ValueError:
                    self.respond_json(
                        {"error": "offset and limit must be integers"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                self.respond_json(
                    search_library_catalog(
                        (params.get("query") or [""])[0],
                        offset=offset,
                        limit=limit,
                        scope=(params.get("scope") or [""])[0],
                    )
                )
                return
            if parsed.path == "/api/reading":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to open your reading history."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    options = reading_query(parsed.query)
                    history = ReadingHistory(REPO_ROOT / current_app.out_dir)
                    self.respond_json(history.status(options["paper_id"]) if "paper_id" in options else history.snapshot(library_catalog(), **options))
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your reading history is temporarily unavailable. Please try again."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/shelf":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to open your shelf."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    self.respond_json(PersonalShelf(REPO_ROOT / current_app.out_dir).snapshot(library_catalog()))
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your shelf is temporarily unavailable. Please try again."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/library/pdf":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to read library PDFs."}, status=HTTPStatus.FORBIDDEN)
                    return
                params = parse_qs(parsed.query)
                ids = params.get("paper_id", [])
                if set(params) != {"paper_id"} or len(ids) != 1 or not 1 <= len(ids[0]) <= 160:
                    self.respond_json({"error": "A single paper_id is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.handle_library_pdf(ids[0])
                return
            if parsed.path == "/api/library-stats":
                self.respond_json(library_catalog_stats())
                return
            if parsed.path == "/api/discover-journals":
                params = parse_qs(parsed.query)
                try:
                    self.respond_json(topic_discovery.journals(owner_key, (params.get("query") or [""])[0]))
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except DiscoveryBusy as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
                except DiscoveryUnavailable as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            if parsed.path == "/api/discover-papers":
                params = parse_qs(parsed.query)
                try:
                    result = topic_discovery.search(
                        owner_key,
                        query=(params.get("query") or [""])[0],
                        since=(params.get("since") or [""])[0],
                        order=(params.get("order") or ["relevance"])[0],
                        kind=(params.get("kind") or ["journal-article"])[0],
                        issns=params.get("issn", []),
                    )
                    catalogue = library_catalog()
                    known = {normalize_doi(str(item.get("doi") or "")) for item in catalogue}
                    for item in result["items"]:
                        item["in_library"] = normalize_doi(item["doi"]) in known
                    ready = {normalize_doi(str(item.get("doi") or "")) for item in catalogue if item.get("searchable") and item.get("local_pdf")}
                    for item in result["items"]:
                        item["fulltext_ready"] = normalize_doi(item["doi"]) in ready
                    self.respond_json(result)
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except DiscoveryBusy as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
                except DiscoveryUnavailable as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            if parsed.path == "/api/sessions":
                owner_key = user.user_id if user else "anonymous"
                sessions = list_saved_sessions(
                    current_app.out_dir, current_app.session_dir
                )
                self.respond_json(
                    {
                        "sessions": sessions_with_job_status(
                            sessions,
                            turn_jobs.owner_jobs(owner_key),
                        )
                    }
                )
                return
            if parsed.path == "/api/session":
                params = parse_qs(parsed.query)
                try:
                    response = current_app.read_session(
                        (params.get("session_dir") or [""])[0]
                    )
                except SessionNotFoundError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                owner_key = user.user_id if user else "anonymous"
                response["jobs"] = turn_jobs.session_jobs(
                    owner_key,
                    safe_session_dir(
                        current_app.out_dir,
                        str(response["session_dir"]),
                    ),
                )
                self.respond_json(response)
                return
            if parsed.path == "/file":
                self.handle_file_request(parsed.query, current_app)
                return
            self.respond_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not runtime_profile.permits(parsed.path):
                self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if parsed.path in collaboration_routes and writing_collaboration is None:
                self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/api/login" and auth_store:
                self.handle_login()
                return
            if parsed.path == "/api/register" and auth_store:
                self.handle_registration()
                return
            context = self.request_context()
            if not context:
                return
            current_app, user, session_token = context
            owner_key = user.user_id if user else "anonymous"
            if turn_jobs is not None and not isinstance(current_app, WorkspaceApp):
                turn_jobs.recover_persisted(owner_key, current_app)
            if auth_store and not self.valid_csrf(session_token):
                self.respond_json({"error": "invalid CSRF token"}, status=HTTPStatus.FORBIDDEN)
                return
            if parsed.path == '/api/document-sync':
                self.handle_document_sync(current_app, post=True)
                return
            if parsed.path == "/api/models":
                local = not auth_store and local_browser_request(
                    self.server.server_address, self.client_address, self.headers)
                supplied = str(self.headers.get("X-OriginRAG-Local-Token") or "")
                try:
                    payload = self.read_json_body()
                    action = payload.get("action") if isinstance(payload, dict) else None
                    permitted_user = (
                        model_download_allowed(user) if action in {"download", "cancel"}
                        else bool(user) if action in {"select", "default", "benchmark"}
                        else model_manage_allowed(user)
                    )
                    if not ((auth_store and permitted_user) or
                            (local and supplied and hmac.compare_digest(supplied, local_import_token))):
                        self.respond_json({"error": "Your account is not allowed to perform this model operation."},
                                          status=HTTPStatus.FORBIDDEN)
                        return
                    manager_for(REPO_ROOT).change(payload)
                    self.respond_json({"accepted": True}, status=HTTPStatus.ACCEPTED)
                except (ValueError, OSError) as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == '/api/folio':
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({'error': 'Use the local browser or sign in to edit documents.'}, status=HTTPStatus.FORBIDDEN)
                    return
                handle_folio(self, parsed.path, parsed.query, REPO_ROOT / current_app.out_dir, post=True)
                return
            if parsed.path == "/api/writing" or parsed.path in collaboration_routes:
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to write."}, status=HTTPStatus.FORBIDDEN)
                    return
                if not runtime_profile.write:
                    self.handle_collection_request(parsed, current_app, post=True)
                    return
                handle_writing(self, parsed.path, parsed.query, REPO_ROOT / current_app.out_dir, library_catalog, post=True,
                               collaboration=writing_collaboration, user=user, language_lock=corpus_lock, chat_slots=writing_slots,
                               model_manager=lambda: manager_for(REPO_ROOT), chunks_dir=REPO_ROOT / 'data/chunks')
                return
            if parsed.path == "/api/job/cancel":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to stop a request."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    payload = self.read_json_body()
                    job_id = payload.get("job_id")
                    if set(payload) != {"job_id"} or not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                        raise ValueError("A valid job_id is required.")
                    result = turn_jobs.cancel(owner_key, job_id)
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(result or {"error": "Request not found."}, status=HTTPStatus.OK if result else HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/api/reading":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to update your reading history."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    if parsed.query:
                        raise ValueError("Reading-history updates do not accept query parameters.")
                    self.respond_json(ReadingHistory(REPO_ROOT / current_app.out_dir).change(self.read_json_body(), library_catalog()))
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your reading history could not be saved. Please try again."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/shelf":
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to save papers."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    payload = self.read_json_body()
                    self.respond_json(PersonalShelf(REPO_ROOT / current_app.out_dir).change(payload, library_catalog()))
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your shelf could not be saved. Please try again."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path in {"/api/reader/notes", "/api/reader/notebook"}:
                if not auth_store and not local_browser_request(self.server.server_address, self.client_address, self.headers):
                    self.respond_json({"error": "Use the local browser or sign in to save notes."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    notebook = parsed.path == "/api/reader/notebook"
                    if notebook and parsed.query:
                        raise ValueError("Paper-note updates do not accept query parameters.")
                    payload = self.read_json_body(reject_duplicate_fields=True) if notebook else self.read_json_body()
                    if notebook:
                        validate_notebook(payload)
                        paper_id = payload["paper_id"]
                    else:
                        paper_id, _ = document_key(payload.get("paper_id"), payload.get("fingerprint"))
                    if not any(p["paper_id"] == paper_id for p in library_catalog()):
                        self.respond_json({"error": "Paper not found in the library."}, status=HTTPStatus.NOT_FOUND)
                        return
                    store = ReaderNotes(REPO_ROOT / current_app.out_dir)
                    self.respond_json(store.change_notebook(payload) if notebook else store.change(payload))
                except NoteConflict as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                except (OSError, sqlite3.Error):
                    self.respond_json({"error": "Your notes could not be saved. Please retry."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/logout" and auth_store and user:
                auth_store.logout(session_token)
                if app_registry:
                    app_registry.discard(user, session_token)
                self.respond_json(
                    {"ok": True},
                    extra_headers=expired_auth_cookie_headers(secure=secure_cookies),
                )
                return
            if parsed.path == "/api/reset":
                if auth_store:
                    try:
                        enforce_saved_chat_limit(current_app, creating_new=True)
                    except QuotaExceededError as exc:
                        self.respond_json(
                            {"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS
                        )
                        return
                self.respond_json(current_app.reset())
                return
            if parsed.path == "/api/session-title":
                try:
                    payload = self.read_json_body()
                    title = rename_saved_session(
                        out_dir=current_app.out_dir,
                        current_session_dir=current_app.session_dir,
                        session_dir_text=str(payload.get("session_dir") or ""),
                        title=str(payload.get("title") or ""),
                    )
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(
                    {
                        "title": title,
                        "sessions": list_saved_sessions(
                            current_app.out_dir, current_app.session_dir
                        ),
                    }
                )
                return
            if parsed.path == "/api/session-delete":
                try:
                    payload = self.read_json_body()
                    deleting_session = safe_session_dir(
                        current_app.out_dir,
                        str(payload.get("session_dir") or ""),
                    )
                    owner_key = user.user_id if user else "anonymous"
                    if turn_jobs.session_busy(owner_key, deleting_session):
                        self.respond_json(
                            {"error": "A queued or running job still belongs to this chat."},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    response = current_app.delete_session(
                        str(payload.get("session_dir") or "")
                    )
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(response)
                return
            if parsed.path == "/api/source-trace":
                try:
                    payload = self.read_json_body()
                    response = current_app.trace_source(
                        paper_id=str(payload.get("paper_id") or ""),
                        claim_text=str(payload.get("claim_text") or ""),
                    )
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(response)
                return
            if parsed.path == "/api/source-request":
                if user and user.role not in {"reviewer", "admin"}:
                    self.respond_json(
                        {"error": "reviewer role required"}, status=HTTPStatus.FORBIDDEN
                    )
                    return
                try:
                    payload = self.read_json_body()
                    reference = payload.get("reference") or {}
                    if not isinstance(reference, dict):
                        raise ValueError("reference must be an object")
                    response = current_app.queue_source(reference)
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(response)
                return
            if parsed.path == "/api/paper-import":
                if not self.can_write_library(user):
                    self.respond_json({"error": "Library import is unavailable or the local request token is invalid. Reload the page, or sign in on a shared server."}, status=HTTPStatus.FORBIDDEN)
                    return
                try:
                    payload = self.read_json_body()
                    if set(payload) != {"doi"} or not isinstance(payload["doi"], str):
                        raise ValueError("Supply a DOI only, not a download URL")
                    job = paper_imports.enqueue(owner_key, payload["doi"])
                    self.respond_json(job, status=HTTPStatus.ACCEPTED)
                except ImportBusy as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
                except ValueError as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/api/library-upload":
                if not self.can_write_library(user):
                    self.respond_json(
                        {"error": "Library upload is unavailable. Reload the local page or sign in on a shared server."},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                if not str(self.headers.get("Content-Type") or "").lower().startswith(
                    "application/pdf"
                ):
                    self.respond_json(
                        {"error": "Content-Type must be application/pdf"},
                        status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    response = library_manager.store_upload(
                        self.rfile,
                        content_length=length,
                        filename=unquote(str(self.headers.get("X-Paper-Filename") or "paper.pdf")),
                        doi=str(self.headers.get("X-Paper-DOI") or ""),
                        uploaded_by=owner_key,
                        collection_directory=REPO_ROOT / current_app.out_dir,
                        collection=str(self.headers.get('X-OriginRAG-Collection') or ''),
                        schedule_import=(
                            str(self.headers.get("X-Paper-Defer-Sync") or "") != "1"
                        ),
                    )
                except Exception as exc:
                    self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.respond_json(response, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/library-sync":
                if not self.can_write_library(user):
                    self.respond_json(
                        {"error": "Library update is unavailable. Reload the local page or sign in on a shared server."},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                library_manager.schedule()
                self.respond_json(library_manager.status(), status=HTTPStatus.ACCEPTED)
                return
            if parsed.path != "/api/chat":
                self.respond_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
                return
            message_text = ""
            if library_manager and library_manager.busy:
                self.respond_json(
                    {
                        "error": "The shared paper library is being updated. Please retry when the library status is complete."
                    },
                    status=HTTPStatus.CONFLICT,
                )
                return
            try:
                payload = self.read_json_body()
                message_text = str(payload.get("message") or "")
                requested_session = str(payload.get("session_dir") or "").strip()
                target_session = (
                    safe_session_dir(current_app.out_dir, requested_session)
                    if requested_session
                    else current_app.session_dir
                )
                settings_update = (
                    authenticated_turn_settings(payload.get("settings"))
                    if auth_store and user
                    else dict(payload.get("settings") or {})
                )



                settings_update["task"] = "auto"
                if auth_store and user:
                    enforce_saved_chat_limit(
                        current_app,
                        creating_new=False,
                        target_session=target_session,
                    )
                    auth_store.reserve_model_request(user.user_id)
                owner_key = user.user_id if user else "anonymous"
                review_conversation_id = current_app.review_conversation_id

                def record_review(result, error) -> None:
                    if not auth_store or not app_registry:
                        return
                    append_anonymous_review_event(
                        app_registry.out_dir,
                        conversation_id=review_conversation_id,
                        question=message_text,
                        response=(result or {"error": error, "task": "failed"}),
                        lock=anonymous_review_lock,
                    )

                response = turn_jobs.enqueue(
                    owner_key=owner_key,
                    app=current_app,
                    session_dir=target_session,
                    message=message_text,
                    settings_update=settings_update,
                    completion_callback=record_review,
                )
            except QuotaExceededError as exc:
                if auth_store and app_registry and message_text:
                    append_anonymous_review_event(
                        app_registry.out_dir,
                        conversation_id=current_app.review_conversation_id,
                        question=message_text,
                        response={"error": str(exc), "task": "rejected_before_run"},
                        lock=anonymous_review_lock,
                    )
                self.respond_json({"error": str(exc)}, status=HTTPStatus.TOO_MANY_REQUESTS)
                return
            except Exception as exc:
                if auth_store and app_registry and message_text:
                    append_anonymous_review_event(
                        app_registry.out_dir,
                        conversation_id=current_app.review_conversation_id,
                        question=message_text,
                        response={"error": str(exc), "task": "failed"},
                        lock=anonymous_review_lock,
                    )
                self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self.respond_json(response, status=HTTPStatus.ACCEPTED)

        def handle_collection_request(self, parsed, current_app, *, post=False):
            from originrag.writing import WritingStore, WritingConflict
            store = WritingStore(REPO_ROOT / current_app.out_dir)
            try:
                params = parse_qs(parsed.query)
                if post:
                    payload = self.read_json_body()
                    if payload.get('action') not in COLLECTION_ACTIONS:
                        self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                        return
                    self.respond_json(store.change(payload, library_catalog()))
                elif parsed.path == '/api/writing/bibliography':
                    self.respond_json({'bibtex': store.collection_bib(params.get('id', [''])[0], library_catalog())})
                elif 'collection_refs' in params:
                    self.respond_json(store.collection_references(params['collection_refs'][0], library_catalog()))
                elif 'id' in params and params.get('kind', ['projects'])[0] == 'collections':
                    data = store.get(params['id'][0], 'collections')
                    ids = set(data['papers'])
                    self.respond_json({**data, 'items': [p for p in library_catalog() if p['paper_id'] in ids]})
                elif params:
                    self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                else:
                    db = store.connect()
                    try:
                        rows = db.execute("SELECT id, data, revision FROM documents WHERE kind='collections' ORDER BY updated DESC").fetchall()
                        collections = []
                        for key, raw, revision in rows:
                            data = json.loads(raw)
                            collections.append({'id': key, 'name': data['name'], 'revision': revision, 'count': len(data['papers']), 'collection': ''})
                    finally:
                        db.close()
                    self.respond_json({'collections': collections, 'projects': [], 'templates': [],
                                       'compiler': {'available': False, 'reason': 'Quill is not enabled in this profile.'}})
            except WritingConflict as exc:
                self.respond_json({'error': str(exc)}, status=HTTPStatus.CONFLICT)
            except KeyError:
                self.respond_json({'error': 'Collection not found in your account.'}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self.respond_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except (OSError, sqlite3.Error):
                self.respond_json({'error': 'Your library is temporarily unavailable.'}, status=HTTPStatus.SERVICE_UNAVAILABLE)

        def request_context(
            self,
        ) -> tuple[WebChatApp, AuthenticatedUser | None, str] | None:
            if not auth_store:
                assert app is not None
                return app, None, ""
            context = self.authenticated_context(send_error=True)
            if not context:
                return None
            token, user, _ = context
            assert app_registry is not None
            if not runtime_profile.rag or urlparse(self.path).path not in CHAT_ROUTES | {'/', '/api/status'}:
                return WorkspaceApp(app_registry.settings, app_registry.out_dir / 'users' / user.user_id), user, token
            return app_registry.for_session(user, token), user, token

        def authenticated_context(
            self, *, send_error: bool
        ) -> tuple[str, AuthenticatedUser, object] | None:
            if not auth_store:
                return None
            token = self.cookie_value("pfempire_session")
            session = auth_store.get_session(token)
            if not session:
                if send_error:
                    if self.path.startswith("/api/") or self.path.startswith("/file"):
                        self.respond_json(
                            {"error": "authentication required"},
                            status=HTTPStatus.UNAUTHORIZED,
                        )
                    else:
                        target = safe_login_target(self.path)
                        self.respond_redirect('/login?' + urlencode({'next': target}) if target != '/' else '/login')
                return None
            return token, session.user, session

        def handle_login(self) -> None:
            assert auth_store is not None
            if not self.valid_public_auth_request():
                self.respond_json({"error": "invalid request"}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                payload = self.read_json_body()
                token, session = auth_store.authenticate(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    user_agent=str(self.headers.get("User-Agent") or ""),
                    remember=bool(payload.get("remember", False)),
                )
            except (AuthenticationError, ValueError, json.JSONDecodeError) as exc:
                self.respond_json({"error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)
                return
            self.respond_json(
                {"ok": True, "user": public_user(session.user)},
                extra_headers=auth_cookie_headers(
                    token,
                    session.csrf_token,
                    secure=secure_cookies,
                    max_age=max(1, session.expires_at - int(datetime.now().timestamp())),
                ),
            )

        def handle_registration(self) -> None:
            assert auth_store is not None
            if not self.valid_public_auth_request():
                self.respond_json({"error": "invalid request"}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                payload = self.read_json_body()
                password = str(payload.get("password") or "")
                if password != str(payload.get("password_confirm") or ""):
                    raise ValueError("Passwords do not match.")
                username = str(payload.get("username") or "")
                display_name = str(payload.get("display_name") or "").strip() or username
                auth_store.create_user(
                    username,
                    password,
                    display_name=display_name,
                )
                token, session = auth_store.authenticate(
                    username,
                    password,
                    user_agent=str(self.headers.get("User-Agent") or ""),
                    remember=bool(payload.get("remember", False)),
                )
            except (AuthenticationError, ValueError, json.JSONDecodeError) as exc:
                status = (
                    HTTPStatus.CONFLICT
                    if "already exists" in str(exc).lower()
                    else HTTPStatus.BAD_REQUEST
                )
                self.respond_json({"error": str(exc)}, status=status)
                return
            self.respond_json(
                {"ok": True, "user": public_user(session.user)},
                extra_headers=auth_cookie_headers(
                    token,
                    session.csrf_token,
                    secure=secure_cookies,
                    max_age=max(1, session.expires_at - int(datetime.now().timestamp())),
                ),
            )

        def handle_document_sync(self, app, *, post=False):
            import sqlite3
            from originrag.document_sync import DocumentSync, SyncConflict
            if auth_store or not runtime_profile.write or not local_browser_request(
                    self.server.server_address, self.client_address, self.headers):
                self.respond_json({'error': 'Folder sync is available on a private local installation only.'}, status=403)
                return
            if post and not hmac.compare_digest(self.headers.get('X-OriginRAG-Local-Token', '').encode(), local_import_token.encode()):
                self.respond_json({'error': 'Reopen Document sync and try again.'}, status=403)
                return
            try:
                result = DocumentSync(REPO_ROOT / app.out_dir, runtime_profile, library_catalog).run(
                    self.read_json_body(reject_duplicate_fields=True) if post else {'action': 'status'})
                if not post:
                    result['token'] = local_import_token
                self.respond_json(result)
            except SyncConflict as exc:
                self.respond_json({'error': str(exc)}, status=409)
            except (TypeError, KeyError, ValueError, RecursionError) as exc:
                self.respond_json({'error': str(exc) if isinstance(exc, ValueError) else 'Invalid or missing synced document.'}, status=400)
            except (OSError, sqlite3.Error):
                self.respond_json({'error': 'Cannot access the sync folder. Check that it is available and writable.'}, status=503)

        def library_access(self, user) -> dict:
            if not library_manager:
                return {"enabled": False, "mode": "unavailable"}
            if auth_store:
                return {"enabled": bool(user), "mode": "authenticated"}
            local = local_browser_request(self.server.server_address, self.client_address, self.headers)
            return {"enabled": local, "mode": "local" if local else "unavailable",
                    "csrf_token": local_import_token if local else ""}

        def can_write_library(self, user) -> bool:
            if not self.library_access(user)["enabled"]:
                return False
            if auth_store:
                return True
            supplied = str(self.headers.get("X-OriginRAG-Local-Token") or "")
            return bool(supplied) and hmac.compare_digest(supplied, local_import_token)

        def valid_csrf(self, session_token: str) -> bool:
            assert auth_store is not None
            header_token = str(self.headers.get("X-CSRF-Token") or "")
            cookie_token = self.cookie_value("pfempire_csrf")
            return bool(header_token and hmac.compare_digest(header_token, cookie_token)) and auth_store.verify_csrf(
                session_token, header_token
            )

        def valid_public_auth_request(self) -> bool:
            content_type = str(self.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("application/json"):
                return False
            origin = str(self.headers.get("Origin") or "").strip()
            if not origin:
                return True
            origin_host = urlparse(origin).netloc.lower()
            request_host = str(self.headers.get("Host") or "").strip().lower()
            return bool(origin_host and request_host and hmac.compare_digest(origin_host, request_host))

        def cookie_value(self, name: str) -> str:
            cookies = SimpleCookie()
            try:
                cookies.load(str(self.headers.get("Cookie") or ""))
            except Exception:
                return ""
            morsel = cookies.get(name)
            return morsel.value if morsel else ""

        def read_json_body(self, *, reject_duplicate_fields: bool = False) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            limit = {'/api/writing': 36_000_000, '/api/folio': 12_000_000}.get(urlparse(self.path).path, 1_000_000)
            if length < 0 or length > limit:
                raise ValueError("Request body is too large.")
            raw = self.rfile.read(length).decode("utf-8")
            if not raw.strip():
                return {}
            def unique_fields(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("JSON fields must not be repeated.")
                    result[key] = value
                return result

            value = json.loads(raw, object_pairs_hook=unique_fields) if reject_duplicate_fields else json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object.")
            return value

        def handle_file_request(self, query: str, current_app: WebChatApp) -> None:
            params = parse_qs(query)
            path_text = (params.get("path") or [""])[0]
            try:
                path = safe_public_output_file(path_text, current_app.out_dir)
            except ValueError as exc:
                self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if not path.exists() or not path.is_file():
                self.respond_json({"error": "file not found"}, status=HTTPStatus.NOT_FOUND)
                return
            content_type = "text/plain; charset=utf-8"
            if path.suffix == ".md":
                content_type = "text/markdown; charset=utf-8"
            elif path.suffix == ".json":
                content_type = "application/json; charset=utf-8"
            self.respond_text(
                path.read_text(encoding="utf-8", errors="replace"), content_type=content_type
            )

        def handle_asset_request(self, path_text: str) -> None:
            try:
                asset_path = safe_repo_file(path_text.removeprefix("/"))
            except ValueError as exc:
                self.respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if not asset_path.is_relative_to((REPO_ROOT / "assets").resolve()):
                self.respond_json(
                    {"error": "asset path must stay inside assets/"}, status=HTTPStatus.BAD_REQUEST
                )
                return
            canonical_route = '/assets/' + asset_path.relative_to((REPO_ROOT / 'assets').resolve()).as_posix()
            if not runtime_profile.permits(canonical_route):
                self.respond_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
                return
            if not asset_path.exists() or not asset_path.is_file():
                self.respond_json({"error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
                return
            content_type = "application/octet-stream"
            if asset_path.suffix.lower() == ".png":
                content_type = "image/png"
            elif asset_path.suffix.lower() in {".js", ".mjs"}:
                content_type = "text/javascript; charset=utf-8"
            elif asset_path.suffix.lower() == ".css":
                content_type = "text/css; charset=utf-8"
            elif asset_path.suffix.lower() == ".wasm":
                content_type = "application/wasm"
            body = asset_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_library_pdf(self, paper_id: str) -> None:
            load_project_env(REPO_ROOT / ".env")
            try:
                bib_dir = get_bib_dir()
            except OriginRAGConfigError:
                bib_dir = REPO_ROOT / "bib"
            headers_sent = False
            try:
                with open_registered_pdf(paper_id, metadata_files=[REPO_ROOT / "data/metadata/papers.tsv", REPO_ROOT / "data/metadata/paper_registry.tsv"], bib_roots=[bib_dir, REPO_ROOT / "bib"]) as handle:
                    size = os.fstat(handle.fileno()).st_size
                    requested_range = self.headers.get("Range")
                    try:
                        start, end = byte_range(requested_range, size)
                    except ValueError:
                        self.respond_json({"error": "Requested PDF range is unavailable."}, status=HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, extra_headers=[("Content-Range", f"bytes */{size}")])
                        return
                    self.send_response(HTTPStatus.PARTIAL_CONTENT if requested_range else HTTPStatus.OK)
                    self.send_security_headers()
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", 'inline; filename="paper.pdf"')
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(end - start + 1))
                    if requested_range:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    headers_sent = True
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (FileNotFoundError, ValueError, OSError, RuntimeError):
                if not headers_sent:
                    self.respond_json({"error": "No local PDF is available for this paper. It may have been moved or removed."}, status=HTTPStatus.NOT_FOUND)

        def respond_json(
            self,
            payload: dict,
            status: HTTPStatus = HTTPStatus.OK,
            *,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in extra_headers or []:
                self.send_header(name, value)
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def respond_text(
            self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_security_headers()
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def send_security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            if urlparse(self.path).path in WORKSPACE_PAGES | {"/reader", "/folio"} or urlparse(self.path).path.startswith("/assets/vendor/pdfjs/"):
                policy = f"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval' 'sha256-{APPEARANCE_SCRIPT_HASH}'; worker-src 'self'; font-src 'self' blob: data:; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            else:
                policy = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            self.send_header("Content-Security-Policy", policy)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")

        def log_message(self, format: str, *args: object) -> None:
            return

    OriginRAGWebHandler.turn_job_manager = turn_jobs
    OriginRAGWebHandler.paper_import_manager = paper_imports
    return OriginRAGWebHandler


def append_anonymous_review_event(
    out_dir: Path,
    *,
    conversation_id: str,
    question: str,
    response: dict,
    lock: threading.Lock,
) -> None:
    """Store an application-level anonymous turn for product evaluation.

    The review record deliberately excludes account IDs, names, email addresses,
    authentication tokens, user directories, and session paths. Free-text questions
    may still contain identifying information supplied by the user.
    """

    root = out_dir if out_dir.is_absolute() else REPO_ROOT / out_dir
    review_dir = root / "admin_review"
    event = {
        "schema_version": "anonymous_web_turn_v1",
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "conversation_id": conversation_id,
        "question": " ".join(question.split()),
        "answer": str(response.get("answer") or ""),
        "task": response.get("task"),
        "evidence_grade": response.get("evidence_grade"),
        "evidence_score": response.get("evidence_score"),
        "grounding_passed": response.get("grounding_passed"),
        "elapsed_seconds": response.get("elapsed_seconds"),
        "error": response.get("error"),
        "sources": [
            {
                "citation": source.get("citation"),
                "doi": source.get("doi"),
                "source_type": source.get("source_type"),
            }
            for source in response.get("sources") or []
        ],
    }
    with lock:
        review_dir.mkdir(parents=True, exist_ok=True)
        review_dir.chmod(0o700)
        review_path = review_dir / "anonymous_turns.jsonl"
        with review_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        review_path.chmod(0o600)


def merge_settings(base: WebChatSettings, update: dict) -> WebChatSettings:
    data = base.__dict__.copy()
    allowed = set(data)
    for key, value in update.items():
        if key not in allowed:
            continue
        if key in {
            "limit",
            "candidate_multiplier",
            "max_per_paper",
            "max_chars_per_chunk",
            "trace_token_budget",
        }:
            data[key] = int(value) if value not in ("", None) else None
        elif key in {"repair_attempts"}:
            data[key] = max(0, int(value or 0))
        elif key in {"min_score"}:
            data[key] = float(value or 0.0)
        elif key in {
            "template_fallback",
            "include_low_quality",
            "exclude_tables",
            "exclude_figures",
            "exclude_supplement",
            "allow_missing_doi_sources",
        }:
            data[key] = parse_web_boolean(value, key)
        else:
            data[key] = value if value != "" else None
    if data.get("evidence_selector") not in WEB_EVIDENCE_SELECTORS:
        raise ValueError(f"Unsupported evidence selector: {data.get('evidence_selector')}")
    return WebChatSettings(**data)


def authenticated_turn_settings(update: object) -> dict:
    """Return the narrow user-controlled settings subset for an authenticated turn."""

    if update is None:
        return {}
    if not isinstance(update, dict):
        raise ValueError("settings must be an object")
    return {
        key: value
        for key, value in update.items()
        if key in AUTHENTICATED_TURN_SETTING_FIELDS
    }


def parse_web_boolean(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    raise ValueError(f"{field} must be a boolean")


def build_ask_args(
    settings: WebChatSettings,
    question: str,
    task: str,
    retrieval_question: str | None = None,
) -> SimpleNamespace:
    evidence_selector = "current" if task == "sources" else settings.evidence_selector
    paper_search_mode = classify_paper_search_mode(question) if task == "sources" else None
    needs_llm_source_planner = (
        paper_search_mode == "topical_discovery"
        and not has_sufficient_curated_source_expansions(question)
    )
    planner_backend = (
        settings.planner_backend
        if task in {"gaps", "hypothesis"} or needs_llm_source_planner
        else "rule_based"
    )
    answer_backend = "none" if task == "sources" else settings.backend
    return SimpleNamespace(
        question=question,
        answer_question=question,
        retrieval_question=retrieval_question or question,
        chunks_dir=settings.chunks_dir,
        papers=settings.papers,
        limit=settings.limit,
        candidate_multiplier=settings.candidate_multiplier,
        max_per_paper=settings.max_per_paper,
        max_chars_per_chunk=settings.max_chars_per_chunk,
        preset=settings.preset,
        backend=answer_backend,
        configured_answer_backend=settings.backend,
        understand_question=True,
        auto_chat_routing=settings.task == "auto",
        allow_reading_excerpt=True,
        require_searchable_fulltext=True,
        planner_backend=planner_backend,
        configured_planner_backend=settings.planner_backend,
        planner_model=settings.planner_model,
        answer_model=settings.answer_model,
        task=task,
        out_dir="",
        category=settings.category,
        include_low_quality=settings.include_low_quality,
        show_context=False,
        query_mode=settings.query_mode,
        min_score=settings.min_score,
        exclude_tables=settings.exclude_tables,
        exclude_figures=settings.exclude_figures,
        exclude_supplement=settings.exclude_supplement,
        allow_missing_doi_sources=settings.allow_missing_doi_sources,
        retriever=settings.retriever,
        retrieval_index=settings.retrieval_index,
        repair_attempts=settings.repair_attempts,
        no_template_fallback=not settings.template_fallback,
        profile_instruction=settings.profile_instruction or "",
        task_mode=CHAT_TASK_MODES.get(task, "evidence_qa"),
        paper_search_mode=paper_search_mode,
        evidence_selector=evidence_selector,
        trace_config="config/trace_rag.yaml",
        trace_blueprint=None,
        trace_lineage_graph=settings.trace_lineage_graph,
        trace_token_budget=settings.trace_token_budget,
        trace_selector_algorithm="lazy_greedy",
        show_trace_certificate=False,
        allow_provisional_synthesis=(task != "sources"),
    )


def serialize_turn_result(
    result: dict, turn_dir: Path, retrieval_question: str, resolved_task: str
) -> dict:
    if resolved_task == "paper_summaries":
        parts = [serialize_turn_result(part, part["answer_path"].parent, retrieval_question, "paper_summary")
                 for part in result["parts"]]
        return {"task": resolved_task, "answer": result["answer"], "answer_state": "answered",
                "sources": [s for part in parts for s in part["sources"]], "parts": parts,
                "output_dir": relative_path(turn_dir), "answer_path": relative_path(result["answer_path"])}
    if resolved_task == "library_status" or result.get("quick_list"):
        return {"task": resolved_task, "answer": result["answer"], "answer_state": "answered",
                "answer_path": relative_path(result["answer_path"]), "output_dir": relative_path(turn_dir),
                "retrieval_question": retrieval_question, "sources": result.get("sources", []),
                "quick_list": bool(result.get("quick_list")), "evidence_grade": "not_applicable"}
    if result.get("reading_card") is not None:
        card = result["reading_card"]
        sources = format_public_sources(result["pack"]["chunks"], source_role="summary_source")
        for source in sources:
            source["cited"] = True
        passages = {p["source"]: p for p in card["inputs"]["passages"]}
        return {"task": "paper_summary", "answer": ask_corpus.card_markdown(card), "answer_state": "answered",
                "output_dir": relative_path(turn_dir), "answer_path": relative_path(result["answer_path"]),
                "sources": sources, "n_unique_papers": 1, "n_chunks": len(passages),
                "retrieval_question": retrieval_question, "evidence_grade": "not_applicable",
                "reading_card": {"coverage": card["inputs"]["coverage"], "cache_hit": card["cache_hit"],
                                 "excerpt_only": card.get("verification") == "source_excerpt_only",
                                 "omitted_claims": card["omitted_claims"],
                                 "passages": [{"section": passages[c["source"]]["section"],
                                                "page_start": passages[c["source"]]["page_start"],
                                                "page_end": passages[c["source"]]["page_end"],
                                                "quote": c["quote"]} for c in card["claims"]]}}
    answer_path = result.get("answer_path")
    answer = ""
    raw_answer = ""
    pack = result.get("pack") or {}
    trace_result = result.get("trace_result") or {}
    assurance = build_public_assurance(
        pack,
        trace_selection=(
            trace_result.get("selection") if isinstance(trace_result, dict) else None
        ),
    )
    paper_search = result.get("paper_search") or pack.get("paper_search") or {}
    paper_search_results = list(result.get("paper_search_results") or [])
    source_records = merge_paper_search_records(
        paper_search_results,
        list(pack.get("chunks", [])),
    )
    sources = format_public_sources(
        source_records,
        query=(
            retrieval_question if resolved_task == "sources" and not paper_search_results else None
        ),
        source_role=("search_result" if resolved_task == "sources" else "answer_evidence"),
    )
    if resolved_task == "sources":
        sources = attach_automatic_paper_search_traces(sources)
    if resolved_task != "sources" and answer_path and Path(answer_path).exists():
        raw_answer = Path(answer_path).read_text(encoding="utf-8")
        answer = public_answer_text(
            raw_answer,
            chunks=list(pack.get("chunks", [])),
        )




        cited_ids = set(re.findall(r"\[\[cite:(E\d{3})\]\]", answer))
        cited_paper_ids = {
            str(chunk.get("paper_id") or "")
            for chunk in pack.get("chunks", [])
            if str(chunk.get("evidence_id") or "") in cited_ids
            and str(chunk.get("paper_id") or "")
        }
        assurance["cited_report_count"] = len(cited_paper_ids)
        cited_family_links = [
            link
            for link in assurance.get("publication_family_links", [])
            if link.get("paper_id_a") in cited_paper_ids
            and link.get("paper_id_b") in cited_paper_ids
        ]
        cited_shared_origin_signals = [
            link
            for link in assurance.get("shared_origin_signals", [])
            if link.get("paper_id_a") in cited_paper_ids
            and link.get("paper_id_b") in cited_paper_ids
        ]
        cited_review_only_mentions = [
            link
            for link in assurance.get("review_only_origin_mentions", [])
            if link.get("paper_id_a") in cited_paper_ids
            and link.get("paper_id_b") in cited_paper_ids
        ]
        assurance["publication_family_links"] = cited_family_links
        assurance["shared_origin_signals"] = cited_shared_origin_signals
        assurance["review_only_origin_mentions"] = cited_review_only_mentions
        assurance["detected_publication_family_links"] = len(cited_family_links)
        assurance["detected_shared_origin_signals"] = len(cited_shared_origin_signals)
        assurance["review_only_origin_mention_count"] = len(cited_review_only_mentions)
        cited_publication_family_upper_bound = ask_corpus.publication_family_upper_bound(
            len(cited_paper_ids), cited_family_links
        )
        assurance["cited_publication_family_upper_bound"] = (
            cited_publication_family_upper_bound
        )


        assurance["publication_family_upper_bound"] = (
            cited_publication_family_upper_bound
        )
        assurance["publication_family_count_scope"] = "cited_reports"
        for source in sources:
            source["cited"] = bool(cited_ids.intersection(source.get("evidence_ids") or []))
        sources.sort(key=lambda source: (not bool(source.get("cited")),))
    attempts = result.get("answer_attempts") or []
    qc = result.get("answer_qc") or {}
    evidence_grade = result.get("evidence_grade") or pack.get("grade") or {}
    grounding_report = result.get("grounding_report") or {}
    if resolved_task == "sources":
        answer_state = "results" if sources else "empty"
        answer = build_public_paper_search_summary(sources, paper_search)
        if pack.get("corpus_inventory") is not None:
            from originrag.reply_language import reply_language
            answer = inventory_markdown(
                pack["corpus_inventory"],
                language=reply_language(pack.get("question", retrieval_question)),
                sources=sources,
            )
    else:
        answer_state = (
            "provisional"
            if answer and result.get("partial_answer_path")
            else "answered" if answer else "withheld"
        )
    if resolved_task != "sources" and not answer:
        llm_result = result.get("llm_result")
        answer = build_public_abstention(
            assurance=assurance,
            evidence_grade=evidence_grade,
            grounding_rejected=bool(result.get("rejected_answer_path")),
            quality_warnings=qc.get("warnings") or [],
            backend_error=str(getattr(llm_result, "error", "") or ""),
            pack=pack,
        )
    claim_source_chains = (
        build_automatic_claim_source_chains(raw_answer, pack)
        if resolved_task != "sources" and raw_answer
        else []
    )
    return {
        "answer": answer,
        "answer_state": answer_state,
        "qc": qc,
        "attempts": attempts,
        "task": resolved_task,
        "retrieval_question": retrieval_question,
        "output_dir": relative_path(turn_dir),
        "answer_path": relative_path(answer_path) if answer_path else None,
        "evidence_table_tsv_path": relative_path(result.get("evidence_table_tsv_path")),
        "evidence_table_md_path": relative_path(result.get("evidence_table_md_path")),
        "evidence_pack_path": relative_path(result.get("pack_path")),
        "paper_search_results_path": relative_path(result.get("paper_search_results_path")),
        "evidence_grade": (
            "not_applicable"
            if resolved_task == "sources"
            else evidence_grade.get("grade", "unknown")
        ),
        "evidence_score": (
            None if resolved_task == "sources" else evidence_grade.get("score")
        ),
        "n_chunks": pack.get("n_chunks", 0),
        "n_unique_papers": (
            len(sources) if resolved_task == "sources" else pack.get("n_unique_papers", 0)
        ),
        "grounding_passed": grounding_report.get("passed") if grounding_report else None,
        "assurance": assurance,
        "paper_search": paper_search,
        "sources": sources,
        "claim_source_chains": claim_source_chains,
    }


def build_automatic_claim_source_chains(
    answer_text: str,
    evidence_pack: dict,
    *,
    trace_builder=None,
    library_resolver: LibraryStatusResolver | None = None,
    max_claims: int = AUTO_TRACE_MAX_CLAIMS,
    max_carriers: int = AUTO_TRACE_MAX_CARRIERS,
) -> list[dict]:
    """Trace cited central answer claims without promoting candidates to primary truth."""

    chunks = {
        str(chunk.get("evidence_id") or ""): chunk
        for chunk in evidence_pack.get("chunks", [])
        if chunk.get("evidence_id")
    }
    if not answer_text.strip() or not chunks:
        return []
    trace_builder = trace_builder or build_public_source_trace
    resolver = library_resolver or default_library_resolver()
    lineage_edges = list(evidence_pack.get("trace_lineage_edges") or [])
    chains = []
    carrier_attempts = 0
    claim_blocks = [
        sentence
        for block in citation_claim_blocks(direct_evidence_text(answer_text))
        for sentence in split_cited_claim_sentences(block)
    ]
    for block in claim_blocks:
        evidence_ids = [
            evidence_id
            for evidence_id in sorted(set(re.findall(r"\bE\d{3}\b", block)))
            if evidence_id in chunks
        ]
        if not evidence_ids:
            continue
        claim_text = clean_public_claim_text(block)
        carriers = []
        seen_papers = set()
        for evidence_id in evidence_ids:
            chunk = chunks[evidence_id]
            paper_id = str(chunk.get("paper_id") or "")
            if not paper_id or paper_id in seen_papers or carrier_attempts >= max_carriers:
                continue
            seen_papers.add(paper_id)
            carrier_attempts += 1
            carrier_status = resolver.resolve(chunk)
            carrier_source_type = source_type_for_record(chunk)
            carrier = {
                "evidence_ids": [
                    item
                    for item in evidence_ids
                    if str(chunks[item].get("paper_id") or "") == paper_id
                ],
                "paper_id": paper_id,
                "citation": chunk.get("citation_short") or chunk.get("citation_label") or paper_id,
                "title": chunk.get("title") or "Untitled",
                "doi": chunk.get("doi") or "",
                "library_status": compact_library_status(carrier_status),
                "source_type": carrier_source_type,
                "role": "claim_carrier",
                "direct_report_status": automatic_direct_report_status(carrier_source_type),
                "lineage_relations": carrier_lineage_relations(paper_id, lineage_edges),
                "trace_status": "not_run",
                "references": [],
            }
            if carrier["direct_report_status"] == "likely_direct_primary_automatic":



                carrier["trace_status"] = "direct_primary_stop"
            elif (carrier_status.get("local_pdf") or {}).get("exists"):
                try:
                    trace = trace_builder(paper_id, claim_text or str(chunk.get("text") or ""))
                    references = list(trace.get("references") or [])
                    for reference in references:
                        reference["source_type"] = source_type_for_record(reference)
                        reference["direct_report_status"] = direct_report_candidate_status(reference)
                        reference["origin_status"] = "unresolved_review_required"
                    carrier["trace_status"] = trace.get("status") or "unresolved"
                    carrier["references"] = references
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    carrier["trace_status"] = "trace_unavailable"
                    carrier["trace_error"] = str(exc)
            else:
                carrier["trace_status"] = "carrier_pdf_missing"
            carriers.append(carrier)
        carriers_requiring_trace = [
            carrier
            for carrier in carriers
            if carrier.get("trace_status") != "direct_primary_stop"
        ]
        if carriers_requiring_trace:
            chains.append(
                {
                    "claim_text": claim_text,
                    "evidence_ids": evidence_ids,
                    "carriers": carriers_requiring_trace,
                    "origin_status": "unresolved_review_required",
                    "policy": (
                        "Carrier identity, citation identity, direct reporting, and scientific "
                        "origin are separate stages. Candidates are never promoted automatically."
                    ),
                }
            )
        if len(chains) >= max_claims or carrier_attempts >= max_carriers:
            break
    return chains


def split_cited_claim_sentences(block: str) -> list[str]:
    """Split prose into citation-bearing claims for compact source tracing."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", " ".join((block or "").split()))
    return [sentence for sentence in sentences if re.search(r"\bE\d{3}\b", sentence)]


def clean_public_claim_text(block: str) -> str:
    text = re.sub(r"\bE\d{3}\b[;,]?", "", block or "")
    text = re.sub(r"\(\s*[;,\s]*\)", "", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return " ".join(text.replace("**", "").split()).strip(" -")


def direct_report_candidate_status(reference: dict) -> str:
    resolution = str(reference.get("bibliographic_resolution_status") or "unresolved")
    if resolution not in {"exact_id", "exact_title"}:
        return "identity_unresolved"
    if str(reference.get("review_status") or "") != "reviewed":
        return automatic_direct_report_status(reference.get("source_type") or {})
    return "reviewed_direct_source" if reference.get("support_relation") == "directly_reports" else "reviewed_not_direct"


def automatic_direct_report_status(source_type: dict) -> str:
    """Return an automatic, non-adjudicated claim-support status."""

    kind = str(source_type.get("source_type") or "uncertain")
    if kind == "primary_research":
        return "likely_direct_primary_automatic"
    if kind in {
        "systematic_review_meta_analysis",
        "narrative_review",
        "editorial_commentary",
        "protocol",
    }:
        return "secondary_or_nonreporting_automatic"
    if kind == "reanalysis_secondary_data":
        return "reanalysis_not_origin_automatic"
    return "source_type_uncertain"


@lru_cache(maxsize=1)
def paper_metadata_by_id() -> dict[str, dict[str, str]]:
    path = REPO_ROOT / "data/metadata/papers.tsv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            str(row.get("paper_id") or ""): row
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("paper_id")
        }


@lru_cache(maxsize=2048)
def paper_design_context(paper_id: str) -> str:
    """Load enough local text to identify design without scanning on every turn."""

    path = REPO_ROOT / "data/chunks" / f"{paper_id}.jsonl"
    if not path.exists():
        return ""
    parts: list[str] = []
    size = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                text = str(row.get("text") or "")
                parts.append(text)
                size += len(text)
                if size >= 24_000:
                    break
    except (OSError, ValueError):
        return ""
    return "\n".join(parts)


def source_type_for_record(record: dict) -> dict:
    supplied_assessment = record.get("source_type_assessment")
    if isinstance(supplied_assessment, dict) and supplied_assessment.get("source_type"):
        return dict(supplied_assessment)
    paper_id = str(record.get("paper_id") or "")
    metadata = paper_metadata_by_id().get(paper_id, {})
    merged = {**metadata, **{key: value for key, value in record.items() if value not in (None, "")}}
    context = paper_design_context(paper_id) if paper_id else ""
    supplied_text = str(record.get("text") or record.get("context_text") or "")
    return classify_source_document(merged, "\n".join(part for part in (context, supplied_text) if part))


def carrier_lineage_relations(paper_id: str, edges: list[dict]) -> list[dict]:
    relations = []
    for edge in edges:
        left, right = str(edge.get("paper_id_a") or ""), str(edge.get("paper_id_b") or "")
        if paper_id not in {left, right}:
            continue
        relations.append(
            {
                "other_paper_id": right if left == paper_id else left,
                "relationship_types": list(edge.get("relationship_types") or []),
                "origin_relation": edge.get("origin_relation") or "unresolved",
                "review_status": edge.get("origin_review_status") or "unreviewed",
            }
        )
    return relations


def build_public_paper_search_summary(
    sources: list[dict],
    paper_search: dict,
) -> str:
    """Give a concise user-facing introduction to deterministic paper results."""
    if not sources:
        return (
            "## Paper search\n\n"
            "No matching papers were found in the current local corpus. Try a broader "
            "topic description or verify an identifier."
        )
    count = len(sources)
    report_count = public_paper_search_report_count(sources)
    noun = "paper" if count == 1 else "papers"
    count_text = f"{count} relevant {noun}"
    if report_count > count:
        count_text = (
            f"{report_count} relevant reports, displayed as {count} "
            "publication-family representatives"
        )
    expansion = (
        "Related terminology was included in the search. "
        if paper_search.get("planner_assisted") or paper_search.get("planner_attempted")
        else ""
    )
    return (
        f"## Best-matching papers\n\nI found {count_text} in the local corpus. "
        f"{expansion}The table below shows why each paper was selected and whether its "
        "full text is available locally."
    )


def merge_paper_search_records(
    ranked_papers: list[dict],
    chunks: list[dict],
) -> list[dict]:
    """Preserve paper ranking while attaching local text needed for source tracing."""
    chunks_by_paper = {
        str(chunk.get("paper_id") or ""): chunk for chunk in chunks if chunk.get("paper_id")
    }
    merged = []
    for paper in ranked_papers:
        paper_id = str(paper.get("paper_id") or "")
        chunk = chunks_by_paper.get(paper_id, {})
        merged.append({**chunk, **paper})
    return merged or chunks


def build_public_assurance(pack: dict, trace_selection: object | None = None) -> dict:
    """Return a compact, conservative assurance summary for the public UI."""
    certificate = pack.get("trace_sufficiency_certificate") or {}
    robust = pack.get("trace_robust_lineage") or {}
    paper_search_sources = list(pack.get("paper_search_sources") or [])
    if str(pack.get("task") or "") == "sources" and paper_search_sources:
        family_edges = public_publication_family_links(pack)
        shared_origin_signals = public_shared_origin_signals(pack)
        review_only_origin_mentions = public_review_only_origin_mentions(pack)
        raw_paper_count = public_paper_search_report_count(paper_search_sources)
        return {
            "mode": "lineage_screen",
            "label": "Study-origin screen",
            "audit_run": False,
            "raw_paper_count": raw_paper_count,
            "detected_publication_family_links": len(family_edges),
            "detected_shared_origin_signals": len(shared_origin_signals),
            "review_only_origin_mention_count": len(review_only_origin_mentions),
            "publication_family_links": family_edges,
            "shared_origin_signals": shared_origin_signals,
            "review_only_origin_mentions": review_only_origin_mentions,
            "publication_family_upper_bound": ask_corpus.publication_family_upper_bound(
                raw_paper_count, family_edges
            ),
        }
    if not certificate or not robust:
        family_edges = public_publication_family_links(pack)
        shared_origin_signals = public_shared_origin_signals(pack)
        review_only_origin_mentions = public_review_only_origin_mentions(pack)
        if paper_search_sources:
            raw_paper_count = public_paper_search_report_count(paper_search_sources)
            return {
                "mode": "lineage_screen",
                "label": "Study-origin screen",
                "audit_run": False,
                "raw_paper_count": raw_paper_count,
                "detected_publication_family_links": len(family_edges),
                "detected_shared_origin_signals": len(shared_origin_signals),
                "review_only_origin_mention_count": len(review_only_origin_mentions),
                "publication_family_links": family_edges,
                "shared_origin_signals": shared_origin_signals,
                "review_only_origin_mentions": review_only_origin_mentions,
                "publication_family_upper_bound": ask_corpus.publication_family_upper_bound(
                    raw_paper_count, family_edges
                ),
            }
        return {
            "mode": "standard",
            "label": "Standard evidence view",
            "audit_run": False,
        }

    effective = robust.get("effective_evidence") or {}
    pessimistic_flow = robust.get("pessimistic_flow") or {}
    reasons = [str(item) for item in certificate.get("reasons") or []]
    graph_status = str(certificate.get("lineage_graph_status") or "not_assessed")
    provisional = (
        graph_status not in {"reviewed", "not_applicable"}
        or "blueprint_not_human_reviewed" in reasons
    )
    family_edges = public_publication_family_links(pack)
    shared_origin_signals = public_shared_origin_signals(pack)
    review_only_origin_mentions = public_review_only_origin_mentions(pack)
    raw_paper_count = int(certificate.get("raw_paper_count") or pack.get("n_unique_papers") or 0)
    selection_run_config = _public_selection_value(trace_selection, "run_config", {})
    if not isinstance(selection_run_config, dict):
        selection_run_config = {}
    selected_report_count = int(
        _public_selection_value(trace_selection, "raw_paper_count", raw_paper_count)
        or raw_paper_count
    )
    requested_minimum_report_count = int(certificate.get("minimum_required_papers") or 0)
    selection_stopping_reason = str(
        selection_run_config.get("stopping_reason")
        or certificate.get("selection_stopping_reason")
        or ""
    )
    selection_token_budget = selection_run_config.get("token_budget")
    selection_token_cost = _public_selection_value(trace_selection, "token_cost", None)
    retrieval_substantive_count = certificate.get("substantive_paper_count")
    certifiable_substantive_count = effective.get("substantive_paper_count")
    selected_publication_family_upper_bound = ask_corpus.publication_family_upper_bound(
        raw_paper_count, family_edges
    )
    return {
        "mode": "lineage_aware",
        "label": "Automatic study-origin check",
        "audit_run": True,
        "provisional": provisional,
        "provisional_synthesis": bool(pack.get("provisional_synthesis_authorized")),
        "status": robust.get("status"),
        "evidence_sufficiency": certificate.get("evidence_sufficiency"),
        "allowed_claim_strength": certificate.get("allowed_claim_strength"),
        "facet_coverage": certificate.get("facet_coverage"),
        "robust_facet_coverage_lower": pessimistic_flow.get("normalized_coverage"),
        "robust_bottleneck_coverage_lower": pessimistic_flow.get("bottleneck_coverage"),
        "covered_facets": certificate.get("required_facets_covered") or [],
        "unresolved_facets": certificate.get("unresolved_facets") or [],
        "robust_unresolved_facets": pessimistic_flow.get("unresolved_facets") or [],
        "raw_paper_count": raw_paper_count,
        "detected_publication_family_links": len(family_edges),
        "detected_shared_origin_signals": len(shared_origin_signals),
        "review_only_origin_mention_count": len(review_only_origin_mentions),
        "publication_family_links": family_edges,
        "shared_origin_signals": shared_origin_signals,
        "review_only_origin_mentions": review_only_origin_mentions,
        "publication_family_upper_bound": selected_publication_family_upper_bound,
        "selected_publication_family_upper_bound": (
            selected_publication_family_upper_bound
        ),
        "publication_family_count_scope": "selected_reports",


        "substantive_paper_count": certifiable_substantive_count,
        "retrieval_substantive_report_count": retrieval_substantive_count,
        "certifiable_substantive_report_count": certifiable_substantive_count,
        "lineage_count_scope": "certifiable_substantive_reports",
        "selected_report_count": selected_report_count,
        "requested_minimum_report_count": requested_minimum_report_count,
        "selected_report_floor_met": (
            selected_report_count >= requested_minimum_report_count
            if requested_minimum_report_count
            else True
        ),
        "selection_stopping_reason": selection_stopping_reason or None,
        "selection_token_budget": selection_token_budget,
        "selection_token_cost": selection_token_cost,
        "lineage_count_lower": robust.get("lineage_count_lower"),
        "lineage_count_upper": robust.get("lineage_count_upper"),
        "required_lineages": robust.get("required_lineages"),
        "effective_evidence_lower": effective.get("lower"),
        "effective_evidence_upper": effective.get("upper"),
        "required_effective_evidence": robust.get("required_effective_evidence"),
        "lineage_graph_status": graph_status,
        "uncertain_pair_count": robust.get("uncertain_pair_count"),
        "missing_pair_count": robust.get("missing_pair_count"),
        "partition_bound_method": robust.get("partition_bound_method"),
        "exact_for_declared_uncertainty": robust.get("exact_for_declared_lineage_uncertainty"),
        "review_priority_count": len(pack.get("trace_lineage_review_priorities") or []),
        "reasons": reasons,
    }


def _public_selection_value(selection: object | None, key: str, default: object) -> object:
    if isinstance(selection, dict):
        return selection.get(key, default)
    return getattr(selection, key, default) if selection is not None else default


def public_publication_family_links(pack: dict) -> list[dict]:
    """Combine supplied graph edges and safe automatic version signals for display."""
    links = []
    for edge in ask_corpus.trace_publication_family_edges(pack):
        links.append(public_lineage_link(edge, pack, automatic=False))
    accepted_signals = {
        "same_doi_family",
        "same_preprint_family",
        "same_publication_version",
    }
    for signal in pack.get("automatic_lineage_signals", []):
        if str(signal.get("signal_type") or "") not in accepted_signals:
            continue
        links.append(
            public_lineage_link(
                {
                    "paper_id_a": signal.get("paper_id_a"),
                    "paper_id_b": signal.get("paper_id_b"),
                    "relationship_types": [signal.get("signal_type")],
                    "details": signal.get("details") or {},
                },
                pack,
                automatic=True,
            )
        )
    return dedupe_public_lineage_links(links)


def public_paper_search_report_count(sources: list[dict]) -> int:
    """Count returned reports while one row may represent a version family."""

    report_ids: set[str] = set()
    anonymous_reports = 0
    for source in sources:
        reports = source.get("publication_family_reports") or [source]
        for report in reports:
            paper_id = str(report.get("paper_id") or "").strip()
            if paper_id:
                report_ids.add(paper_id)
            else:
                anonymous_reports += 1
    return len(report_ids) + anonymous_reports


def public_shared_origin_signals(pack: dict) -> list[dict]:
    accepted_signals = {
        "same_dataset_accession",
        "same_trial_id",
        "same_cohort",
        "shared_cited_data_origin",
    }
    return dedupe_public_lineage_links(
        [
            public_lineage_link(
                {
                    "paper_id_a": signal.get("paper_id_a"),
                    "paper_id_b": signal.get("paper_id_b"),
                    "relationship_types": [signal.get("signal_type")],
                    "details": signal.get("details") or {},
                },
                pack,
                automatic=True,
            )
            for signal in pack.get("automatic_lineage_signals", [])
            if str(signal.get("signal_type") or "") in accepted_signals
        ]
    )


def public_review_only_origin_mentions(pack: dict) -> list[dict]:
    """Expose shared text mentions separately without presenting them as origin links."""
    review_only_signals = {
        "shared_cohort_mention",
        "shared_identifier_mention",
        "document_explicit_reuse_mention",
    }
    return dedupe_public_lineage_links(
        [
            public_lineage_link(
                {
                    "paper_id_a": signal.get("paper_id_a"),
                    "paper_id_b": signal.get("paper_id_b"),
                    "relationship_types": [signal.get("signal_type")],
                    "details": signal.get("details") or {},
                },
                pack,
                automatic=True,
            )
            for signal in pack.get("automatic_lineage_signals", [])
            if str(signal.get("signal_type") or "") in review_only_signals
        ]
    )


def public_lineage_link(edge: dict, pack: dict, *, automatic: bool) -> dict:
    paper_records = [
        *list(pack.get("chunks", [])),
        *list(pack.get("paper_search_sources", [])),
    ]
    paper_records.extend(
        report
        for source in pack.get("paper_search_sources", [])
        for report in source.get("publication_family_reports") or []
    )
    labels = {
        str(chunk.get("paper_id") or ""): public_citation_short(chunk)
        for chunk in paper_records
    }
    left = str(edge.get("paper_id_a") or "")
    right = str(edge.get("paper_id_b") or "")
    return {
        "paper_id_a": left,
        "paper_id_b": right,
        "label_a": labels.get(left, left),
        "label_b": labels.get(right, right),
        "relationship_types": sorted(set(edge.get("relationship_types") or [])),
        "automatic": automatic,
        "details": edge.get("details") or {},
    }


def dedupe_public_lineage_links(links: list[dict]) -> list[dict]:
    deduped = {}
    for link in links:
        pair = tuple(sorted([str(link.get("paper_id_a") or ""), str(link.get("paper_id_b") or "")]))
        if not all(pair):
            continue
        key = (pair, tuple(link.get("relationship_types") or []))
        current = deduped.get(key)
        if current is None or (current.get("automatic") and not link.get("automatic")):
            deduped[key] = link
    return list(deduped.values())


def build_public_abstention(
    *,
    assurance: dict,
    evidence_grade: dict,
    grounding_rejected: bool,
    quality_warnings: list[str] | None = None,
    backend_error: str = "",
    pack: dict | None = None,
) -> str:
    """Explain a fail-closed result instead of returning an empty chat bubble."""
    if grounding_rejected:
        warnings = set(quality_warnings or [])
        format_warnings = {
            "missing_required_sections", "empty_required_sections", "unbalanced_delimiters",
            "incomplete_prose_section", "incomplete_key_sources",
        }
        if warnings and warnings <= format_warnings:
            reason = (
                "The draft contained incomplete or malformed text and could not be "
                "presented reliably. This does not mean that matching papers are missing. "
            )
        elif warnings:
            reason = "A draft answer was generated but failed answer validation. "
        else:
            reason = "A draft answer was generated but failed the citation check. "
        return (
            "## Answer withheld\n\n"
            + reason
            + "The retrieved evidence remains available below, but OriginRAG did not "
            "present the draft as a supported scientific answer."
        )
    if assurance.get("audit_run"):
        status = assurance.get("status")
        if status == "lineage_review_required":
            return (
                "## Study overlap needs review\n\n"
                "The retrieved papers may support an answer, but some may come from the "
                "same study, cohort, dataset, or publication family. Independent studies "
                "remain unknown until these links have been reviewed."
            )
        if status == "insufficient_even_optimistically":
            missing_facets = public_missing_facet_labels(pack or {})
            if not (pack or {}).get("chunks") and missing_facets:
                return (
                    "## No directly matching evidence retrieved\n\n"
                    "The current local corpus returned no prompt-visible passage that "
                    "covered all required parts of the question: "
                    f"{', '.join(missing_facets)}. No literature synthesis was generated.\n\n"
                    "This is a corpus-conditional retrieval result, not evidence that no "
                    "relevant studies exist."
                )
            return (
                "## More evidence needed\n\n"
                "Even if every unresolved paper were counted separately, the retrieved "
                "evidence would not cover all required parts of the question. OriginRAG "
                "therefore withheld a synthesized answer."
            )
        if backend_error:
            return (
                "## Answer backend unavailable\n\n"
                "The evidence audit completed, but the local answer model did not return "
                "a usable response. The evidence details remain available for inspection."
            )
        return (
            "## Evidence check incomplete\n\n"
            "OriginRAG could not complete the evidence check. Open the evidence details "
            "and study-overlap view to see what still needs review."
        )
    if backend_error and "below threshold" not in backend_error:
        return (
            "## Answer backend unavailable\n\n"
            "Evidence retrieval completed, but the local answer model did not return a "
            "usable response. Check the local model service and try again."
        )
    return (
        "## More evidence needed\n\n"
        "The local corpus did not return enough directly relevant support for a "
        "source-grounded answer. Try a narrower question or add the missing papers "
        "to the local corpus."
    )


def public_missing_facet_labels(pack: dict) -> list[str]:
    """Convert internal facet IDs into compact user-facing labels."""

    certificate = pack.get("trace_sufficiency_certificate") or {}
    labels = []
    for value in certificate.get("unresolved_facets") or []:
        label = re.sub(r"^[0-9a-f]{12}_", "", str(value), flags=re.IGNORECASE)
        label = label.replace("_", " ").strip()
        if label:
            labels.append(label.replace("tcr", "TCR").replace("t cell", "T-cell"))
    return list(dict.fromkeys(labels))


def build_turn_context(user_text: str, retrieval_question: str, result: dict) -> ChatTurnContext:
    pack = result.get("pack") or {}
    chunks = list(pack.get("chunks", []))
    answer_summary = ""
    answer_path = result.get("answer_path")
    if answer_path and Path(answer_path).exists():
        answer_summary = summarize_answer_for_context(
            Path(answer_path).read_text(encoding="utf-8", errors="replace")
        )
    return ChatTurnContext(
        user_question=user_text,
        retrieval_question=retrieval_question,
        answer_summary=answer_summary,
        source_labels=tuple(
            str(chunk.get("citation_label") or "").strip()
            for chunk in chunks[:8]
            if str(chunk.get("citation_label") or "").strip()
        ),
        source_titles=tuple(unique_chunk_titles(chunks, limit=6)),
    )


def summarize_answer_for_context(answer: str, max_chars: int = 500) -> str:
    public = public_answer_text(answer)
    public = remove_markdown_section(public, "## References")
    lines = [
        line.strip("#-: ")
        for line in public.splitlines()
        if line.strip() and not line.strip().startswith("E")
    ]
    summary = " ".join(lines)
    summary = re.sub(r"\([^)]*\bE\d{3}\b[^)]*\)", "", summary)
    return " ".join(summary.split())[:max_chars].strip()


def unique_chunk_titles(chunks: list[dict], limit: int) -> list[str]:
    titles = []
    seen = set()
    for chunk in chunks:
        title = " ".join(str(chunk.get("title") or "").split())
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def turn_context_from_saved_turn(turn: dict) -> ChatTurnContext:
    output_text = str(turn.get("output") or "")
    answer_summary = ""
    titles: list[str] = []
    if output_text:
        try:
            output_dir = safe_repo_file(output_text)
        except ValueError:
            output_dir = None
        if output_dir:
            answer_path = output_dir / "answer.md"
            if answer_path.exists() and answer_path.is_file():
                answer_summary = summarize_answer_for_context(
                    answer_path.read_text(encoding="utf-8", errors="replace")
                )
            evidence_table = output_dir / "evidence_table.tsv"
            if evidence_table.exists() and evidence_table.is_file():
                titles = titles_from_evidence_table(evidence_table)
            elif turn.get("task") in {"sources", "paper_summaries"}:
                titles = [str(p["title"]) for p in saved_turn_result(turn).get("sources", [])[:6] if p.get("title")]
    return ChatTurnContext(
        user_question=str(turn.get("user") or ""),
        retrieval_question=str(turn.get("retrieval_question") or ""),
        answer_summary=answer_summary,
        source_labels=tuple(turn.get("sources") or []),
        source_titles=tuple(titles),
        resolved_task=str(turn.get("task") or ""),
    )


def titles_from_evidence_table(path: Path, limit: int = 6) -> list[str]:
    titles = []
    seen = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                title = " ".join(str(row.get("title") or "").split())
                key = title.lower()
                if not title or key in seen:
                    continue
                seen.add(key)
                titles.append(title)
                if len(titles) >= limit:
                    break
    except OSError:
        return []
    return titles


def public_answer_text(answer: str, chunks: list[dict] | None = None) -> str:
    text = strip_chunk_mentions(answer)
    text = remove_internal_evidence_lines(text)
    text = remove_markdown_section(text, "## Evidence Map")
    text = remove_markdown_section(text, "## Key Sources")
    text = remove_markdown_section(text, "## Key sources")
    text = remove_markdown_section(text, "## References")
    text = remove_markdown_section(text, "### key_references")
    text = remove_markdown_section(text, "## Caveat")
    for heading in (
        "## Evidence-backed statements",
        "## Cautious inference",
        "## Limitations / uncertainty",
        "## Missing evidence",
    ):
        text = remove_public_heading(text, heading)
    text = replace_internal_citations(text, chunks or [])
    return text.strip()


def replace_internal_citations(answer: str, chunks: list[dict]) -> str:
    """Replace audit-only IDs with private tokens rendered as linked citations."""
    chunk_by_id = {
        str(chunk.get("evidence_id")): chunk
        for chunk in chunks
        if chunk.get("evidence_id")
    }
    citation_by_id = {
        evidence_id: public_citation_short(chunk)
        for evidence_id, chunk in chunk_by_id.items()
    }
    if not citation_by_id:
        return answer

    lines = [re.sub(r"^(\s*-\s*)E\d{3}:\s*", r"\1", line) for line in answer.splitlines()]
    text = "\n".join(lines)





    for evidence_id, chunk in chunk_by_id.items():
        labels = [citation_by_id[evidence_id]]
        author = str(chunk.get("author") or "").strip()
        year = str(chunk.get("year") or "").strip()
        if author:
            author_pattern = re.escape(author)
            year_pattern = rf"(?:,?\s*{re.escape(year)})?" if year else ""
            labels.append(rf"{author_pattern}(?:\s+et\s+al\.)?{year_pattern}")
        for label in dict.fromkeys(labels):
            label_pattern = re.escape(label) if label == citation_by_id[evidence_id] else label
            text = re.sub(
                rf"(?<!\w){label_pattern}\s*[;,]\s*{re.escape(evidence_id)}\b",
                evidence_id,
                text,
                flags=re.IGNORECASE,
            )

    def source_identities(evidence_id: str) -> tuple[tuple[str, str], ...]:
        chunk = chunk_by_id[evidence_id]
        paper_id = str(chunk.get("paper_id") or "").strip()
        doi = normalize_doi(chunk.get("doi"))
        identities = []
        if doi:
            identities.append(("doi", doi))
        if paper_id:
            identities.append(("paper_id", paper_id))
        return tuple(identities) or (
            ("citation", citation_by_id[evidence_id].casefold()),
        )

    def replace_group(match: re.Match[str]) -> str:
        evidence_ids = [
            evidence_id
            for evidence_id in re.findall(r"E\d{3}", match.group(0))
            if evidence_id in chunk_by_id
        ]
        unique_ids = []
        seen_sources: set[tuple[str, str]] = set()
        for evidence_id in evidence_ids:
            identities = source_identities(evidence_id)
            if any(identity in seen_sources for identity in identities):
                seen_sources.update(identities)
                continue
            seen_sources.update(identities)
            unique_ids.append(evidence_id)
        labels = [f"[[cite:{evidence_id}]]" for evidence_id in unique_ids]
        return f"({'; '.join(labels)})"

    text = re.sub(r"\((?:\s*E\d{3}\s*[;,]?\s*)+\)", replace_group, text)
    return re.sub(
        r"(?<!\[)\bE\d{3}\b(?!\])",
        lambda match: f"[[cite:{match.group(0)}]]",
        text,
    )


def public_citation_short(chunk: dict) -> str:
    citation = str(chunk.get("citation_short") or "").strip()
    if citation and not citation.lower().startswith("unknown et al."):
        return citation
    author = str(chunk.get("author") or "").strip()
    year = str(chunk.get("year") or "n.d.").strip()
    if author:
        return f"{author} et al., {year}"
    title = " ".join(str(chunk.get("title") or "Untitled").split())
    short_title = " ".join(title.split()[:6])
    return f"{short_title}, {year}"


def remove_public_heading(answer: str, heading: str) -> str:
    """Keep internal answer sections, but present normal chat as readable prose."""
    return re.sub(rf"(?im)^\s*{re.escape(heading)}\s*$\n?", "", answer)


def strip_chunk_mentions(answer: str) -> str:
    text = re.sub(r",?\s*chunk\s+[A-Za-z0-9_\\-]+", "", answer)
    text = re.sub(r",?\s*chunk\s+C\d+", "", text)
    text = re.sub(r"\bretrieved evidence chunks\b", "retrieved evidence", text, flags=re.IGNORECASE)
    text = re.sub(r"\bevidence chunks\b", "retrieved evidence", text, flags=re.IGNORECASE)
    text = re.sub(r"\bchunks\b", "evidence", text, flags=re.IGNORECASE)
    return text


def remove_internal_evidence_lines(answer: str) -> str:
    kept = []
    for line in answer.splitlines():
        lowered = line.lower()
        if "supporting evidence labels:" in lowered:
            continue
        if re.match(r"^\s*-\s*the cited sources\s*:", line, flags=re.IGNORECASE):
            continue
        kept.append(line)
    return "\n".join(kept)


def remove_markdown_section(answer: str, heading: str) -> str:
    start = answer.find(heading)
    if start < 0:
        return answer
    remainder = answer[start + len(heading) :]
    next_heading = re.search(r"\n#{2,3}\s+", remainder)
    if not next_heading:
        return answer[:start].rstrip()
    end = start + len(heading) + next_heading.start()
    return (answer[:start].rstrip() + "\n\n" + answer[end:].lstrip()).strip()


def format_public_sources(
    chunks: list[dict],
    query: str | None = None,
    *,
    library_resolver: LibraryStatusResolver | None = None,
    source_role: str = "answer_evidence",
) -> list[dict]:
    filtered_chunks = filter_source_chunks(chunks, query) if query else chunks
    resolver = library_resolver or default_library_resolver()
    evidence_ids_by_source: dict[tuple[str, str], list[str]] = {}
    for chunk in filtered_chunks:
        evidence_id = str(chunk.get("evidence_id") or "")
        if evidence_id:
            evidence_ids_by_source.setdefault(source_identity(chunk), []).append(evidence_id)
    sources = []
    seen = set()
    for chunk in filtered_chunks:
        key = source_identity(chunk)
        if key in seen:
            continue
        seen.add(key)
        status = resolver.resolve(chunk)
        registry_record = (status.get("registry") or {}).get("record") or {}
        source = {
            **registry_record,
            **{key: value for key, value in chunk.items() if value not in (None, "")},
        }
        source_type = source_type_for_record(source)
        question_fields, question_field_statuses, question_fields_status = (
            public_question_field_provenance(source)
        )
        sources.append(
            {
                "paper_id": source.get("paper_id") or status.get("paper_id"),
                "bib_key": source.get("bib_key") or "",
                "author": source.get("author") or "Unknown",
                "year": source.get("year") or "n.d.",
                "doi": source.get("doi") or "",
                "pmid": source.get("pmid") or "",
                "pmcid": source.get("pmcid") or "",
                "url": source_url(source),
                "title": source.get("title") or "Untitled",
                "journal": source.get("journal") or "Unknown journal",
                "citation": (source.get("citation_apa") or source.get("citation_short") or ""),
                "citation_short": public_citation_short(source),
                "evidence_ids": sorted(set(evidence_ids_by_source.get(key, []))),
                "source_role": source_role,
                "source_type": source_type,
                "match_reason": source.get("selection_reason") or source.get("rationale") or "",
                "match_reason_status": source.get("selection_reason_status") or "retrieval",
                "question_fields": question_fields,
                "question_field_statuses": question_field_statuses,
                "question_fields_status": question_fields_status,
                "publication_version_display": source.get(
                    "publication_version_display"
                )
                or "",
                "publication_version_relationship_types": list(
                    source.get("publication_version_relationship_types") or []
                ),
                "publication_family_reports": list(
                    source.get("publication_family_reports") or []
                ),
                "publication_family_report_count": int(
                    source.get("publication_family_report_count") or 1
                ),
                "library_status": compact_library_status(status),
                "can_trace_citations": bool((status.get("local_pdf") or {}).get("exists")),
                "trace_claim_text": str(source.get("text") or "")[:1800],
            }
        )
    return sources[:PUBLIC_SOURCE_LIMIT]


def public_question_field_provenance(
    source: dict,
) -> tuple[dict[str, object], dict[str, str], str]:
    """Expose model extraction as unverified and normalize persisted legacy rows."""

    fields = (
        dict(source.get("question_fields") or {})
        if isinstance(source.get("question_fields"), dict)
        else {}
    )
    if not fields:
        return {}, {}, ""
    supplied_statuses = (
        dict(source.get("question_field_statuses") or {})
        if isinstance(source.get("question_field_statuses"), dict)
        else {}
    )
    legacy_overall = str(source.get("question_fields_status") or "")
    statuses: dict[str, str] = {}
    for field in fields:
        raw_status = str(supplied_statuses.get(field) or legacy_overall)
        statuses[field] = (
            "model_extracted_unverified"
            if raw_status in {"qwen_grounded", "model_extracted_unverified"}
            else (
                raw_status
                if raw_status in {
                    "deterministic_excerpt_supported", "not_assessed_context_budget",
                }
                else "not_reported_fallback"
            )
        )
    unique_statuses = set(statuses.values())
    overall = next(iter(unique_statuses)) if len(unique_statuses) == 1 else "mixed_extraction"
    return fields, statuses, overall


def attach_automatic_paper_search_traces(sources: list[dict]) -> list[dict]:
    """Resolve excerpt-local citations when a search hit may be carrying another study's claim."""
    secondary_types = {
        "systematic_review_meta_analysis",
        "narrative_review",
        "editorial_commentary",
        "reanalysis_secondary_data",
    }
    enriched = []
    for source in sources:
        item = dict(source)
        source_type = str((item.get("source_type") or {}).get("source_type") or "uncertain")
        excerpt = str(item.get("trace_claim_text") or "")
        attributed = bool(
            re.search(
                r"\b[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.?)?\s*[,;(]?\s*(?:19|20)\d{2}\b",
                excerpt,
            )
        )
        should_trace = source_type in secondary_types or attributed
        item["citation_trace_required"] = should_trace
        item["cited_sources"] = []
        if should_trace and item.get("can_trace_citations"):
            try:
                trace = build_public_source_trace(
                    str(item.get("paper_id") or ""),
                    excerpt,
                )
                item["citation_trace_status"] = trace.get("status") or "unresolved"
                item["cited_sources"] = [
                    reference
                    for reference in trace.get("references") or []
                    if reference.get("bibliographic_resolution_status")
                    in {"exact_id", "exact_title"}
                ][:5]
            except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                item["citation_trace_status"] = "unavailable"
                item["citation_trace_error"] = str(exc)
        elif should_trace:
            item["citation_trace_status"] = "carrier_pdf_missing"
        else:
            item["citation_trace_status"] = "not_required"
        enriched.append(item)
    return enriched


def source_identity(source: dict) -> tuple[str, str]:
    normalizers = {
        "doi": normalize_doi,
        "pmcid": normalize_pmcid,
        "pmid": normalize_pmid,
        "paper_id": lambda value: str(value or "").strip(),
    }
    for field, normalizer in normalizers.items():
        value = normalizer(source.get(field))
        if value:
            return field, value
    return "title_year", "|".join(
        [normalize_title(source.get("title")), str(source.get("year") or "").strip()]
    )


def default_library_resolver() -> LibraryStatusResolver:
    load_project_env(REPO_ROOT / ".env")
    try:
        bib_dir = get_bib_dir()
    except OriginRAGConfigError:
        bib_dir = REPO_ROOT / "bib"
    metadata = REPO_ROOT / "data/metadata"
    registry = metadata / "paper_registry.tsv"
    return LibraryStatusResolver(
        registry_path=registry if registry.is_file() else metadata / "papers.tsv",
        bib_dir=bib_dir,
        chunks_dir=REPO_ROOT / "data/chunks",
        allow_missing_registry=True,
    )


def compact_library_status(status: dict) -> dict:
    return {
        "state": status.get("state"),
        "symbol": status.get("symbol"),
        "label": status.get("label"),
        "display": status.get("display"),
        "can_queue": bool(status.get("can_queue")),
        "local_pdf": bool((status.get("local_pdf") or {}).get("exists")),
        "local_chunk": bool((status.get("local_chunk") or {}).get("exists")),
        "oa_url": (status.get("oa") or {}).get("url"),
    }


def build_public_source_trace(paper_id: str, claim_text: str) -> dict:
    clean_paper_id = " ".join((paper_id or "").split())
    clean_claim = " ".join((claim_text or "").split())
    if not clean_paper_id or not clean_claim:
        raise ValueError("paper_id and claim_text are required")
    resolver = default_library_resolver()
    carrier_status = resolver.resolve({"paper_id": clean_paper_id})
    pdf_path_text = str((carrier_status.get("local_pdf") or {}).get("path") or "")
    pdf_path = Path(pdf_path_text) if pdf_path_text else None
    if not pdf_path or not pdf_path.is_file():
        raise ValueError("The carrier PDF is not available on this machine.")


    from originrag.provenance.references import extract_pdf_citation_map, trace_claim_sources
    from originrag.provenance.resolver import resolve_trace_against_library

    citation_map = extract_pdf_citation_map(pdf_path, clean_paper_id)
    trace = trace_claim_sources(citation_map, clean_claim, min_score=55.0, limit=12)
    trace = resolve_trace_against_library(trace, resolver)
    references = []
    seen = set()
    for mention in trace.get("mentions", []):
        reference = dict(mention.get("reference") or {})
        identity = str(reference.get("work_id") or reference.get("citation_text") or "")
        if identity in seen:
            continue
        seen.add(identity)
        status = reference.get("library_status") or {}
        resolution = str(reference.get("bibliographic_resolution_status") or "unresolved")
        public_reference = {
                "work_id": reference.get("work_id") or "",
                "paper_id": reference.get("paper_id") or status.get("paper_id") or "",
                "citation": reference.get("citation_display")
                or reference.get("citation_text")
                or "",
                "doi": reference.get("doi") or "",
                "pmid": reference.get("pmid") or "",
                "pmcid": reference.get("pmcid") or "",
                "title": reference.get("title") or "",
                "year": reference.get("year") or "",
                "url": reference.get("landing_url") or source_url(reference),
                "bibliographic_resolution_status": resolution,
                "verified_identifiers": reference.get("verified_identifiers") or {},
                "identifier_verified": bool(
                    any((reference.get("verified_identifiers") or {}).values())
                ),
                "citation_link_status": mention.get("citation_link_status"),
                "support_role": mention.get("support_role"),
                "support_relation": mention.get("support_relation"),
                "review_status": mention.get("origin_review_status"),
                "source_page": mention.get("source_page"),
                "marker_text": mention.get("marker_text"),
                "context_text": mention.get("context_text"),
                "library_status": compact_library_status(status),
                "can_queue": (
                    resolution
                    in {
                        "exact_id",
                        "exact_title",
                        "identifier_extracted_unverified",
                    }
                    and bool(status.get("can_queue"))
                    and any(reference.get(field) for field in ("doi", "pmid", "pmcid"))
                ),
            }
        public_reference["source_type"] = source_type_for_record(public_reference)
        public_reference["direct_report_status"] = direct_report_candidate_status(public_reference)
        references.append(public_reference)
    return {
        "status": trace.get("status"),
        "paper_id": clean_paper_id,
        "references": references,
        "policy": trace.get("source_chain_policy"),
    }


def source_url(chunk: dict) -> str:
    doi = str(chunk.get("doi") or "").strip()
    if doi:
        return doi if doi.startswith(("http://", "https://")) else f"https://doi.org/{doi}"
    pmcid = str(chunk.get("pmcid") or "").strip()
    if pmcid:
        normalized_pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{normalized_pmcid}/"
    pmid = str(chunk.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return ""


def filter_source_chunks(chunks: list[dict], query: str | None) -> list[dict]:
    terms = source_lookup_terms(query or "")
    if not terms:
        return chunks
    sample_threshold = source_lookup_sample_threshold(query or "")
    ranked = []
    for index, chunk in enumerate(chunks):
        searchable = " ".join(
            str(chunk.get(field) or "")
            for field in [
                "title",
                "journal",
                "author",
                "year",
                "doi",
                "pmid",
                "pmcid",
                "text",
                "snippet",
            ]
        ).lower()
        score = sum(1 for term in terms if term in searchable)
        score += source_lookup_sample_score(searchable, sample_threshold)
        if score > 0:
            ranked.append((index, score, chunk))
    if not ranked:
        return chunks
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return [chunk for _, _, chunk in ranked]


def source_lookup_terms(query: str) -> set[str]:
    terms = set(re.findall(r"[a-zäöüß]+", query.lower()))
    if "kenya" in terms:
        terms.update({"kenyan", "kilifi", "chonyi", "western"})
    if "kohorte" in terms:
        terms.add("cohort")
    stop_terms = {
        "paper",
        "papers",
        "quelle",
        "quellen",
        "source",
        "sources",
        "reference",
        "references",
        "hieß",
        "hiess",
        "welche",
        "welches",
        "with",
        "that",
        "about",
    }
    return {term for term in terms if len(term) >= 4 and term not in stop_terms}


def source_lookup_sample_threshold(query: str) -> int | None:
    lowered = query.lower()
    patterns = [
        r"(?:over|more than|above|at least|about|around|ca|circa)\s+(\d{2,5})",
        r"(\d{2,5})\s*(?:\+|plus)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    return None


def source_lookup_sample_score(text: str, threshold: int | None) -> int:
    if not threshold:
        return 0
    numbers = [
        int(match.group(0))
        for match in re.finditer(r"\b\d{2,5}\b", text)
        if not 1900 <= int(match.group(0)) <= 2100
    ]
    if any(
        abs(number - threshold) <= 75 and sample_number_context(text, str(number))
        for number in numbers
    ):
        return 12
    sample_numbers = [number for number in numbers if sample_number_context(text, str(number))]
    if any(number >= threshold for number in sample_numbers):
        return 10
    if len(sample_numbers) >= 2 and sum(sample_numbers[:4]) >= threshold:
        return 8
    return 0


def sample_number_context(text: str, number_text: str) -> bool:
    match = re.search(rf"\b{re.escape(number_text)}\b", text)
    if not match:
        return False
    window = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
    return any(
        term in window
        for term in [
            "case",
            "cases",
            "child",
            "children",
            "cohort",
            "participant",
            "participants",
            "patient",
            "patients",
            "sample",
            "samples",
            "severe",
            "non-severe",
            "non severe",
        ]
    )


def append_turn_summary(
    session_dir: Path,
    user_text: str,
    retrieval_question: str,
    resolved_task: str,
    result: dict,
    turn_dir: Path,
) -> None:
    qc = result.get("answer_qc") or {}
    attempts = result.get("answer_attempts") or []
    top_sources = [
        f"- {chunk.get('evidence_id')}: {chunk.get('citation_label')}"
        for chunk in result.get("pack", {}).get("chunks", [])[:8]
    ]
    append_session_line(
        session_dir,
        "\n".join(
            [
                f"\n## Turn: {user_text}",
                f"Retrieval question: {retrieval_question}",
                f"Task: {resolved_task}",
                f"Output: {relative_path(turn_dir)}",
                f"QC passed: {qc.get('passed')}",
                f"Attempts: {', '.join(str(item.get('kind')) for item in attempts) or 'none'}",
                f"Evidence table: {relative_path(result.get('evidence_table_tsv_path')) if result.get('evidence_table_tsv_path') else 'none'}",
                "Sources:",
                *top_sources,
                "",
            ]
        ),
    )


def safe_repo_file(path_text: str) -> Path:
    if not path_text:
        raise ValueError("Missing path.")
    path = (
        (REPO_ROOT / path_text).resolve()
        if not Path(path_text).is_absolute()
        else Path(path_text).resolve()
    )
    if not path.is_relative_to(REPO_ROOT):
        raise ValueError("File path must stay inside the OriginRAG repository.")
    return path


def safe_public_output_file(path_text: str, out_dir: Path) -> Path:
    """Resolve a text artifact while preventing arbitrary repository-file reads."""
    if not path_text:
        raise ValueError("Missing path.")
    candidate = Path(path_text)
    path = candidate.resolve() if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
    output_root = out_dir.resolve() if out_dir.is_absolute() else (REPO_ROOT / out_dir).resolve()
    if not path.is_relative_to(output_root):
        raise ValueError("File path must stay inside the web chat output directory.")
    if path.suffix.lower() not in {".json", ".md", ".tsv", ".txt"}:
        raise ValueError("Only public text artifacts can be opened.")
    return path


def make_session_dir(base_dir: Path) -> Path:
    base = base_dir if base_dir.is_absolute() else REPO_ROOT / base_dir
    session_stem = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = base / session_stem
    counter = 2
    while session_dir.exists():
        session_dir = base / f"{session_stem}_{counter:02d}"
        counter += 1
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def next_session_turn_index(session_dir: Path) -> int:
    """Return a collision-free turn number for one serialized conversation."""

    indices = []
    for path in session_dir.glob("turn_*"):
        match = re.match(r"turn_(\d+)_", path.name)
        if match:
            indices.append(int(match.group(1)))
    return max([len(read_session_turns(session_dir)), *indices], default=0) + 1


def append_session_line(session_dir: Path, text: str) -> None:
    with (session_dir / "session.md").open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def read_session_turns(session_dir: Path) -> list[dict]:
    session_file = session_dir / "session.md"
    if not session_file.exists():
        return []
    turns: list[dict] = []
    current: dict | None = None
    reading_sources = False
    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## Turn: "):
            if current:
                turns.append(current)
            current = {"user": line.removeprefix("## Turn: ").strip()}
            reading_sources = False
        elif current is not None and line.startswith("Retrieval question: "):
            current["retrieval_question"] = line.removeprefix("Retrieval question: ").strip()
            reading_sources = False
        elif current is not None and line.startswith("Task: "):
            current["task"] = line.removeprefix("Task: ").strip()
            reading_sources = False
        elif current is not None and line.startswith("Output: "):
            current["output"] = line.removeprefix("Output: ").strip()
            reading_sources = False
        elif current is not None and line == "Sources:":
            current["sources"] = []
            reading_sources = True
        elif current is not None and reading_sources and line.startswith("- "):
            current.setdefault("sources", []).append(line.removeprefix("- ").strip())
        elif current is not None and line and not line.startswith("  "):
            reading_sources = False
    if current:
        turns.append(current)
    return turns


def session_messages(session_dir: Path) -> list[dict]:
    messages = []
    for turn in read_session_turns(session_dir):
        user_text = str(turn.get("user") or "").strip()
        if user_text:
            messages.append({"role": "user", "text": user_text})
        saved_result = saved_turn_result(turn)
        answer = str(saved_result.get("answer") or "") or saved_turn_answer(turn)
        if answer:
            message = {"role": "assistant", "text": answer}
            if saved_result:
                message["result"] = saved_result
            messages.append(message)
    return messages


def saved_turn_result(turn: dict) -> dict:
    output_text = str(turn.get("output") or "")
    if not output_text:
        return {}
    try:
        output_dir = safe_repo_file(output_text)
    except ValueError:
        return {}
    result_path = output_dir / "public_result.json"
    if not result_path.exists() or not result_path.is_file():
        return {}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def saved_turn_answer(turn: dict) -> str:
    output_text = str(turn.get("output") or "")
    if not output_text:
        return ""
    try:
        output_dir = safe_repo_file(output_text)
    except ValueError:
        return ""
    answer_path = output_dir / "answer.md"
    if not answer_path.exists() or not answer_path.is_file():
        return ""
    return public_answer_text(answer_path.read_text(encoding="utf-8", errors="replace"))


def list_saved_sessions(
    out_dir: Path, current_session_dir: Path, limit: int | None = None
) -> list[dict]:
    base = out_dir if out_dir.is_absolute() else REPO_ROOT / out_dir
    if not base.exists():
        return []
    session_dirs = sorted(
        [path for path in base.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if limit is not None:
        session_dirs = session_dirs[:limit]
    sessions = []
    for session_dir in session_dirs:
        if not session_should_be_listed(session_dir):
            continue
        session_file = session_dir / "session.md"
        sessions.append(
            {
                "title": session_title(session_dir),
                "path": relative_path(session_file),
                "session_dir": relative_path(session_dir),
                "current": session_dir.resolve() == current_session_dir.resolve(),
            }
        )
    return sessions


def sessions_with_job_status(sessions: list[dict], jobs: list[dict]) -> list[dict]:
    """Attach only the current owner's aggregate per-chat job state."""

    jobs_by_session: dict[str, list[dict]] = {}
    for job in jobs:
        if str(job.get("state") or "") == "complete":
            continue
        jobs_by_session.setdefault(str(job.get("session_dir") or ""), []).append(job)
    priority = {"running": 0, "cancelling": 0, "queued": 1, "failed": 2, "interrupted": 3}
    enriched = []
    for session in sessions:
        session_jobs = jobs_by_session.get(str(session.get("session_dir") or ""), [])
        active = sorted(
            session_jobs,
            key=lambda item: (
                priority.get(str(item.get("state") or ""), 9),
                float(item.get("created_at") or 0),
            ),
        )
        copy = dict(session)
        copy["job_count"] = len(session_jobs)
        copy["job_state"] = str(active[0].get("state") or "") if active else ""
        copy["queue_position"] = (
            active[0].get("queue_position") if active else None
        )
        enriched.append(copy)
    return enriched


def session_should_be_listed(session_dir: Path) -> bool:
    title_file = session_dir / TITLE_FILENAME
    if (
        title_file.exists()
        and sanitize_session_title(title_file.read_text(encoding="utf-8", errors="replace"))
        != "New chat"
    ):
        return True
    return bool(read_session_turns(session_dir))


def enforce_saved_chat_limit(
    app: WebChatApp,
    *,
    creating_new: bool,
    target_session: Path | None = None,
) -> None:
    """Enforce quota against the chat receiving the request, not another tab."""

    session_dir = app.session_dir if target_session is None else target_session
    saved_count = len(list_saved_sessions(app.out_dir, session_dir))
    current_is_saved = session_should_be_listed(session_dir)
    if saved_count >= MAX_SAVED_CHATS and (creating_new or not current_is_saved):
        raise QuotaExceededError(
            f"Saved-chat limit reached ({MAX_SAVED_CHATS}). Delete an old chat before creating another."
        )


def session_title(session_dir: Path) -> str:
    title_file = session_dir / TITLE_FILENAME
    if title_file.exists():
        return sanitize_session_title(title_file.read_text(encoding="utf-8", errors="replace"))
    session_file = session_dir / "session.md"
    if not session_file.exists():
        return "New chat"
    text = session_file.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("## Turn: "):
            return infer_session_title(line.removeprefix("## Turn: "), "auto")
    return "New chat"


def ensure_session_title(session_dir: Path, title: str) -> None:
    title_file = session_dir / TITLE_FILENAME
    if title_file.exists():
        return
    write_session_title(session_dir, title)


def write_session_title(session_dir: Path, title: str) -> str:
    clean_title = sanitize_session_title(title)
    (session_dir / TITLE_FILENAME).write_text(f"{clean_title}\n", encoding="utf-8")
    return clean_title


def rename_saved_session(
    out_dir: Path,
    current_session_dir: Path,
    session_dir_text: str,
    title: str,
) -> str:
    session_dir = safe_session_dir(out_dir, session_dir_text)
    clean_title = sanitize_session_title(title)
    if clean_title == "New chat" and title.strip():
        raise ValueError("Chat title must contain letters, not only numbers or symbols.")
    written_title = write_session_title(session_dir, clean_title)
    if session_dir.resolve() == current_session_dir.resolve():
        return written_title
    return written_title


def safe_session_dir(out_dir: Path, session_dir_text: str) -> Path:
    if not session_dir_text:
        raise ValueError("Missing session directory.")
    base = (out_dir if out_dir.is_absolute() else REPO_ROOT / out_dir).resolve()
    candidate = Path(session_dir_text)
    candidate = (
        candidate.resolve() if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
    )
    if not candidate.is_relative_to(base):
        raise ValueError("Session path must stay inside the web chat output directory.")
    if not candidate.exists() or not candidate.is_dir():
        raise SessionNotFoundError("Session not found.")
    return candidate


def sanitize_session_title(title: str) -> str:
    clean_title = re.sub(r"[\r\n\t]+", " ", title or "")
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" .:/\\")
    if not clean_title or re.fullmatch(r"[\d\s:_-]+", clean_title):
        return "New chat"
    return clean_title[:TITLE_MAX_CHARS].rstrip(" .:/\\") or "New chat"


def infer_session_title(message: str, task: str = "auto") -> str:
    text = " ".join((message or "").split())
    lower = text.lower()
    topic = title_topic(lower)
    resolved_task = title_task(task, lower)

    if topic == "Kenya Cohort":
        return topic
    if resolved_task == "gaps":
        return sanitize_session_title(f"{topic or 'Research'} Gaps")
    if resolved_task == "hypothesis":
        return sanitize_session_title(f"{topic} Hypothesis" if topic else "Hypothesis")
    if resolved_task == "sources":
        return sanitize_session_title(f"{topic or 'Source'} Search")
    if topic:
        return sanitize_session_title(topic)

    tokens = [canonical_title_token(token) for token in re.findall(r"[A-Za-z0-9ÄÖÜäöüß]+", text)]
    useful = [
        token
        for token in tokens
        if len(token) >= 3 and token.lower() not in TITLE_STOPWORDS and not token.isdigit()
    ]
    if useful:
        return sanitize_session_title(" ".join(useful[:2]))
    return "New chat"


def title_topic(lower_text: str) -> str | None:
    if re.search(r"\bkenya\b|\bkenyan\b|\bkilifi\b", lower_text):
        return "Kenya Cohort"
    if re.search(r"\bparasite (?:gene )?(?:expression|transcriptome)\b", lower_text):
        return "Parasite Expression"
    if "rifin" in lower_text and "stevor" in lower_text:
        return "RIFIN/STEVOR"
    if "rifin" in lower_text:
        return "RIFIN"
    if "stevor" in lower_text:
        return "STEVOR"
    if "pfemp1" in lower_text:
        return "PfEMP1"
    if re.search(r"\bvar\b|var gene|var genes", lower_text):
        return "Var Genes"
    if re.search(
        r"sex difference|sex differences|sex-specific|sex specific|female|male", lower_text
    ):
        return "Sex"
    if re.search(r"\bt[ -]cells?\b", lower_text):
        return "T Cells"
    if re.search(r"single cell|scrna|sc-rna", lower_text):
        return "Single Cell"
    if "severe" in lower_text:
        return "Severe Malaria"
    if "asymptomatic" in lower_text:
        return "Asymptomatic"
    if re.search(r"resistance|resistant", lower_text):
        return "Resistance"
    if "diagnostic" in lower_text:
        return "Diagnostics"
    if "vaccine" in lower_text:
        return "Vaccine"
    return None


def title_task(task: str, lower_text: str) -> str:
    resolved_task = (task or "auto").lower()
    if resolved_task != "auto":
        return resolved_task
    if re.search(r"\bgap\b|\bgaps\b|lücke|lücken|underexplored|unknown", lower_text):
        return "gaps"
    if re.search(r"hypothesis|hypothese|hypotheses|testable", lower_text):
        return "hypothesis"
    if re.search(
        r"which paper|what paper|source|reference|quelle|paper mit|hieß|hiess", lower_text
    ):
        return "sources"
    return "answer"


def canonical_title_token(token: str) -> str:
    override = TITLE_TOKEN_OVERRIDES.get(token.lower())
    if override:
        return override
    return token[:1].upper() + token[1:].lower()


def relative_path(path: object) -> str | None:
    if path in (None, ""):
        return None
    try:
        path_obj = Path(path)
    except TypeError:
        return None
    try:
        return str(path_obj.relative_to(REPO_ROOT))
    except ValueError:
        return str(path_obj)


def public_user(user: AuthenticatedUser) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


def public_queue_details(
    manager: TurnJobManager,
    user: AuthenticatedUser | None,
    auth_store: AuthStore | None,
) -> tuple[str, list[dict]]:
    """Expose queue membership without exposing questions or private job data."""

    owner_key = user.user_id if user else "anonymous"
    is_admin = bool(user and user.role == "admin" and auth_store)
    is_authenticated = bool(user and auth_store)
    account_by_id = (
        {str(item["user_id"]): item for item in auth_store.list_users()}
        if is_authenticated and auth_store
        else {}
    )
    jobs = manager.queue_details(None if is_authenticated else owner_key)
    public = []
    for job in jobs:
        job_owner = str(job.get("owner_key") or "")
        account = account_by_id.get(job_owner, {})
        same_user = hmac.compare_digest(job_owner, owner_key)
        public.append(
            {
                "state": str(job.get("state") or ""),
                "stage": str(job.get("stage") or ""),
                "queue_position": job.get("queue_position"),
                "owner_display_name": (
                    str(account.get("display_name") or account.get("username") or "Unknown user")
                    if is_authenticated
                    else "You"
                ),
                "owner_username": str(account.get("username") or "") if is_admin else "",
                "is_current_user": same_user,
            }
        )
    return ("global" if is_authenticated else "own"), public


def auth_cookie_headers(
    session_token: str,
    csrf_token: str,
    *,
    secure: bool,
    max_age: int = 28800,
) -> list[tuple[str, str]]:
    secure_part = "; Secure" if secure else ""
    bounded_max_age = max(1, int(max_age))
    return [
        (
            "Set-Cookie",
            f"pfempire_session={session_token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={bounded_max_age}{secure_part}",
        ),
        (
            "Set-Cookie",
            f"pfempire_csrf={csrf_token}; Path=/; SameSite=Strict; "
            f"Max-Age={bounded_max_age}{secure_part}",
        ),
    ]


def expired_auth_cookie_headers(*, secure: bool) -> list[tuple[str, str]]:
    secure_part = "; Secure" if secure else ""
    return [
        (
            "Set-Cookie",
            f"pfempire_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure_part}",
        ),
        (
            "Set-Cookie",
            f"pfempire_csrf=; Path=/; SameSite=Strict; Max-Age=0{secure_part}",
        ),
    ]


LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OriginRAG · Sign in</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #031018; color: #edf8f8; }
    main { width: min(440px, calc(100vw - 32px)); padding: 32px; border: 1px solid #244552; border-radius: 24px; background: #071821; box-shadow: 0 24px 80px #0008; }
    h1 { margin: 0 0 8px; font-size: 30px; letter-spacing: .04em; }
    p { color: #9bb1b8; line-height: 1.5; }
    label { display: block; margin: 16px 0 7px; font-weight: 700; }
    input { width: 100%; padding: 13px 14px; border: 1px solid #355864; border-radius: 12px; background: #041219; color: inherit; font: inherit; }
    input:focus { outline: 2px solid #42cdbb; outline-offset: 2px; }
    button { width: 100%; margin-top: 20px; padding: 13px; border: 0; border-radius: 12px; background: #42cdbb; color: #02201c; font: inherit; font-weight: 800; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    .tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 22px 0 6px; }
    .tabs button { margin: 0; background: #0c2630; color: #a9c2c8; }
    .tabs button.active { background: #42cdbb; color: #02201c; }
    .panel[hidden], form[hidden] { display: none; }
    .check { display: flex; gap: 9px; align-items: center; margin-top: 14px; color: #b8cbd0; font-weight: 500; }
    .check input { width: 17px; height: 17px; }
    .message { min-height: 24px; color: #ff9494; }
    .secondary { margin-top: 10px; background: transparent; color: #b8cbd0; border: 1px solid #355864; }
    .password-help { margin: 8px 0 0; padding-left: 18px; color: #8fa7ad; font-size: 12px; line-height: 1.45; }
    .password-help li { margin: 3px 0; }
    .password-help li.met { color: #65dfc8; }
    .password-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; }
    .password-row input { min-width: 0; }
    .password-row button { width: auto; min-width: 92px; margin: 0; padding: 11px 12px; background: #0c2630; color: #b8cbd0; border: 1px solid #355864; }
    .strength { display: flex; align-items: center; gap: 8px; margin-top: 9px; color: #9bb1b8; font-size: 12px; }
    .strength-bar { flex: 1; height: 5px; overflow: hidden; border-radius: 99px; background: #17313a; }
    .strength-fill { width: 0; height: 100%; background: #ff9494; transition: width .15s ease, background .15s ease; }
    .generate { margin-top: 9px; background: transparent; color: #65dfc8; border: 1px solid #355864; }
    .notice { font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <h1>OriginRAG</h1>
    <p>Self-hosted scientific literature assistant.</p>
    <div class="tabs" role="tablist">
      <button id="loginTab" class="active" type="button">Sign in</button>
      <button id="registerTab" type="button">Create account</button>
    </div>
    <section class="panel" id="loginPanel">
      <form id="loginForm">
        <label for="loginIdentity">Account name</label>
        <input id="loginIdentity" autocomplete="username" required autofocus />
        <label for="loginPassword">Password</label>
        <input id="loginPassword" type="password" autocomplete="current-password" required />
        <label class="check"><input id="loginRemember" type="checkbox" /> Keep me signed in on this device</label>
        <button id="loginSubmit" type="submit">Sign in</button>
        <p id="loginMessage" class="message" role="alert"></p>
      </form>
    </section>
    <section class="panel" id="registerPanel" hidden>
      <form id="registerForm">
        <label for="registerUsername">Account name</label>
        <input id="registerUsername" autocomplete="username" minlength="3" maxlength="32" pattern="[a-z][a-z0-9._-]{2,31}" placeholder="e.g. alex" required aria-describedby="usernameHelp" />
        <p class="notice" id="usernameHelp">3–32 lowercase characters: letters, numbers, dot, dash, or underscore. The name must still be available.</p>
        <label for="registerName">Display name <span class="notice">(optional)</span></label>
        <input id="registerName" autocomplete="name" maxlength="80" />
        <label for="registerPassword">Password</label>
        <div class="password-row">
          <input id="registerPassword" type="password" autocomplete="new-password" minlength="13" required aria-describedby="passwordRequirements passwordStrength" />
          <button id="togglePassword" type="button" aria-pressed="false">Show</button>
        </div>
        <div class="strength" id="passwordStrength" aria-live="polite"><span id="strengthLabel">Not assessed</span><span class="strength-bar"><span class="strength-fill" id="strengthFill"></span></span></div>
        <ul class="password-help" id="passwordRequirements">
          <li id="requireLength">At least 13 characters</li>
          <li id="requireLetters">At least one letter (upper- or lowercase)</li>
          <li id="requireNumbers">At least two numbers</li>
          <li id="requireSpecial">At least one special character</li>
          <li id="requireIdentity">Does not contain your account or display name</li>
          <li id="requireMatch">Both password fields match</li>
        </ul>
        <button class="generate" id="generatePassword" type="button">Generate a strong password</button>
        <label for="registerPasswordConfirm">Repeat password</label>
        <input id="registerPasswordConfirm" type="password" autocomplete="new-password" minlength="13" required />
        <label class="check"><input id="registerRemember" type="checkbox" /> Keep me signed in on this device</label>
        <button id="registerSubmit" type="submit">Create account and open OriginRAG</button>
        <p id="registerMessage" class="message" role="alert"></p>
      </form>
    </section>
    <p class="notice">Do not enter patient-identifying or otherwise restricted data.</p>
  </main>
  <script>
    const loginTab = document.getElementById("loginTab");
    const registerTab = document.getElementById("registerTab");
    const loginPanel = document.getElementById("loginPanel");
    const registerPanel = document.getElementById("registerPanel");
    const registerForm = document.getElementById("registerForm");
    const passwordInput = document.getElementById("registerPassword");
    const passwordConfirm = document.getElementById("registerPasswordConfirm");

    function showPanel(name) {
      const login = name === "login";
      loginPanel.hidden = !login;
      registerPanel.hidden = login;
      loginTab.classList.toggle("active", login);
      registerTab.classList.toggle("active", !login);
    }
    loginTab.addEventListener("click", () => showPanel("login"));
    registerTab.addEventListener("click", () => showPanel("register"));

    function identityTerms() {
      const username = document.getElementById("registerUsername").value || "";
      const displayName = document.getElementById("registerName").value || "";
      return [username, displayName]
        .map((value) => value.toLowerCase().replace(/[^a-z0-9]/g, ""))
        .filter((value) => value.length >= 3);
    }

    function updatePasswordStrength() {
      const value = passwordInput.value;
      const compact = value.toLowerCase().replace(/\s/g, "");
      const lengthMet = value.length >= 13;
      const lettersMet = /[A-Za-z]/.test(value);
      const numbersMet = (value.match(/[0-9]/g) || []).length >= 2;
      const specialMet = /[^A-Za-z0-9\s]/.test(value);
      const identityMet = !identityTerms().some((term) => compact.includes(term));
      const matchMet = Boolean(value) && value === passwordConfirm.value;
      const checks = [lengthMet, lettersMet, numbersMet, specialMet, identityMet, matchMet];
      ["requireLength", "requireLetters", "requireNumbers", "requireSpecial", "requireIdentity", "requireMatch"].forEach((id, index) => {
        document.getElementById(id).classList.toggle("met", checks[index]);
      });
      const score = checks.filter(Boolean).length;
      const fill = document.getElementById("strengthFill");
      fill.style.width = `${Math.round(score / checks.length * 100)}%`;
      fill.style.background = score === checks.length ? "#65dfc8" : score >= 3 ? "#efbd70" : "#ff9494";
      document.getElementById("strengthLabel").textContent = score === checks.length ? "Strong" : score >= 3 ? "Needs work" : "Weak";
    }

    function generatedPassword() {
      const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%+-_";
      const unbiasedLimit = Math.floor(256 / alphabet.length) * alphabet.length;
      while (true) {
        let result = "";
        while (result.length < 15) {
          const bytes = new Uint8Array(32);
          window.crypto.getRandomValues(bytes);
          for (const value of bytes) {
            if (value >= unbiasedLimit) continue;
            result += alphabet[value % alphabet.length];
            if (result.length === 15) break;
          }
        }
        const hasTwoNumbers = (result.match(/[0-9]/g) || []).length >= 2;
        const excludesIdentity = !identityTerms().some((term) => result.toLowerCase().includes(term));
        if (/[A-Za-z]/.test(result) && /[^A-Za-z0-9\s]/.test(result) && hasTwoNumbers && excludesIdentity) return result;
      }
    }

    document.getElementById("generatePassword").addEventListener("click", () => {
      const value = generatedPassword();
      passwordInput.value = value;
      passwordConfirm.value = value;
      passwordInput.type = "text";
      passwordConfirm.type = "text";
      document.getElementById("togglePassword").textContent = "Hide";
      document.getElementById("togglePassword").setAttribute("aria-pressed", "true");
      updatePasswordStrength();
      passwordInput.focus();
    });
    document.getElementById("togglePassword").addEventListener("click", (event) => {
      const show = passwordInput.type === "password";
      passwordInput.type = show ? "text" : "password";
      passwordConfirm.type = show ? "text" : "password";
      event.currentTarget.textContent = show ? "Hide" : "Show";
      event.currentTarget.setAttribute("aria-pressed", show ? "true" : "false");
    });
    [passwordInput, passwordConfirm, document.getElementById("registerUsername"), document.getElementById("registerName")]
      .forEach((element) => element.addEventListener("input", updatePasswordStrength));

    const returnTargets = new URLSearchParams(window.location.search).getAll("next");
    const loginTarget = returnTargets.length === 1 && /^\/(?:write\?id=[0-9a-f]{32}|folio(?:\?id=[0-9a-f]{32})?)$/.test(returnTargets[0]) ? returnTargets[0] : "/";
    document.getElementById("loginForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.getElementById("loginMessage");
      const submit = document.getElementById("loginSubmit");
      message.textContent = "";
      submit.disabled = true;
      try {
        const response = await fetch("/api/login", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            username: document.getElementById("loginIdentity").value,
            password: document.getElementById("loginPassword").value,
            remember: document.getElementById("loginRemember").checked
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Sign-in failed.");
        window.sessionStorage.removeItem("pfempire.currentSession");
        window.location.replace(loginTarget);
      } catch (cause) {
        message.textContent = cause.message || "Sign-in failed.";
        submit.disabled = false;
      }
    });

    registerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.getElementById("registerMessage");
      const submit = document.getElementById("registerSubmit");
      message.textContent = "";
      submit.disabled = true;
      try {
        const response = await fetch("/api/register", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            username: document.getElementById("registerUsername").value,
            display_name: document.getElementById("registerName").value,
            password: document.getElementById("registerPassword").value,
            password_confirm: document.getElementById("registerPasswordConfirm").value,
            remember: document.getElementById("registerRemember").checked
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Registration failed.");
        window.sessionStorage.removeItem("pfempire.currentSession");
        window.location.replace(loginTarget);
      } catch (cause) {
        message.textContent = cause.message || "Registration failed.";
        submit.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OriginRAG</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #040b10;
      --sidebar: #061016;
      --panel: #09141c;
      --panel-2: #0d1b24;
      --panel-3: #122630;
      --line: #203744;
      --line-strong: #315565;
      --text: #eff8f7;
      --muted: #94aab1;
      --soft: #d6e4e5;
      --accent: #eff8f7;
      --accent-2: #8dbecf;
      --teal: #42d6c7;
      --teal-soft: rgba(66, 214, 199, .12);
      --amber: #efbd70;
      --danger: #ff7f7f;
      --code: #11212a;
      --shadow: 0 22px 70px rgba(0, 0, 0, .34);
      --sidebar-width: 296px;
    }
    * { box-sizing: border-box; }
    html { background: var(--bg); }
    body {
      position: relative;
      margin: 0;
      height: 100vh;
      height: 100dvh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      z-index: 0;
      pointer-events: none;
      background:
        radial-gradient(circle at 72% 10%, rgba(26, 104, 105, .12), transparent 34%),
        var(--bg);
    }
    .skip-link {
      position: fixed;
      top: 8px;
      left: 8px;
      z-index: 100;
      transform: translateY(-160%);
      border-radius: 8px;
      padding: 8px 12px;
      background: var(--teal);
      color: #04100f;
      font-weight: 800;
    }
    .skip-link:focus { transform: translateY(0); }
    :focus-visible {
      outline: 2px solid var(--teal);
      outline-offset: 3px;
    }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
    .app {
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
      height: 100%;
      min-height: 0;
      min-width: 0;
      overflow: hidden;
    }
    aside {
      position: relative;
      width: var(--sidebar-width);
      min-width: 0;
      min-height: 0;
      overflow: hidden;
      border-right: 1px solid var(--line);
      background: rgba(6, 16, 22, .94);
      backdrop-filter: blur(22px);
      padding: 16px 10px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .sidebar-resizer {
      position: absolute;
      z-index: 5;
      top: 0;
      right: -5px;
      width: 10px;
      height: 100%;
      cursor: col-resize;
      touch-action: none;
    }
    .sidebar-resizer:hover,
    body.sidebar-resizing .sidebar-resizer {
      background: linear-gradient(90deg, transparent 40%, var(--teal) 40%, var(--teal) 55%, transparent 55%);
    }
    body.sidebar-resizing { cursor: col-resize; user-select: none; }
    .sidebar-brand-actions { display: flex; align-items: center; gap: 6px; }
    .sidebar-close {
      width: 30px;
      min-height: 30px;
      padding: 0;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
      font-size: 18px;
    }
    .control-help {
      margin: 7px 2px 2px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.4;
    }
    .control-help strong { color: var(--soft); }
    main {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
      grid-template-areas:
        "header"
        "library-status"
        "chat"
        "composer";
      height: 100%;
      min-height: 0;
      min-width: 0;
      overflow: hidden;
      background: var(--bg);
      background-attachment: fixed;
    }
    h1 {
      font-size: 22px;
      margin: 0;
      letter-spacing: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 6px 8px 12px;
    }
    .sub {
      color: var(--muted);
      margin: 0;
      font-size: 13px;
    }
    .nav-button, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      min-height: 38px;
    }
    .nav-button:hover, button:hover { border-color: var(--line-strong); background: var(--panel-3); }
    .nav-button.primary {
      border-color: transparent;
      background: transparent;
      color: var(--text);
      justify-content: flex-start;
      text-align: left;
      padding: 0 10px;
    }
    .nav-button.primary:hover { background: #1b1b1b; border-color: #262626; }
    .session-list {
      display: grid;
      gap: 3px;
      max-height: min(58vh, 560px);
      overflow-y: auto;
      padding-right: 2px;
    }
    .sessions-section {
      display: grid;
      gap: 6px;
    }
    .section-toggle {
      width: 100%;
      min-height: 30px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 0 8px;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
      text-align: left;
    }
    .section-toggle:hover {
      background: #151515;
      border-color: #262626;
    }
    .section-toggle-icon {
      color: var(--muted);
      transition: transform .16s ease;
    }
    .sessions-section.collapsed .section-toggle-icon {
      transform: rotate(-90deg);
    }
    .sessions-section.collapsed .session-list {
      display: none;
    }
    .session-row {
      position: relative;
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr) 30px;
      align-items: center;
      gap: 2px;
      min-height: 34px;
      border: 1px solid transparent;
      border-radius: 9px;
    }
    .session-job-indicator {
      width: 12px;
      height: 12px;
      justify-self: end;
      border-radius: 999px;
    }
    .session-job-indicator.running,
    .session-job-indicator.cancelling,
    .session-job-indicator.queued {
      border: 2px solid rgba(66, 214, 199, .24);
      border-top-color: var(--teal);
      animation: spin .85s linear infinite;
    }
    .session-job-indicator.failed,
    .session-job-indicator.interrupted {
      background: var(--danger);
      box-shadow: 0 0 0 3px rgba(255, 127, 127, .12);
    }
    .session-row:hover,
    .session-row.current,
    .session-row.menu-open {
      background: #1b1b1b;
      border-color: #262626;
    }
    .session-link {
      color: var(--soft);
      border: 1px solid transparent;
      border-radius: 9px;
      padding: 6px 8px 6px 10px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-decoration: none;
      font-size: 14px;
      line-height: 1.3;
    }
    .session-menu-button {
      width: 28px;
      min-height: 28px;
      padding: 0;
      color: var(--muted);
      background: transparent;
      border-color: transparent;
      border-radius: 8px;
      font-size: 18px;
      line-height: 1;
      opacity: 0;
    }
    .session-row:hover .session-menu-button,
    .session-row.current .session-menu-button,
    .session-row.menu-open .session-menu-button,
    .session-row:focus-within .session-menu-button {
      opacity: 1;
    }
    .session-menu-button:hover {
      background: #2a2a2a;
      border-color: #343434;
      color: var(--text);
    }
    .session-menu {
      position: fixed;
      z-index: 100;
      display: none;
      min-width: 148px;
      padding: 6px;
      border: 1px solid #303030;
      border-radius: 9px;
      background: #1b1b1b;
      box-shadow: 0 14px 42px rgba(0, 0, 0, .55);
    }
    .session-menu.open {
      display: grid;
      gap: 2px;
    }
    .session-menu-item {
      width: 100%;
      min-height: 31px;
      border: 0;
      border-radius: 7px;
      padding: 0 9px;
      background: transparent;
      color: var(--soft);
      text-align: left;
      font-weight: 600;
      font-size: 13px;
    }
    .session-menu-item:hover {
      background: #2a2a2a;
    }
    .session-menu-item.danger {
      color: #ff9a9a;
    }
    .session-link:hover {
      text-decoration: none;
    }
    .session-row.current .session-link {
      color: var(--text);
    }
    .empty-sessions {
      color: var(--muted);
      font-size: 13px;
      padding: 8px 9px;
    }
    .source-list {
      width: 100%;
      color: var(--muted);
      margin-top: 2px;
    }
    .source-list strong {
      color: var(--accent-2);
    }
    .source-list ol {
      display: grid;
      gap: 8px;
      margin: 10px 0 0;
      padding: 0;
      list-style: none;
      counter-reset: source;
    }
    .source-list li {
      position: relative;
      margin: 0;
      padding: 12px 13px 12px 44px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(6, 16, 22, .72);
    }
    .source-list li::before {
      counter-increment: source;
      content: counter(source, decimal-leading-zero);
      position: absolute;
      left: 13px;
      top: 13px;
      color: var(--teal);
      font-size: 11px;
      font-weight: 850;
      letter-spacing: .06em;
    }
    .source-status {
      display: inline-flex;
      align-items: center;
      margin-right: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
    }
    .source-status.corpus, .source-status.library {
      color: var(--accent);
      border-color: #276f59;
    }
    .source-status.unresolved {
      color: var(--danger);
      border-color: #704040;
    }
    .source-title {
      color: var(--soft);
    }
    .source-match {
      display: block;
      margin-top: 5px;
      color: var(--teal);
      font-size: 11px;
    }
    .source-type {
      display: block;
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 11px;
    }
    .source-identifiers {
      display: block;
      margin-top: 2px;
      font-size: 12px;
      color: var(--muted);
    }
    .source-actions { margin: 6px 0 0; }
    .source-action {
      width: auto;
      min-height: 30px;
      padding: 5px 9px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--teal);
      font-size: 12px;
      cursor: pointer;
    }
    .paper-table-wrap {
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(7, 17, 24, .92);
    }
    .paper-table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      font-size: 12px;
    }
    .paper-table th,
    .paper-table td {
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    .paper-table tr:last-child td { border-bottom: 0; }
    .paper-table th {
      color: var(--muted);
      font-size: 10px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .paper-table-rank { color: var(--teal); font-weight: 850; }
    .paper-table-paper { min-width: 280px; }
    .paper-table-paper a { color: var(--soft); font-weight: 750; }
    .paper-table-meta { display: block; margin-top: 4px; color: var(--muted); }
    .paper-table-reason { min-width: 220px; color: var(--soft); }
    .paper-table-details { min-width: 340px; }
    .paper-detail-grid { display: grid; gap: 5px; }
    .paper-detail-row {
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 8px;
      line-height: 1.35;
    }
    .paper-detail-label { color: var(--muted); font-size: 10px; font-weight: 800; text-transform: uppercase; }
    .paper-detail-value { color: var(--soft); }
    .paper-detail-value.not-reported { color: var(--muted); font-style: italic; }
    .paper-detail-note { display: block; margin-top: 7px; color: var(--muted); font-size: 10px; }
    .citation-trail {
      margin: 8px 0 12px;
      padding: 9px 11px;
      border-left: 2px solid #35516b;
      background: #0c1118;
      color: var(--muted);
      font-size: 12px;
    }
    .citation-trail ol { margin: 7px 0 0; padding-left: 20px; }
    .citation-context { margin-top: 4px; color: #9aaaba; }
    label {
      display: block;
      font-weight: 650;
      margin: 12px 0 6px;
      color: var(--soft);
      font-size: 13px;
    }
    select, input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0d0d0d;
      color: var(--text);
      font: inherit;
    }
    select, input {
      height: 36px;
      padding: 0 10px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }
    .check input {
      width: 16px;
      height: 16px;
    }
    .advanced {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(9, 20, 28, .88);
      padding: 10px;
    }
    .advanced summary {
      cursor: pointer;
      color: var(--soft);
      font-weight: 750;
    }
    header {
      grid-area: header;
      position: relative;
      z-index: 80;
      padding: 16px 26px;
      border-bottom: 1px solid var(--line);
      background: rgba(4, 11, 16, .72);
      backdrop-filter: blur(16px);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }
    .header-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .header-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      min-width: 0;
      align-items: center;
      gap: 10px;
    }
    .privacy-badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
    }
    .connection-indicator {
      display: inline-flex;
      align-items: center;
    }
    .connection-dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--danger);
      box-shadow: 0 0 0 4px rgba(255, 122, 122, .12);
    }
    .connection-indicator.online .connection-dot {
      background: var(--teal);
      box-shadow: 0 0 0 4px var(--teal-soft);
    }
    .queue-summary {
      display: inline-flex;
      align-items: center;
      width: auto;
      min-height: 28px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 9px;
      color: var(--muted);
      background: rgba(9, 20, 28, .78);
      font-size: 11px;
      font-weight: 750;
      white-space: nowrap;
      cursor: pointer;
    }
    .queue-summary.busy { color: var(--teal); border-color: rgba(66, 214, 199, .34); }
    .mobile-sidebar-toggle {
      display: none;
      width: 36px;
      min-height: 34px;
      padding: 0;
      font-size: 18px;
    }
    #status {
      color: var(--muted);
      font-size: 13px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 24px;
    }
    #status:empty { display: none; }
    #status.working::before,
    .working-dot {
      content: "";
      width: 13px;
      height: 13px;
      border: 2px solid rgba(141, 179, 255, .25);
      border-top-color: var(--accent-2);
      border-radius: 999px;
      animation: spin .85s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .header-button {
      width: auto;
      min-height: 34px;
      padding: 0 12px;
      border-radius: 8px;
      background: rgba(20, 20, 20, .82);
      color: var(--soft);
      font-weight: 750;
    }
    .about-button {
      width: 34px;
      padding: 0;
      border-radius: 999px;
      font-size: 15px;
    }
    .library-stats {
      position: relative;
      flex: 0 0 auto;
    }
    .library-stats-button {
      width: auto;
      min-height: 34px;
      padding: 0 10px;
      border: 1px solid rgba(70, 207, 193, .26);
      border-radius: 999px;
      background: rgba(10, 31, 37, .88);
      color: var(--soft);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 720;
      white-space: nowrap;
    }
    .library-stats-button:hover,
    .library-stats-button[aria-expanded="true"] {
      border-color: rgba(70, 207, 193, .58);
      background: rgba(14, 43, 49, .96);
    }
    .library-stats-dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--teal);
      box-shadow: 0 0 0 4px var(--teal-soft);
    }
    .library-stats-button strong {
      color: var(--text);
      font-variant-numeric: tabular-nums;
    }
    .library-stats-popover {
      position: absolute;
      z-index: 100;
      top: calc(100% + 10px);
      right: 0;
      width: min(390px, calc(100vw - 24px));
      padding: 13px;
      border: 1px solid var(--line-strong);
      border-radius: 14px;
      background: rgba(5, 17, 23, .98);
      box-shadow: 0 20px 55px rgba(0, 0, 0, .48);
    }
    .library-stats-popover[hidden] { display: none; }
    .library-stats-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      padding: 1px 2px 10px;
    }
    .library-stats-head strong {
      color: var(--text);
      font-size: 13px;
    }
    .library-stats-head span,
    .library-stats-coverage {
      color: var(--muted);
      font-size: 11px;
    }
    .library-stats-visual {
      display: grid;
      grid-template-columns: 112px minmax(0, 1fr);
      gap: 14px;
      align-items: center;
    }
    .library-stats-donut {
      width: 108px;
      height: 108px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: conic-gradient(var(--line-strong) 0 100%);
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .05);
    }
    .library-stats-donut-center {
      width: 67px;
      height: 67px;
      border-radius: 50%;
      display: grid;
      place-content: center;
      text-align: center;
      background: rgb(5, 17, 23);
      box-shadow: 0 0 0 1px rgba(255, 255, 255, .05);
    }
    .library-stats-donut-center strong {
      color: var(--text);
      font-size: 16px;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }
    .library-stats-donut-center span {
      margin-top: 3px;
      color: var(--muted);
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .library-stats-groups {
      display: grid;
      gap: 5px;
    }
    .library-stats-group {
      display: grid;
      grid-template-columns: 8px minmax(0, 1fr) auto;
      gap: 7px;
      align-items: center;
      color: var(--muted);
      font-size: 10.5px;
    }
    .library-stats-swatch {
      width: 8px;
      height: 8px;
      border-radius: 3px;
    }
    .library-stats-group span:nth-child(2) {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .library-stats-group strong {
      color: var(--soft);
      font-variant-numeric: tabular-nums;
    }
    .library-stats-coverage {
      margin-top: 10px;
      padding: 8px 2px 1px;
      border-top: 1px solid var(--line);
      text-align: center;
    }
    .library-sync-status {
      grid-area: library-status;
      display: none;
      align-items: center;
      gap: 10px;
      padding: 7px 26px;
      border-bottom: 1px solid var(--line);
      background: rgba(9, 20, 28, .94);
      color: var(--muted);
      font-size: 12px;
    }
    .library-sync-status.visible { display: flex; }
    .library-sync-status strong { color: var(--soft); }
    .library-sync-dot {
      width: 9px;
      height: 9px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: var(--amber);
      box-shadow: 0 0 0 4px rgba(239, 189, 112, .11);
    }
    .library-sync-status.complete .library-sync-dot {
      background: var(--teal);
      box-shadow: 0 0 0 4px var(--teal-soft);
    }
    .library-sync-status.error .library-sync-dot {
      background: var(--danger);
      box-shadow: 0 0 0 4px rgba(255, 122, 122, .12);
    }
    #chat {
      grid-area: chat;
      overflow-y: auto;
      min-height: 0;
      min-width: 0;
      padding: 28px 24px;
    }
    .hero {
      min-height: 100%;
      display: grid;
      place-items: center;
      text-align: left;
      color: var(--soft);
      min-width: 0;
    }
    .hero-shell {
      width: min(860px, 100%);
      min-width: 0;
      padding: 42px 18px;
    }
    .hero-signals {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 20px;
    }
    .hero-signal-label {
      color: var(--muted);
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .hero-signal {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: rgba(9, 20, 28, .68);
      color: var(--muted);
      font-size: 10px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .hero-signal:first-child {
      color: var(--teal);
      border-color: rgba(66, 214, 199, .34);
      background: var(--teal-soft);
    }
    .hero h2 {
      max-width: none;
      font-size: clamp(36px, 4.2vw, 56px);
      font-weight: 680;
      letter-spacing: -.045em;
      line-height: 1.02;
      margin: 0 0 18px;
      color: var(--text);
      white-space: nowrap;
    }
    .hero h2 span { color: var(--teal); }
    .hero-footnote {
      margin-top: 13px;
      color: var(--muted);
      font-size: 11px;
    }
    .msg {
      max-width: 860px;
      margin: 0 auto 22px;
      display: grid;
      gap: 8px;
    }
    .role {
      font-size: 12px;
      color: var(--muted);
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .bubble {
      background: rgba(9, 20, 28, .93);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: var(--shadow);
      white-space: pre-wrap;
    }
    .user .bubble {
      margin-left: auto;
      max-width: 760px;
      background: rgba(18, 38, 48, .94);
      border-color: var(--line-strong);
    }
    .assistant.working .bubble {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
    }
    .assistant.job-failed .bubble {
      color: #ffd6d6;
      border-color: #704040;
    }
    .working-stage {
      display: grid;
      gap: 6px;
      min-width: min(360px, 60vw);
    }
    .working-progress {
      width: 100%;
      height: 4px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(112, 151, 169, .18);
    }
    .working-progress-bar {
      width: 6%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--teal), var(--accent));
      transition: width .35s ease;
    }
    .markdown {
      white-space: normal;
    }
    .markdown h2 {
      font-size: 20px;
      margin: 4px 0 12px;
      letter-spacing: 0;
    }
    .markdown h3 {
      font-size: 15px;
      margin: 16px 0 8px;
      color: #dbe7f5;
      letter-spacing: 0;
    }
    .markdown p {
      margin: 0 0 10px;
    }
    .markdown ul {
      margin: 0 0 12px;
      padding-left: 20px;
    }
    .markdown li {
      margin: 5px 0;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .result-meta {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }
    .result-summary {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 10px;
      color: var(--muted);
      background: #0c1118;
      font-size: 12px;
    }
    .pill.ok { color: var(--accent); border-color: #276f59; }
    .pill.bad { color: var(--danger); border-color: #704040; }
    .pill.warn { color: var(--amber); border-color: #70552f; }
    .inline-citation {
      display: inline;
      color: var(--accent-2);
      font-size: inherit;
      font-weight: 650;
      text-decoration: underline;
      text-decoration-color: rgba(110, 207, 227, .45);
      text-underline-offset: 2px;
    }
    .inline-citation:hover { color: var(--teal); }
    .source-legend {
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      color: var(--muted);
      background: rgba(8, 18, 25, .72);
      font-size: 11px;
      line-height: 1.45;
    }
    .source-legend strong { color: var(--text); }
    .assurance-panel {
      width: 100%;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: linear-gradient(145deg, rgba(10, 27, 34, .96), rgba(7, 17, 24, .96));
    }
    .assurance-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 15px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }
    .assurance-panel:not([open]) .assurance-head { border-bottom: 0; }
    .assurance-head::marker { color: var(--accent-2); }
    .assurance-title {
      display: grid;
      gap: 3px;
    }
    .assurance-title strong { color: var(--text); }
    .assurance-title span { color: var(--muted); font-size: 12px; }
    .assurance-status {
      border-radius: 999px;
      padding: 4px 9px;
      white-space: nowrap;
      font-size: 11px;
      font-weight: 800;
    }
    .assurance-status.ok { color: var(--teal); background: var(--teal-soft); }
    .assurance-status.warn { color: var(--amber); background: rgba(239, 189, 112, .11); }
    .assurance-status.bad { color: var(--danger); background: rgba(255, 127, 127, .10); }
    .assurance-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .assurance-flow {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr);
      align-items: stretch;
    }
    .assurance-flow-step {
      display: grid;
      align-content: center;
      gap: 3px;
      min-width: 0;
      padding: 14px 15px;
      text-align: center;
    }
    .assurance-flow-step span {
      color: var(--muted);
      font-size: 10px;
      font-weight: 780;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .assurance-flow-step strong { color: var(--text); font-size: 20px; }
    .assurance-flow-arrow { align-self: center; color: var(--amber); font-size: 20px; }
    .assurance-metric {
      min-width: 0;
      padding: 13px 15px;
      border-right: 1px solid var(--line);
    }
    .assurance-metric:last-child { border-right: 0; }
    .assurance-metric span {
      display: block;
      color: var(--muted);
      font-size: 10px;
      font-weight: 780;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .assurance-metric strong {
      display: block;
      margin-top: 4px;
      overflow: hidden;
      color: var(--text);
      font-size: 17px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .assurance-note {
      padding: 10px 15px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
    }
    .assurance-note strong { color: var(--amber); }
    .study-audit-action {
      display: flex;
      justify-content: flex-start;
    }
    .study-audit-button {
      width: auto;
      min-height: 38px;
      padding: 0 14px;
      border-color: rgba(100, 220, 196, .42);
      color: var(--teal);
      background: rgba(55, 193, 166, .08);
      font-weight: 780;
    }
    .study-audit-button:hover { background: rgba(55, 193, 166, .15); }
    .study-audit-backdrop {
      position: fixed;
      inset: 0;
      z-index: 35;
      display: none;
      place-items: center;
      padding: 20px;
      background: rgba(0, 0, 0, .64);
      backdrop-filter: blur(8px);
    }
    .study-audit-backdrop.open { display: grid; }
    .study-audit-panel {
      width: min(760px, 100%);
      max-height: min(760px, calc(100vh - 40px));
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #07131a;
      box-shadow: 0 24px 80px rgba(0, 0, 0, .62);
      padding: 20px;
    }
    .study-audit-panel h2 { margin: 0 0 5px; }
    .study-audit-intro { margin: 0 0 16px; color: var(--muted); }
    .study-audit-result {
      display: grid;
      gap: 12px;
    }
    .study-audit-result .assurance-flow {
      border: 1px solid var(--line);
      border-radius: 12px;
    }
    .study-audit-section {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 13px 14px;
      background: rgba(8, 23, 30, .9);
    }
    .study-audit-section h3 { margin: 0 0 7px; font-size: 14px; }
    .study-audit-section p { margin: 0; color: var(--muted); font-size: 13px; }
    .study-audit-list { margin: 7px 0 0; padding-left: 20px; color: var(--text); }
    .study-audit-list li + li { margin-top: 6px; }
    .study-audit-close-row { display: flex; justify-content: flex-end; margin-top: 16px; }
    .study-audit-close-row button { width: auto; padding: 0 16px; }
    .claim-trace-panel {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(7, 17, 24, .94);
      overflow: hidden;
    }
    .claim-trace-panel > summary {
      cursor: pointer;
      padding: 12px 14px;
      color: var(--accent-2);
      font-weight: 780;
    }
    .claim-trace-list {
      display: grid;
      gap: 12px;
      padding: 0 14px 14px;
    }
    .claim-trace-item {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .claim-trace-text { margin: 0 0 9px; color: var(--soft); font-size: 13px; }
    .source-chain {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(130px, .65fr);
      align-items: stretch;
      gap: 7px;
    }
    .source-chain-node {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 9px 10px;
      background: rgba(13, 27, 36, .88);
    }
    .source-chain-node strong,
    .source-chain-node span { display: block; }
    .source-chain-node strong { overflow-wrap: anywhere; font-size: 12px; }
    .source-chain-node span { margin-top: 4px; color: var(--muted); font-size: 10px; }
    .source-chain-arrow { align-self: center; color: var(--teal); font-weight: 800; }
    .source-chain-citations { display: grid; gap: 6px; }
    .source-chain-node.unresolved { border-color: rgba(239, 189, 112, .42); }
    .claim-trace-policy { margin: 8px 0 0; color: var(--muted); font-size: 10px; }
    details.sources-box {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(7, 17, 24, .92);
      padding: 12px 14px;
      margin-top: 0;
    }
    details.sources-box summary {
      cursor: pointer;
      font-weight: 750;
      color: var(--accent-2);
    }
    .sources {
      margin: 8px 0 0;
      padding-left: 18px;
      color: var(--muted);
    }
    .composer {
      grid-area: composer;
      position: relative;
      z-index: 3;
      background: linear-gradient(180deg, rgba(4, 11, 16, 0), var(--bg) 24%);
      padding: 24px 22px 18px;
    }
    .composer-context {
      max-width: 860px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin: 0 auto 7px;
      padding: 0 12px;
      color: var(--muted);
      font-size: 11px;
    }
    .composer-inner {
      max-width: 860px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 52px;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: rgba(13, 27, 36, .96);
      padding: 8px;
      box-shadow: var(--shadow);
    }
    #composerStop, .job-stop { width: auto; min-height: 30px; padding: 5px 12px; border-radius: 9px; font-size: 12px; white-space: nowrap; }
    .job-stop { margin: 10px 0 0 12px; align-self: center; }
    .composer-context { align-items: center; }
    @media (max-width: 700px) {
      .assistant.working .bubble { flex-wrap: wrap; }
      .working-stage { min-width: 0; flex: 1 1 140px; }
      .job-stop { margin-left: auto; }
    }
    textarea {
      min-height: 44px;
      max-height: 170px;
      resize: none;
      padding: 11px 14px;
      border: 0;
      background: transparent;
      outline: 0;
    }
    #send {
      grid-column: 2;
      grid-row: 1;
      border-radius: 999px;
      min-height: 44px;
      background: var(--text);
      color: #07090d;
      border: 0;
      font-size: 18px;
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    a {
      color: var(--accent-2);
      text-decoration: none;
    }
    a:hover { text-decoration: underline; }
    code {
      background: var(--code);
      padding: 1px 5px;
      border-radius: 4px;
      color: #d9e4f1;
    }
    .profile-backdrop,
    .about-backdrop,
    .queue-backdrop,
    .library-browser-backdrop,
    .paper-upload-backdrop {
      position: fixed;
      inset: 0;
      z-index: 30;
      display: none;
      place-items: center;
      padding: 20px;
      background: rgba(0, 0, 0, .58);
      backdrop-filter: blur(8px);
    }
    .profile-backdrop.open,
    .about-backdrop.open,
    .queue-backdrop.open,
    .library-browser-backdrop.open,
    .paper-upload-backdrop.open { display: grid; }
    .profile-panel,
    .about-panel,
    .queue-panel,
    .library-browser-panel,
    .paper-upload-panel {
      width: min(620px, 100%);
      border: 1px solid #303846;
      border-radius: 12px;
      background: rgba(12, 12, 12, .96);
      box-shadow: 0 24px 80px rgba(0, 0, 0, .58);
      padding: 18px;
    }
    .profile-panel h2 {
      margin: 0 0 8px;
      letter-spacing: 0;
    }
    .about-panel { width: min(560px, 100%); }
    .about-panel h2 { margin: 0 0 12px; letter-spacing: 0; }
    .about-panel p { margin: 0 0 10px; color: var(--soft); }
    .about-panel p:last-of-type { margin-bottom: 18px; }
    .about-actions { display: flex; justify-content: flex-end; }
    .about-actions button { width: auto; padding: 0 16px; }
    .queue-panel { width: min(720px, 100%); }
    .queue-panel h2 { margin: 0 0 5px; }
    .queue-panel > p { margin: 0 0 14px; color: var(--muted); }
    .queue-detail-list {
      max-height: min(560px, 65vh);
      overflow-y: auto;
      display: grid;
      gap: 8px;
    }
    .queue-detail-row {
      display: grid;
      gap: 5px;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: rgba(4, 11, 16, .72);
    }
    .queue-detail-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 11px;
    }
    .queue-detail-owner { color: var(--teal); font-weight: 780; }
    .queue-detail-empty { padding: 24px 10px; color: var(--muted); text-align: center; }
    .queue-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
    .queue-actions button { width: auto; padding: 0 16px; }
    .profile-panel p {
      margin: 0 0 14px;
      color: var(--muted);
    }
    #profileText {
      min-height: 150px;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #0a0a0a;
      padding: 12px;
    }
    .profile-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 14px;
    }
    .profile-actions button {
      width: auto;
      padding: 0 14px;
    }
    .paper-upload-panel { width: min(680px, 100%); }
    .paper-upload-panel h2 { margin: 0 0 6px; }
    .paper-upload-panel > p { margin: 0 0 14px; color: var(--muted); }
    .paper-drop-zone {
      min-height: 190px;
      display: grid;
      place-items: center;
      align-content: center;
      gap: 8px;
      padding: 24px;
      border: 2px dashed var(--line-strong);
      border-radius: 14px;
      background: rgba(66, 214, 199, .04);
      text-align: center;
      cursor: pointer;
    }
    .paper-drop-zone.dragging {
      border-color: var(--teal);
      background: var(--teal-soft);
    }
    .paper-drop-zone strong { font-size: 17px; }
    .paper-drop-zone span { color: var(--muted); }
    .paper-upload-doi { display: grid; gap: 5px; margin-top: 12px; }
    .paper-upload-doi label { color: var(--soft); font-weight: 700; }
    .paper-upload-doi input {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #071117;
      color: var(--text);
      padding: 0 11px;
    }
    .paper-upload-list { display: grid; gap: 6px; margin-top: 12px; }
    .paper-upload-item {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
    }
    .paper-upload-actions { display: flex; justify-content: flex-end; margin-top: 14px; }
    .paper-upload-actions button { width: auto; padding: 0 16px; }
    .library-browser-panel {
      width: min(920px, 100%);
      height: min(780px, calc(100vh - 40px));
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
      gap: 12px;
    }
    .library-browser-heading {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }
    .library-browser-heading h2 { margin: 0 0 4px; }
    .library-browser-heading p { margin: 0; color: var(--muted); font-size: 13px; }
    .library-browser-heading button,
    .library-browser-footer button { width: auto; padding: 0 15px; }
    .library-search {
      min-height: 44px;
      border-color: var(--line-strong);
      background: #071117;
      padding: 0 13px;
    }
    .library-scope {
      min-height: 38px;
      border-color: var(--line-strong);
      background: #071117;
      color: var(--soft);
      padding: 0 11px;
      font: inherit;
    }
    .library-results {
      min-height: 0;
      overflow-y: auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(4, 11, 16, .62);
    }
    .library-paper {
      display: grid;
      gap: 5px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .library-paper:last-child { border-bottom: 0; }
    .library-paper-title { color: var(--text); font-weight: 780; line-height: 1.35; }
    .library-paper-meta,
    .library-paper-status { color: var(--muted); font-size: 12px; }
    .library-paper-category {
      width: fit-content;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 720;
    }
    .library-paper-links { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; }
    .library-paper-links a { color: var(--accent-2); font-size: 12px; font-weight: 720; }
    .library-local { color: var(--teal); }
    .library-unavailable { color: var(--muted); }
    .library-empty { padding: 28px 16px; color: var(--muted); text-align: center; }
    .library-browser-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
    }
    .sidebar-backdrop { display: none; }
    @media (min-width: 821px) {
      .app { transition: grid-template-columns .16s ease; }
      body.sidebar-collapsed .app { grid-template-columns: 0 minmax(0, 1fr); }
      body.sidebar-collapsed aside { display: none; }
      body.sidebar-collapsed main { grid-column: 1 / -1; }
      body.sidebar-collapsed .mobile-sidebar-toggle { display: inline-grid; place-items: center; }
    }
    @media (max-width: 820px) {
      .app { width: 100%; max-width: 100vw; grid-template-columns: minmax(0, 1fr); overflow-x: hidden; }
      aside {
        position: fixed;
        inset: 0 auto 0 0;
        z-index: 40;
        width: min(300px, 86vw);
        transform: translateX(-102%);
        transition: transform .2s ease;
        box-shadow: 20px 0 70px rgba(0, 0, 0, .45);
      }
      .sidebar-resizer { display: none; }
      body.sidebar-open aside { transform: translateX(0); }
      .sidebar-backdrop {
        position: fixed;
        inset: 0;
        z-index: 35;
        background: rgba(0, 0, 0, .58);
      }
      body.sidebar-open .sidebar-backdrop { display: block; }
      .mobile-sidebar-toggle { display: inline-grid; place-items: center; }
      header { padding: 14px 16px; }
      #chat { padding: 20px 14px; }
      main { width: 100%; max-width: 100vw; overflow-x: hidden; background-attachment: scroll; }
      .hero { min-height: 100%; }
      .hero-shell {
        width: calc(100vw - 28px);
        max-width: calc(100vw - 28px);
        padding: 28px 0;
      }
      .hero h2 { font-size: clamp(38px, 11vw, 54px); }
      .hero h2 { white-space: normal; }
      .header-actions { gap: 6px; }
      .header-button { padding: 0 9px; }
      .library-stats-word { display: none; }
      .composer { padding: 18px 12px 12px; }
      .composer-inner { grid-template-columns: minmax(0, 1fr) 46px; border-radius: 22px; }
      .assurance-grid { grid-template-columns: 1fr 1fr; }
      .assurance-metric:nth-child(2) { border-right: 0; }
      .assurance-metric:nth-child(-n + 2) { border-bottom: 1px solid var(--line); }
      .source-chain { grid-template-columns: minmax(0, 1fr); }
      .source-chain-arrow { transform: rotate(90deg); justify-self: center; }
    }
    @media (max-width: 520px) {
      .hero-signals { gap: 5px; }
      .hero h2 {
        width: 100%;
        max-width: calc(100vw - 28px);
        overflow-wrap: anywhere;
        font-size: clamp(34px, 10.5vw, 43px);
      }
      .hero p { font-size: 14px; }
      .assurance-head { display: grid; }
      .privacy-badge { display: none; }
      #status { display: none; }
      header { padding-inline: 12px; }
      .library-stats-popover {
        position: fixed;
        top: 66px;
        right: 12px;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        scroll-behavior: auto !important;
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: .01ms !important;
      }
    }
  </style>
</head>
<body>
  <a class="skip-link" href="#message">Skip to research question</a>
  <div class="app">
    <aside id="sidebar" aria-label="Chat history and research controls">
      <div class="brand">
        <div>
          <h1>OriginRAG</h1>
        </div>
        <div class="sidebar-brand-actions">
          <button class="sidebar-close" id="sidebarClose" type="button" title="Hide sidebar" aria-label="Hide sidebar">×</button>
        </div>
      </div>
      <button class="nav-button primary" id="newChat" type="button">New chat</button>
      <div class="sessions-section" id="sessionsSection">
        <button class="section-toggle" id="sessionsToggle" type="button" aria-expanded="true" aria-controls="sessions">
          <span>Recent chats</span>
          <span class="section-toggle-icon">⌄</span>
        </button>
        <div class="session-list" id="sessions">
          <div class="empty-sessions">No saved chats yet.</div>
        </div>
      </div>
      <input id="backend" type="hidden" value="qwen" />
      <input id="preset" type="hidden" value="pro" />
      <input id="evidenceSelector" type="hidden" value="trace_flow" />
      <input id="repair" type="hidden" value="1" />
      <input id="category" type="hidden" value="" />
      <input id="fallback" type="checkbox" hidden checked />
      <div class="sidebar-resizer" id="sidebarResizer" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" tabindex="0"></div>
    </aside>
    <div class="sidebar-backdrop" id="sidebarBackdrop"></div>
    <main>
      <header>
        <div class="header-left">
          <a class="header-button" href="/workspace" style="text-decoration:none;display:inline-flex;align-items:center">Workspace</a>
          <button class="mobile-sidebar-toggle" id="sidebarToggle" type="button" title="Show sidebar" aria-label="Show sidebar" aria-controls="sidebar" aria-expanded="false">☰</button>
        </div>
        <div class="header-actions">
          <button class="header-button" id="chatModels" type="button" aria-haspopup="dialog" aria-controls="modelDialog" title="Installed models, downloads and active model">Models</button>
          <button class="header-button" id="appearanceButton" type="button" aria-haspopup="dialog" aria-controls="appearanceDialog" aria-expanded="false">Appearance</button>
          <button class="header-button about-button" id="aboutButton" type="button" title="About OriginRAG" aria-label="About OriginRAG" aria-controls="aboutBackdrop" aria-expanded="false">?</button>
          <span class="connection-indicator" id="connectionIndicator" title="Offline" aria-label="Offline"><span class="connection-dot"></span></span>
          <button class="queue-summary" id="queueSummary" type="button" aria-live="polite" aria-expanded="false" aria-controls="queueBackdrop">Queue idle</button>
          <div class="library-stats" id="libraryStats">
            <button class="library-stats-button" id="libraryStatsButton" type="button" aria-expanded="false" aria-controls="libraryStatsPopover" title="Library composition">
              <span class="library-stats-dot" aria-hidden="true"></span>
              <strong id="libraryStatsTotal">—</strong>
              <span class="library-stats-word">papers</span>
            </button>
            <div class="library-stats-popover" id="libraryStatsPopover" role="dialog" aria-label="Library composition" hidden>
              <div class="library-stats-head">
                <strong>Library composition</strong>
                <span>live catalogue</span>
              </div>
              <div class="library-stats-visual">
                <div class="library-stats-donut" id="libraryStatsDonut" role="img" aria-label="Paper distribution by topic">
                  <div class="library-stats-donut-center">
                    <strong id="libraryStatsDonutTotal">—</strong>
                    <span>papers</span>
                  </div>
                </div>
                <div class="library-stats-groups" id="libraryStatsGroups"></div>
              </div>
              <div class="library-stats-coverage" id="libraryStatsCoverage">Loading composition…</div>
            </div>
          </div>
          <span class="privacy-badge" id="userBadge" hidden></span>
          <button class="header-button" id="libraryButton" type="button" hidden>Library</button>
          <button class="header-button" id="paperUploadButton" type="button" hidden>Add papers</button>
          <button class="header-button" id="profileButton" type="button" hidden>Answer style</button>
          <button class="header-button" id="logoutButton" type="button" hidden>Sign out</button>
          <div id="status" role="status" aria-live="polite"></div>
        </div>
      </header>
      <div class="library-sync-status" id="librarySyncStatus" role="status" aria-live="polite">
        <span class="library-sync-dot" aria-hidden="true"></span>
        <strong id="librarySyncLabel">Updating library</strong>
        <span id="librarySyncCounts"></span>
      </div>
      <section id="chat" role="log" aria-live="polite" aria-relevant="additions" aria-label="Research conversation">
        <div class="hero" id="hero">
          <div class="hero-shell">
            <h2>Evidence, with <span>its origin intact.</span></h2>
          </div>
        </div>
      </section>
      <form class="composer" id="form">
        <div class="composer-context">
          <span>Enter to send · Shift+Enter for a new line</span>
          <button id="composerStop" type="button" hidden aria-label="Stop current request">■ Stop</button>
        </div>
        <div class="composer-inner">
          <label class="sr-only" for="message">Research question</label>
          <textarea id="message" placeholder="Ask a question about your corpus..." required></textarea>
          <button id="send" type="submit" title="Send" aria-label="Send research question">↑</button>
        </div>
      </form>
    </main>
  </div>
  <div class="about-backdrop" id="aboutBackdrop" aria-hidden="true">
    <div class="about-panel" role="dialog" aria-modal="true" aria-labelledby="aboutTitle">
      <h2 id="aboutTitle">What does this tool do?</h2>
      <p>It searches your local scientific literature and generates source-grounded answers.</p>
      <p>Multiple papers may use the same study or dataset, so they are not automatically counted as independent evidence.</p>
      <p><strong>Independent studies: ?</strong> means that study overlap has not yet been sufficiently reviewed.</p>
      <p>Always check the cited paper before using extracted details in a publication.</p>
      <div class="about-actions"><button id="aboutClose" class="dialog-close" type="button" aria-label="Close about" title="Close about">×</button></div>
    </div>
  </div>
  <div class="profile-backdrop" id="profileBackdrop" aria-hidden="true">
    <div class="profile-panel" role="dialog" aria-modal="true" aria-labelledby="profileTitle">
      <h2 id="profileTitle">Response profile</h2>
      <p>Optional: choose Qwen's tone, length, or level of detail. Evidence, citation, and safety rules are unchanged.</p>
      <textarea id="profileText" placeholder="Example: Keep answers concise, use simple language, focus on mechanisms and concrete next analyses."></textarea>
      <div class="profile-actions">
        <button id="profileClose" class="dialog-close" type="button" aria-label="Close response profile" title="Close response profile">×</button>
        <button id="profileSave" type="button">Save profile</button>
      </div>
    </div>
  </div>
  <div class="paper-upload-backdrop" id="paperUploadBackdrop" aria-hidden="true">
    <div class="paper-upload-panel" role="dialog" aria-modal="true" aria-labelledby="paperUploadTitle">
      <h2 id="paperUploadTitle">Add papers</h2>
      <p>Drop searchable article PDFs here. OriginRAG checks identifiers and duplicates, names and sorts accepted papers, then refreshes the shared index.</p>
      <input id="paperFileInput" type="file" accept="application/pdf,.pdf" multiple hidden />
      <div class="paper-drop-zone" id="paperDropZone" role="button" tabindex="0">
        <strong>Drop PDF files here</strong>
        <span>or click to choose files · maximum 100 MB each</span>
      </div>
      <div class="paper-upload-doi">
        <label for="paperDoi">DOI for a single PDF (optional)</label>
        <input id="paperDoi" type="text" placeholder="10.xxxx/xxxxx" autocomplete="off" />
      </div>
      <div class="paper-upload-list" id="paperUploadList" aria-live="polite"></div>
      <button id="paperUploadClose" class="dialog-close" type="button" aria-label="Close paper upload" title="Close paper upload">×</button>
    </div>
  </div>
  <div class="library-browser-backdrop" id="libraryBrowserBackdrop" aria-hidden="true">
    <div class="library-browser-panel" role="dialog" aria-modal="true" aria-labelledby="libraryBrowserTitle">
      <div class="library-browser-heading">
        <div>
          <h2 id="libraryBrowserTitle">Library</h2>
        </div>
        <button id="libraryBrowserClose" class="dialog-close" type="button" aria-label="Close library" title="Close library">×</button>
      </div>
      <label class="sr-only" for="librarySearch">Search the bibliography</label>
      <input class="library-search" id="librarySearch" type="search" placeholder="Search author, title, journal, year, or DOI…" autocomplete="off" />
      <label class="sr-only" for="libraryScope">Filter library by subject</label>
      <select class="library-scope" id="libraryScope">
        <option value="">All papers</option>
        <option value="p_falciparum">Plasmodium falciparum</option>
      </select>
      <div class="library-results" id="libraryResults" tabindex="0" aria-live="polite"></div>
      <div class="library-browser-footer">
        <span id="libraryResultCount">Loading library…</span>
        <button id="libraryLoadMore" type="button" hidden>Load more</button>
      </div>
    </div>
  </div>
  <div class="queue-backdrop" id="queueBackdrop" aria-hidden="true">
    <div class="queue-panel" role="dialog" aria-modal="true" aria-labelledby="queueTitle">
      <h2 id="queueTitle">Active queue</h2>
      <p id="queueScopeNote">Loading active jobs…</p>
      <div class="queue-detail-list" id="queueDetailList" aria-live="polite"></div>
      <div class="queue-actions">
        <button id="queueRefresh" type="button">Refresh</button>
        <button id="queueClose" class="dialog-close" type="button" aria-label="Close queue" title="Close queue">×</button>
      </div>
    </div>
  </div>
  <div class="study-audit-backdrop" id="studyAuditBackdrop" aria-hidden="true">
    <div class="study-audit-panel" role="dialog" aria-modal="true" aria-labelledby="studyAuditTitle">
      <h2 id="studyAuditTitle">Study overlap details</h2>
      <p class="study-audit-intro">Shows the automatic study-overlap check for this result.</p>
      <div class="study-audit-result" id="studyAuditResult"></div>
      <button id="studyAuditClose" class="dialog-close" type="button" aria-label="Close study overlap details" title="Close study overlap details">×</button>
    </div>
  </div>
  <script>
    const browserFetch = window.fetch.bind(window);
    let localImportToken = "";
    function cookieValue(name) {
      const prefix = `${name}=`;
      const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
      return item ? decodeURIComponent(item.slice(prefix.length)) : "";
    }
    window.fetch = async (resource, options = {}) => {
      const requestOptions = {...options, credentials: "same-origin"};
      const method = String(requestOptions.method || "GET").toUpperCase();
      const target = new URL(resource instanceof Request ? resource.url : String(resource), window.location.href);
      const sameOrigin = target.origin === window.location.origin;
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        requestOptions.headers = new Headers(requestOptions.headers || {});
        const csrfToken = cookieValue("pfempire_csrf");
        if (csrfToken && sameOrigin) requestOptions.headers.set("X-CSRF-Token", csrfToken);
        if (localImportToken && sameOrigin && ["/api/paper-import", "/api/library-upload", "/api/library-sync"].includes(target.pathname)) {
          requestOptions.headers.set("X-OriginRAG-Local-Token", localImportToken);
        }
      }
      const response = await browserFetch(resource, requestOptions);
      if (sameOrigin && target.pathname === "/api/status" && response.ok) {
        try { localImportToken = (await response.clone().json()).library_access?.csrf_token || ""; } catch { localImportToken = ""; }
      }
      if (response.status === 401 && !String(resource).includes("/api/login")) {
        window.sessionStorage.removeItem("pfempire.currentSession");
        window.location.replace("/login");
      }
      return response;
    };
    const chat = document.getElementById("chat");
    const form = document.getElementById("form");
    const message = document.getElementById("message");
    const send = document.getElementById("send");
    const statusEl = document.getElementById("status");
    const aboutButton = document.getElementById("aboutButton");
    const aboutBackdrop = document.getElementById("aboutBackdrop");
    const aboutClose = document.getElementById("aboutClose");
    const queueSummary = document.getElementById("queueSummary");
    const queueBackdrop = document.getElementById("queueBackdrop");
    const queueDetailList = document.getElementById("queueDetailList");
    const queueScopeNote = document.getElementById("queueScopeNote");
    const queueRefresh = document.getElementById("queueRefresh");
    const queueClose = document.getElementById("queueClose");
    const evidenceSelector = document.getElementById("evidenceSelector");
    const heroTemplate = document.getElementById("hero").outerHTML;
    let hero = document.getElementById("hero");
    const newChat = document.getElementById("newChat");
    const sessionsSection = document.getElementById("sessionsSection");
    const sessionsToggle = document.getElementById("sessionsToggle");
    const sessionsEl = document.getElementById("sessions");
    const profileButton = document.getElementById("profileButton");
    const profileBackdrop = document.getElementById("profileBackdrop");
    const profileText = document.getElementById("profileText");
    const profileSave = document.getElementById("profileSave");
    const profileClose = document.getElementById("profileClose");
    const libraryButton = document.getElementById("libraryButton");
    const libraryBrowserBackdrop = document.getElementById("libraryBrowserBackdrop");
    const libraryBrowserClose = document.getElementById("libraryBrowserClose");
    const librarySearch = document.getElementById("librarySearch");
    const libraryScope = document.getElementById("libraryScope");
    const libraryResults = document.getElementById("libraryResults");
    const libraryResultCount = document.getElementById("libraryResultCount");
    const libraryLoadMore = document.getElementById("libraryLoadMore");
    const paperUploadButton = document.getElementById("paperUploadButton");
    const paperUploadBackdrop = document.getElementById("paperUploadBackdrop");
    const paperUploadClose = document.getElementById("paperUploadClose");
    const paperDropZone = document.getElementById("paperDropZone");
    const paperFileInput = document.getElementById("paperFileInput");
    const paperDoi = document.getElementById("paperDoi");
    const paperUploadList = document.getElementById("paperUploadList");
    const librarySyncStatus = document.getElementById("librarySyncStatus");
    const librarySyncLabel = document.getElementById("librarySyncLabel");
    const librarySyncCounts = document.getElementById("librarySyncCounts");
    const studyAuditBackdrop = document.getElementById("studyAuditBackdrop");
    const studyAuditResult = document.getElementById("studyAuditResult");
    const studyAuditClose = document.getElementById("studyAuditClose");
    const sidebarToggle = document.getElementById("sidebarToggle");
    const sidebarClose = document.getElementById("sidebarClose");
    const sidebarResizer = document.getElementById("sidebarResizer");
    const sidebarBackdrop = document.getElementById("sidebarBackdrop");
    const userBadge = document.getElementById("userBadge");
    const logoutButton = document.getElementById("logoutButton");
    const connectionIndicator = document.getElementById("connectionIndicator");
    const libraryStatsButton = document.getElementById("libraryStatsButton");
    const libraryStatsTotal = document.getElementById("libraryStatsTotal");
    const libraryStatsPopover = document.getElementById("libraryStatsPopover");
    const libraryStatsDonut = document.getElementById("libraryStatsDonut");
    const libraryStatsDonutTotal = document.getElementById("libraryStatsDonutTotal");
    const libraryStatsGroups = document.getElementById("libraryStatsGroups");
    const libraryStatsCoverage = document.getElementById("libraryStatsCoverage");
    const profileStorageKey = "pfempire.responseProfile";
    const sessionsCollapsedKey = "pfempire.sessionsCollapsed";
    const sidebarCollapsedKey = "pfempire.sidebarCollapsed";
    const sidebarWidthKey = "pfempire.sidebarWidth";
    const currentSessionStorageKey = "pfempire.currentSession";
    let currentSessionDir = "";
    let submittingTurn = false;
    let cancelSubmittedTurn = false;
    let knownJobStates = new Map();
    const activeTurnJobs = new Map();
    const cancelRequests = new Set();
    const composerStop = document.getElementById('composerStop');
    let jobPollBusy = false;
    let jobPollFailures = 0;
    let nextJobPollAt = 0;
    let lastJobSignature = "";
    let sessionLoadSequence = 0;
    let sessionsLoadSequence = 0;
    let sessionNavigationPending = false;
    let sessionReconcileTimer = null;
    let sessionReconcilePending = false;
    let libraryBusy = false;
    let visibleLibraryBatch = "";
    let libraryCompleteUntil = 0;
    let librarySearchTimer = null;
    let libraryRequestSequence = 0;
    let libraryOffset = 0;
    let libraryHasMore = false;
    let libraryLoading = false;
    const libraryCategoryLabels = {
      var_pfemp1: "Malaria · var/PfEMP1",
      host_immunity: "Malaria · host immunity",
      malaria_host_omics: "Malaria · host omics",
      malaria_host_genetics: "Malaria · host genetics",
      resistance_dx: "Malaria · resistance/diagnostics",
      general_malaria: "Malaria · general biology",
      parasite_biology: "Malaria · parasite biology",
      malaria_omics: "Malaria · omics/signatures",
      malaria_tools: "Malaria · tools",
      malaria: "Malaria · other",
      covid_host_response: "COVID-19 · host response",
      infectious_disease_host_response: "Other infection · host response",
      noninfectious_disease_biology: "Non-infectious disease biology",
      host_tissue_atlas: "Host tissue atlases",
      host_immunity_general: "Host immunology",
      general_methods: "General methods",
      sc_tools: "Single-cell · perturbation/tools",
      sc_harmonization: "Single-cell · integration",
      scrna_annotation: "Single-cell · annotation",
      tools_ml: "Computational methods",
      foundation_models: "AI · language/foundation models",
      evidence_synthesis: "Evidence synthesis",
      sex_diff: "Sex differences",
      latent_variable_methods: "Latent-variable methods",
      rna_seq_methods: "RNA-seq methods",
      bioinformatics: "Bioinformatics",
      uncategorized: "Uncategorized"
    };

    logoutButton.addEventListener("click", async () => {
      logoutButton.disabled = true;
      try {
        await fetch("/api/logout", {method: "POST"});
      } finally {
        window.sessionStorage.removeItem(currentSessionStorageKey);
        window.location.replace("/login");
      }
    });

    function settings() {
      return {
        backend: document.getElementById("backend").value,
        preset: document.getElementById("preset").value,
        evidence_selector: evidenceSelector.value,
        repair_attempts: Number(document.getElementById("repair").value || 0),
        template_fallback: document.getElementById("fallback").checked,
        category: document.getElementById("category").value || null,
        profile_instruction: readProfileInstruction()
      };
    }

    function addMessage(role, text, extra, result = null) {
      if (hero) hero.remove();
      const wrap = document.createElement("div");
      wrap.className = `msg ${role}`;
      const roleEl = document.createElement("div");
      roleEl.className = "role";
      roleEl.textContent = role === "user" ? "You" : "OriginRAG";
      const bubble = document.createElement("div");
      bubble.className = role === "assistant" ? "bubble markdown" : "bubble";
      if (role === "assistant") {
        renderMarkdown(bubble, text, result);
      } else {
        bubble.textContent = text;
      }
      wrap.appendChild(roleEl);
      wrap.appendChild(bubble);
      if (extra) wrap.appendChild(extra);
      chat.appendChild(wrap);
      chat.scrollTop = chat.scrollHeight;
      return wrap;
    }

    function addJobMessage(job, includeUser = false) {
      rememberTurnJob(job);
      const jobId = String(job.job_id || "");
      const existing = jobId
        ? Array.from(document.querySelectorAll("[data-job-id]")).find(
            (node) => node.dataset.jobId === jobId
          )
        : null;
      if (existing) {
        updateJobMessage(existing, job);
        return existing;
      }
      if (includeUser) addMessage("user", job.message || "Submitted question");
      if (hero) hero.remove();
      const wrap = document.createElement("div");
      wrap.className = "msg assistant working";
      wrap.dataset.jobId = jobId;
      const roleEl = document.createElement("div");
      roleEl.className = "role";
      roleEl.textContent = "OriginRAG";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const dot = document.createElement("span");
      dot.className = "working-dot";
      const stage = document.createElement("span");
      stage.className = "working-stage";
      const label = document.createElement("span");
      label.className = "working-stage-label";
      const progress = document.createElement("div");
      progress.className = "working-progress";
      progress.setAttribute("role", "progressbar");
      progress.setAttribute("aria-valuemin", "0");
      progress.setAttribute("aria-valuemax", "100");
      progress.setAttribute("aria-valuenow", "6");
      const progressBar = document.createElement("div");
      progressBar.className = "working-progress-bar";
      progress.appendChild(progressBar);
      stage.appendChild(label);
      stage.appendChild(progress);
      bubble.appendChild(dot);
      bubble.appendChild(stage);
      const stop = document.createElement('button');
      stop.type = 'button'; stop.className = 'job-stop'; stop.textContent = '■ Stop';
      stop.setAttribute('aria-label', 'Stop this request');
      stop.addEventListener('click', () => cancelTurnJob(jobId));
      bubble.appendChild(stop);
      wrap.appendChild(roleEl);
      wrap.appendChild(bubble);
      chat.appendChild(wrap);
      chat.scrollTop = chat.scrollHeight;
      updateJobMessage(wrap, job);
      return wrap;
    }

    function jobStageLabel(job) {
      if (job.state === 'submitting' && cancelSubmittedTurn) return 'Stop requested · waiting for server confirmation';
      if (job.state === 'cancelling') return 'Stopping this request…';
      if (job.state === 'cancelled') return job.stage || 'Stopped by you';
      if (job.state === "queued") {
        const ahead = Number(job.jobs_ahead || 0);
        if (ahead === 0) return "Waiting in queue · next to run";
        return `Waiting in queue · ${ahead} ${ahead === 1 ? "job" : "jobs"} ahead`;
      }
      if (job.state === "running") return job.stage || "Reading local evidence";
      if (job.state === "failed") return `Job failed: ${job.error || "unknown error"}`;
      if (job.state === "interrupted") return "Job was interrupted by a server restart.";
      return job.stage || "Complete";
    }

    function updateJobMessage(wrap, job) {
      rememberTurnJob(job);
      job = activeTurnJobs.get(job.job_id) || job;
      if (!wrap) return;
      wrap.classList.toggle("working", ["submitting", "queued", "running", "cancelling"].includes(job.state));
      const stop = wrap.querySelector('.job-stop');
      if (stop) {
        stop.hidden = !['submitting', 'queued', 'running', 'cancelling'].includes(job.state);
        stop.disabled = job.state === 'cancelling' || (job.state === 'submitting' && cancelSubmittedTurn) || job.can_cancel === false || cancelRequests.has(job.job_id);
        stop.textContent = job.state === 'cancelling' || (job.state === 'submitting' && cancelSubmittedTurn) ? 'Stopping…' : '■ Stop';
      }
      wrap.classList.toggle("job-failed", ["failed", "interrupted"].includes(job.state));
      const label = wrap.querySelector(".working-stage-label");
      if (label) label.textContent = jobStageLabel(job);
      const progress = wrap.querySelector(".working-progress");
      const bar = wrap.querySelector(".working-progress-bar");
      if (progress) progress.hidden = ['submitting', 'cancelled'].includes(job.state);
      const bounded = Math.max(2, Math.min(100, Number(job.progress) || 2));
      if (progress) progress.setAttribute("aria-valuenow", String(bounded));
      if (bar) bar.style.width = `${bounded}%`;
      const dot = wrap.querySelector(".working-dot");
      if (dot) dot.hidden = !["submitting", "queued", "running", "cancelling"].includes(job.state);
    }

    function rememberTurnJob(job) {
      if (!job.job_id || job.state === 'submitting') return;
      const previous = activeTurnJobs.get(job.job_id);
      if (previous && ['cancelled', 'cancelling'].includes(previous.state) && ['queued', 'running'].includes(job.state)) return;
      activeTurnJobs.set(job.job_id, job);
      updateComposerStop();
    }

    function updateComposerStop() {
      const jobs = [...activeTurnJobs.values()].filter(job => job.session_dir === currentSessionDir && ['queued','running','cancelling'].includes(job.state));
      jobs.sort((a,b) => Number(a.state === 'queued') - Number(b.state === 'queued') || Number(a.created_at || 0) - Number(b.created_at || 0));
      const job = jobs[0];
      if (!job && submittingTurn && !sessionNavigationPending) {
        composerStop.hidden = false;
        composerStop.disabled = cancelSubmittedTurn;
        composerStop.textContent = cancelSubmittedTurn ? 'Stopping…' : '■ Stop';
        composerStop.onclick = () => cancelTurnJob('submission-pending');
        return;
      }
      composerStop.hidden = !job || sessionNavigationPending;
      composerStop.disabled = !job || job.state === 'cancelling' || job.can_cancel === false || cancelRequests.has(job.job_id);
      composerStop.textContent = job?.state === 'cancelling' ? 'Stopping…' : '■ Stop';
      composerStop.onclick = () => { if (job) cancelTurnJob(job.job_id); };
    }

    async function cancelTurnJob(jobId) {
      if (jobId.startsWith('submission-')) {
        cancelSubmittedTurn = true;
        document.querySelectorAll('[data-job-id^="submission-"]').forEach(wrap => updateJobMessage(wrap, {job_id:wrap.dataset.jobId, state:'submitting'}));
        updateComposerStop();
        return;
      }
      if (cancelRequests.has(jobId)) return;
      cancelRequests.add(jobId);
      const update = job => {
        rememberTurnJob(job);
        const wrap = [...document.querySelectorAll('[data-job-id]')].find(el => el.dataset.jobId === jobId);
        if (wrap) updateJobMessage(wrap, job);
      };
      const existing = activeTurnJobs.get(jobId);
      if (existing) update(existing);
      try {
        const response = await fetch('/api/job/cancel', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({job_id:jobId})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not stop the request.');
        update(data);
        statusEl.textContent = jobStageLabel(data);
        pollJobs();
      } catch (error) {
        statusEl.textContent = error.message;
      } finally {
        cancelRequests.delete(jobId);
        const job = activeTurnJobs.get(jobId);
        if (job) update(job);
        updateComposerStop();
      }
    }

    function setSubmitting(isSubmitting) {
      submittingTurn = isSubmitting;
      updateComposerStop();
      send.disabled = isSubmitting || libraryBusy || sessionNavigationPending;
      message.disabled = libraryBusy || sessionNavigationPending;
    }

    function setSessionNavigationPending(isPending) {
      sessionNavigationPending = isPending;
      updateComposerStop();
      send.disabled = submittingTurn || libraryBusy || isPending;
      message.disabled = libraryBusy || isPending;
    }

    function renderMarkdown(container, text, result = null) {
      const lines = String(text || "").split("\n");
      if (result?.quick_list) {
        const heading = document.createElement("p");
        heading.textContent = lines[0];
        container.appendChild(heading);
        const list = document.createElement("ol");
        for (const paper of result.sources || []) {
          const item = document.createElement("li");
          const href = paper.local_pdf ? "/reader?paper_id=" + encodeURIComponent(paper.paper_id)
            : paper.doi ? "https://doi.org/" + paper.doi : "";
          const title = document.createElement(href ? "a" : "span");
          if (href) title.href = href;
          title.textContent = paper.title;
          item.append(title, paper.year ? " (" + paper.year + ")" : "");
          list.appendChild(item);
        }
        if (list.children.length) container.appendChild(list);
        return;
      }
      const citationSources = new Map();
      for (const source of (result || {}).sources || []) {
        for (const evidenceId of source.evidence_ids || []) citationSources.set(evidenceId, source);
      }
      let list = null;
      let listType = null;
      let paragraph = [];
      function closeList() {
        if (list) {
          container.appendChild(list);
          list = null;
          listType = null;
        }
      }
      function closeParagraph() {
        if (!paragraph.length) return;
        const p = document.createElement("p");
        appendCitationAwareText(p, paragraph.join(" ").replace(/\*\*/g, ""), citationSources);
        container.appendChild(p);
        paragraph = [];
      }
      for (const raw of lines) {
        const line = raw.trimEnd();
        if (!line.trim()) {
          closeParagraph();
          closeList();
          continue;
        }
        if (line.startsWith("## ")) {
          closeParagraph();
          closeList();
          const h = document.createElement("h2");
          h.textContent = line.replace(/^##\s+/, "");
          container.appendChild(h);
        } else if (line.startsWith("### ")) {
          closeParagraph();
          closeList();
          const h = document.createElement("h3");
          h.textContent = line.replace(/^###\s+/, "");
          container.appendChild(h);
        } else if (line.startsWith("- ")) {
          closeParagraph();
          if (!list || listType !== "ul") {
            closeList();
            list = document.createElement("ul");
            listType = "ul";
          }
          const li = document.createElement("li");
          appendCitationAwareText(li, line.replace(/^-\s+/, "").replace(/\*\*/g, ""), citationSources);
          list.appendChild(li);
        } else if (/^\d+\.\s+/.test(line)) {
          closeParagraph();
          if (!list || listType !== "ol") {
            closeList();
            list = document.createElement("ol");
            listType = "ol";
          }
          const li = document.createElement("li");
          appendCitationAwareText(li, line.replace(/^\d+\.\s+/, "").replace(/\*\*/g, ""), citationSources);
          list.appendChild(li);
        } else {
          closeList();
          paragraph.push(line.trim());
        }
      }
      closeParagraph();
      closeList();
    }

    function appendCitationAwareText(container, text, citationSources) {
      const parts = String(text || "").split(/(\[\[cite:E\d{3}\]\])/g);
      for (const part of parts) {
        const tokenMatch = part.match(/^\[\[cite:(E\d{3})\]\]$/);
        if (!tokenMatch) {
          container.appendChild(document.createTextNode(part));
          continue;
        }
        const source = citationSources.get(tokenMatch[1]);
        if (!source) {
          container.appendChild(document.createTextNode("source unavailable"));
          continue;
        }
        const citation = source.url ? document.createElement("a") : document.createElement("span");
        citation.className = "inline-citation";
        citation.textContent = source.citation_short || `${source.author || "Unknown"} et al., ${source.year || "n.d."}`;
        citation.title = `${source.title || "Untitled"}. Open DOI, PubMed, or PMC page.`;
        if (source.url) {
          citation.href = source.url;
          citation.target = "_blank";
          citation.rel = "noopener noreferrer";
        }
        container.appendChild(citation);
      }
    }

    function sourceTypeCode(assessment) {
      return {
        primary_research: "P",
        systematic_review_meta_analysis: "S",
        narrative_review: "S",
        editorial_commentary: "S",
        reanalysis_secondary_data: "R",
        protocol: "X",
        uncertain: "?",
      }[(assessment || {}).source_type] || "?";
    }

    function buildSourceLegend() {
      const legend = document.createElement("details");
      legend.className = "source-legend";
      legend.innerHTML = "<summary>About these references</summary><p>● local searchable full text · ○ missing/unresolved · P primary research · S review/meta-analysis · R reanalysis · X protocol · ? uncertain. Author-year citations open the DOI, PubMed, or PMC page. For an empirical claim OriginRAG prefers the directly reporting primary study; a secondary carrier remains visible when the direct report cannot be verified.</p>";
      return legend;
    }

    function buildAnswerDetails(summary) {
      const panel = document.createElement("details");
      panel.className = "answer-details";
      const heading = document.createElement("summary");
      heading.textContent = "Search & citation checks";
      panel.append(heading, summary);
      return panel;
    }

    function buildAnswerWarnings(data) {
      const warnings = document.createElement("div");
      warnings.className = "answer-warnings";
      const messages = [];
      if (data.answer_state === "withheld") messages.push("Answer withheld — see the explanation above.");
      else if (data.answer_state === "provisional" && assuranceStatus(data.assurance || {}).label !== "Study overlap needs review") {
        messages.push("Provisional answer — study overlap still needs review.");
      }
      if (data.grounding_passed === false) messages.push("Citation check failed — verify the cited claims before using this answer.");
      else if (data.grounding_passed !== true && data.task !== "sources") messages.push("Citations have not been checked.");
      for (const message of messages) {
        const note = document.createElement("p");
        note.textContent = message;
        warnings.appendChild(note);
      }
      return warnings;
    }

    function resultMeta(data) {
      const box = document.createElement("div");
      if (data.task === "paper_summaries") {
        for (const part of data.parts || []) {
          const details = document.createElement("details");
          const label = document.createElement("summary");
          label.textContent = part.sources?.[0]?.title || "Paper details";
          details.append(label, resultMeta(part)); box.appendChild(details);
        }
        return box;
      }
      if (data.task === "library_status" || data.quick_list) return box;
      box.className = "result-meta";
      const summary = document.createElement("div");
      summary.className = "result-summary";
      if (data.task === "paper_summary") {
        const card = data.reading_card || {};
        if (card.excerpt_only) return box;
        const coverage = card.coverage || {};
        summary.appendChild(makePill(coverage.basis === "abstract_only" ? "Paper summary · abstract only" : "Paper summary · selected passages"));
        if (card.cache_hit) summary.appendChild(makePill("Saved summary"));
        summary.appendChild(makePill(formatElapsed(data.elapsed_seconds)));
        box.appendChild(summary);
        const details = document.createElement("details");
        details.className = "sources-box";
        const heading = document.createElement("summary");
        heading.textContent = "Passages behind this summary";
        details.appendChild(heading);
        const seen = new Set();
        for (const passage of card.passages || []) {
          const key = `${passage.section}|${passage.page_start}|${passage.quote}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const label = document.createElement("p");
          label.textContent = `${passage.section || "Passage"}${passage.page_start ? ` · p. ${passage.page_start}` : " · page unavailable"}`;
          const quote = document.createElement("blockquote");
          quote.textContent = passage.quote;
          details.append(label, quote);
        }
        box.appendChild(details);
        box.appendChild(buildSourcesPanel(data.sources || []));
        return box;
      }
      if (data.task === "sources") {
        const search = data.paper_search || {};
        const matchCount = (data.sources || []).length;
        summary.appendChild(makePill(`${matchCount} paper ${matchCount === 1 ? "match" : "matches"}`, matchCount ? "ok" : "warn"));
        summary.appendChild(makePill(
          search.mode === "targeted_lookup" ? "Targeted lookup" : "Topical discovery"
        ));
        summary.appendChild(makePill(
          search.planner_assisted
            ? "LLM query expansion"
            : search.planner_attempted
              ? "Automatic query expansion"
              : "Deterministic expansion",
          search.planner_assisted || search.planner_attempted ? "warn" : "ok"
        ));
        summary.appendChild(makePill("Deterministic ranking", "ok"));
        summary.appendChild(makePill(formatElapsed(data.elapsed_seconds)));
        const assurance = data.assurance || {};
        box.appendChild(buildPaperSearchOriginSummary(assurance));
        if (hasOriginScreen(assurance)) {
          box.appendChild(buildStudyAuditAction(assurance));
        }
        if ((data.sources || []).length) {
          box.appendChild(buildPaperSearchTable(
            data.sources,
            search.requested_detail_fields || []
          ));
        }
        box.appendChild(buildAnswerDetails(summary));
        return box;
      }
      const grade = String(data.evidence_grade || "unknown");
      summary.appendChild(makePill(`Search results · ${grade}`, grade === "good" || grade === "usable" ? "ok" : grade === "weak" ? "warn" : "bad"));
      summary.appendChild(makePill(`${data.n_unique_papers || 0} papers · ${data.n_chunks || 0} full-text passages`));
      summary.appendChild(makePill(formatElapsed(data.elapsed_seconds)));
      if (data.answer_state === "withheld") {
        summary.appendChild(makePill("Answer withheld", "warn"));
      } else if (data.answer_state === "provisional") {
        summary.appendChild(makePill("Study overlap needs review", "warn"));
      }
      if (data.grounding_passed === true) {
        summary.appendChild(makePill("Citations checked", "ok"));
      } else if (data.grounding_passed === false) {
        summary.appendChild(makePill("Citation check failed", "bad"));
      } else {
        summary.appendChild(makePill("Citations not checked"));
      }
      const warnings = buildAnswerWarnings(data);
      if (warnings.childElementCount) box.appendChild(warnings);
      const assurance = data.assurance || {};
      box.appendChild(buildAssurancePanel(assurance));
      if ((data.sources || []).length) box.appendChild(buildSourcesPanel(data.sources));
      if ((data.claim_source_chains || []).length) box.appendChild(buildClaimSourceChains(data.claim_source_chains));
      box.appendChild(buildAnswerDetails(summary));
      return box;
    }

    function buildClaimSourceChains(chains) {
      const panel = document.createElement("details");
      panel.className = "claim-trace-panel";
      panel.open = false;
      const summary = document.createElement("summary");
      summary.textContent = `Secondary-source follow-up · ${chains.length}`;
      panel.appendChild(summary);
      const list = document.createElement("div");
      list.className = "claim-trace-list";
      for (const chain of chains) {
        const item = document.createElement("section");
        item.className = "claim-trace-item";
        const claim = document.createElement("p");
        claim.className = "claim-trace-text";
        claim.textContent = chain.claim_text || "Cited central claim";
        item.appendChild(claim);
        for (const carrier of chain.carriers || []) {
          const path = document.createElement("div");
          path.className = "source-chain";
          path.appendChild(sourceChainNode(
            `Claim carrier · ${sourceTypeLabel(carrier.source_type)}`,
            carrier.citation || carrier.paper_id || "Unknown carrier",
            `${directReportLabel(carrier.direct_report_status)} · ${(carrier.library_status || {}).display || "? Local status unknown"}`
          ));
          path.appendChild(sourceChainArrow());
          const cited = document.createElement("div");
          cited.className = "source-chain-citations";
          if ((carrier.references || []).length) {
            for (const reference of carrier.references) {
              cited.appendChild(sourceChainNode(
                sourceTypeLabel(reference.source_type),
                reference.citation || reference.title || reference.work_id || "Unresolved cited work",
                `${directReportLabel(reference.direct_report_status)} · ${(reference.library_status || {}).display || "? Local status unknown"}`
              ));
            }
          } else {
            cited.appendChild(sourceChainNode(
              "Cited source",
              carrier.trace_status === "carrier_pdf_missing" ? "Carrier PDF missing" : "No citation linked safely",
              "Direct-report source unresolved",
              true
            ));
          }
          path.appendChild(cited);
          path.appendChild(sourceChainArrow());
          path.appendChild(sourceChainNode(
            "Scientific origin · independence layer",
            "Unresolved",
            "Handled by Study counting; reviewed origin evidence required",
            true
          ));
          item.appendChild(path);
        }
        const policy = document.createElement("p");
        policy.className = "claim-trace-policy";
        policy.textContent = chain.policy || "Candidate links are not automatically promoted to scientific origins.";
        item.appendChild(policy);
        list.appendChild(item);
      }
      panel.appendChild(list);
      return panel;
    }

    function sourceChainNode(label, value, status, unresolved = false) {
      const node = document.createElement("div");
      node.className = `source-chain-node${unresolved ? " unresolved" : ""}`;
      const labelEl = document.createElement("span");
      labelEl.textContent = label;
      const valueEl = document.createElement("strong");
      valueEl.textContent = value;
      const statusEl = document.createElement("span");
      statusEl.textContent = status;
      node.appendChild(labelEl);
      node.appendChild(valueEl);
      node.appendChild(statusEl);
      return node;
    }

    function sourceChainArrow() {
      const arrow = document.createElement("span");
      arrow.className = "source-chain-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "→";
      return arrow;
    }

    function sourceTypeLabel(assessment) {
      return (assessment || {}).label || "Source type uncertain";
    }

    function directReportLabel(status) {
      return {
        reviewed_direct_source: "Reviewed direct source",
        reviewed_not_direct: "Reviewed as not direct",
        identity_unresolved: "Identity unresolved",
        likely_direct_primary_automatic: "Likely direct report · automatic",
        secondary_or_nonreporting_automatic: "Secondary/non-reporting source · automatic",
        reanalysis_not_origin_automatic: "Reanalysis; original dataset source still needed",
        source_type_uncertain: "Direct-report status uncertain",
      }[status] || "Review required";
    }

    function makePill(text, tone = "") {
      const pill = document.createElement("span");
      pill.className = `pill ${tone}`.trim();
      pill.textContent = text;
      return pill;
    }

    function buildAssurancePanel(assurance) {
      const panel = document.createElement("details");
      panel.className = "assurance-panel";
      panel.setAttribute("aria-label", "Evidence status and details");
      panel.open = false;
      const head = document.createElement("summary");
      head.className = "assurance-head";
      const title = document.createElement("div");
      title.className = "assurance-title";
      const titleStrong = document.createElement("strong");
      titleStrong.textContent = "Evidence status";
      const subtitle = document.createElement("span");
      subtitle.textContent = assurance.audit_run
        ? assurance.lineage_graph_status === "reviewed"
          ? "Study-overlap details and evidence requirements."
          : "Independent studies not yet verified."
        : "No study-overlap check available.";
      title.appendChild(titleStrong);
      title.appendChild(subtitle);
      head.appendChild(title);

      const status = document.createElement("span");
      const details = assuranceStatus(assurance);
      status.className = `assurance-status ${details.tone}`;
      status.textContent = details.label;
      head.appendChild(status);
      panel.appendChild(head);

      if (assurance.audit_run) {
        const reportCount = assurance.cited_report_count !== undefined
          ? assurance.cited_report_count
          : assurance.raw_paper_count;
        const familyCount = assurance.cited_publication_family_upper_bound !== undefined
          ? assurance.cited_publication_family_upper_bound
          : assurance.publication_family_upper_bound !== undefined
          ? assurance.publication_family_upper_bound
          : reportCount;
        const reviewedOrigins = assurance.lineage_graph_status === "reviewed";
        const originCount = reviewedOrigins
          ? formatRange(assurance.lineage_count_lower, assurance.lineage_count_upper, null)
          : "?";
        const flow = document.createElement("div");
        flow.className = "assurance-flow";
        flow.appendChild(assuranceFlowStep("Cited reports", formatNumber(reportCount)));
        flow.appendChild(assuranceFlowArrow());
        flow.appendChild(assuranceFlowStep(
          "Publication families",
          `≤${formatNumber(familyCount)}`
        ));
        flow.appendChild(assuranceFlowArrow());
        flow.appendChild(assuranceFlowStep("Independent studies", originCount));
        panel.appendChild(flow);
      }

      const note = document.createElement("div");
      note.className = "assurance-note";
      note.textContent = assuranceNote(assurance);
      panel.appendChild(note);
      if (assurance.audit_run) panel.appendChild(buildStudyAuditAction(assurance));
      return panel;
    }

    function buildStudyAuditAction(assurance) {
      const row = document.createElement("div");
      row.className = "study-audit-action";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "study-audit-button";
      button.textContent = "View study overlap";
      button.addEventListener("click", () => openStudyAudit(assurance));
      row.appendChild(button);
      return row;
    }

    function hasOriginScreen(assurance) {
      return Boolean(assurance.audit_run || assurance.mode === "lineage_screen");
    }

    function openStudyAudit(assurance) {
      studyAuditResult.innerHTML = "";
      const reportCount = assurance.cited_report_count ?? assurance.raw_paper_count ?? 0;
      const familyCount = assurance.cited_publication_family_upper_bound
        ?? assurance.publication_family_upper_bound
        ?? reportCount;
      const reviewedOrigins = assurance.lineage_graph_status === "reviewed";
      const originCount = reviewedOrigins
        ? formatRange(assurance.lineage_count_lower, assurance.lineage_count_upper, null)
        : "?";
      const flow = document.createElement("div");
      flow.className = "assurance-flow";
      flow.appendChild(assuranceFlowStep(
        assurance.mode === "lineage_screen" ? "Returned reports" : "Cited reports",
        formatNumber(reportCount)
      ));
      flow.appendChild(assuranceFlowArrow());
      flow.appendChild(assuranceFlowStep("Publication families", `≤${formatNumber(familyCount)}`));
      flow.appendChild(assuranceFlowArrow());
      flow.appendChild(assuranceFlowStep("Independent studies", originCount));
      studyAuditResult.appendChild(flow);

      studyAuditResult.appendChild(studyAuditSection(
        "Publication versions",
        assurance.publication_family_links || [],
        "No exact preprint, journal-version, or duplicate-identifier link was detected. This is not proof that every report is a separate publication family."
      ));
      studyAuditResult.appendChild(studyAuditSection(
        "Shared cohorts, trials, or datasets",
        assurance.shared_origin_signals || [],
        "No shared cohort, trial, or dataset identifier was detected in the screened local text. Unnamed reuse can still remain hidden."
      ));
      studyAuditResult.appendChild(studyAuditSection(
        "Shared mentions needing inspection",
        assurance.review_only_origin_mentions || [],
        "No shared identifier was found only in background, references, or ambiguous context."
      ));

      const conclusion = document.createElement("section");
      conclusion.className = "study-audit-section";
      const heading = document.createElement("h3");
      heading.textContent = "What can be counted?";
      const text = document.createElement("p");
      text.textContent = reviewedOrigins
        ? `Reviewed study-overlap evidence supports the displayed independent-study range: ${originCount}.`
        : "Independent studies: unknown until study overlap has been reviewed.";
      conclusion.appendChild(heading);
      conclusion.appendChild(text);
      studyAuditResult.appendChild(conclusion);

      studyAuditBackdrop.classList.add("open");
      studyAuditBackdrop.setAttribute("aria-hidden", "false");
      studyAuditClose.focus();
    }

    function studyAuditSection(title, links, emptyText) {
      const section = document.createElement("section");
      section.className = "study-audit-section";
      const heading = document.createElement("h3");
      heading.textContent = title;
      section.appendChild(heading);
      if (!links.length) {
        const text = document.createElement("p");
        text.textContent = emptyText;
        section.appendChild(text);
        return section;
      }
      const list = document.createElement("ul");
      list.className = "study-audit-list";
      for (const link of links) {
        const item = document.createElement("li");
        const relationship = (link.relationship_types || []).map(formatRelationship).join(", ");
        const details = lineageSignalDetails(link);
        item.textContent = `${link.label_a || link.paper_id_a} ↔ ${link.label_b || link.paper_id_b}${relationship ? ` · ${relationship}` : ""}${details ? ` · ${details}` : ""}${link.automatic ? " · automatic candidate; review required" : ""}`;
        list.appendChild(item);
      }
      section.appendChild(list);
      return section;
    }

    function formatRelationship(value) {
      return String(value || "").replaceAll("_", " ");
    }

    function lineageSignalDetails(link) {
      const details = link.details || {};
      const identifiers = details.shared || details.shared_labels || [];
      const parts = identifiers.length ? [identifiers.join(", ")] : [];
      const evidence = details.evidence || {};
      const locations = [];
      for (const [paperId, records] of Object.entries(evidence)) {
        if (!Array.isArray(records) || !records.length) continue;
        const record = records.find((value) => value.mention_class === "own_data") || records[0];
        const label = paperId === link.paper_id_a
          ? (link.label_a || paperId)
          : paperId === link.paper_id_b
            ? (link.label_b || paperId)
            : paperId;
        const pageStart = record.page_start;
        const pageEnd = record.page_end;
        const location = pageStart && pageEnd && pageStart !== pageEnd
          ? `pp. ${pageStart}–${pageEnd}`
          : pageStart
            ? `p. ${pageStart}`
            : record.chunk_id || "local text";
        locations.push(`${label}: ${location}, ${formatRelationship(record.mention_class || "ambiguous")}`);
      }
      if (locations.length) parts.push(locations.join("; "));
      return parts.join(" · ");
    }

    function closeStudyAudit() {
      studyAuditBackdrop.classList.remove("open");
      studyAuditBackdrop.setAttribute("aria-hidden", "true");
    }

    function assuranceFlowStep(label, value) {
      const step = document.createElement("div");
      step.className = "assurance-flow-step";
      const name = document.createElement("span");
      name.textContent = label;
      const displayed = document.createElement("strong");
      displayed.textContent = value;
      step.appendChild(name);
      step.appendChild(displayed);
      return step;
    }

    function assuranceFlowArrow() {
      const arrow = document.createElement("span");
      arrow.className = "assurance-flow-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "→";
      return arrow;
    }

    function assuranceMetric(label, value) {
      const metric = document.createElement("div");
      metric.className = "assurance-metric";
      const name = document.createElement("span");
      name.textContent = label;
      const displayed = document.createElement("strong");
      displayed.textContent = value;
      metric.appendChild(name);
      metric.appendChild(displayed);
      return metric;
    }

    function assuranceStatus(assurance) {
      if (!assurance.audit_run) return {label: "More evidence needed", tone: "warn"};
      if (
        (assurance.detected_publication_family_links || 0) > 0
        || (assurance.detected_shared_origin_signals || 0) > 0
        || (assurance.review_only_origin_mention_count || 0) > 0
      ) return {label: "Study overlap needs review", tone: "warn"};
      if (assurance.status === "robustly_sufficient" && !assurance.provisional) {
        return {label: "Supported", tone: "ok"};
      }
      if (assurance.status === "insufficient_even_optimistically") {
        return {label: "More evidence needed", tone: "bad"};
      }
      return {label: "Study overlap needs review", tone: "warn"};
    }

    function assuranceNote(assurance) {
      if (!assurance.audit_run) {
        return "The automatic study-overlap check was unavailable for this result.";
      }
      const floorDiagnostic = assurance.selected_report_floor_met === false
        ? assurance.selection_stopping_reason === "no_valid_candidates"
          ? "The retrieved papers did not cover all parts of the question."
          : "The evidence selection ended before enough suitable reports were found."
        : "";
      const withFloorDiagnostic = (message) => floorDiagnostic
        ? `${floorDiagnostic} ${message}`
        : message;
      const unresolved = (assurance.robust_unresolved_facets || [])
        .slice(0, 3)
        .map((facet) => String(facet).replace(/^[a-f0-9]{12}_/, "").replaceAll("_", " "))
        .join(", ");
      if ((assurance.detected_publication_family_links || 0) > 0) {
        const pairs = (assurance.publication_family_links || []).slice(0, 3).map((link) => `${link.label_a} ↔ ${link.label_b}`).join("; ");
        const citedFamilyCount = assurance.cited_publication_family_upper_bound
          ?? assurance.publication_family_upper_bound;
        return withFloorDiagnostic(`Study overlap needs review: ${formatNumber(assurance.cited_report_count ?? assurance.raw_paper_count)} cited reports include at most ${formatNumber(citedFamilyCount)} publication families${pairs ? ` (${pairs})` : ""}. Independent studies remain unknown until the overlap has been reviewed.`);
      }
      if ((assurance.detected_shared_origin_signals || 0) > 0) {
        const pairs = (assurance.shared_origin_signals || []).slice(0, 3).map((link) => `${link.label_a} ↔ ${link.label_b}`).join("; ");
        return withFloorDiagnostic(`Possible shared cohort, trial, or dataset detected${pairs ? `: ${pairs}` : ""}. These reports are not counted as verified independent studies.`);
      }
      if ((assurance.review_only_origin_mention_count || 0) > 0) {
        return withFloorDiagnostic("A shared identifier or cohort label appears in the cited reports, but only in background, references, or ambiguous context. It is queued for inspection and is not counted as shared or independent evidence.");
      }
      if (assurance.lineage_graph_status !== "reviewed" && assurance.lineage_graph_status !== "not_applicable") {
        return withFloorDiagnostic(`Independent studies: unknown until study overlap has been reviewed. The automatic screen found no duplicate publication or shared identifier, but unnamed reuse may remain.${unresolved ? ` Parts of the question still needing support: ${unresolved}.` : ""}`);
      }
      if (assurance.exact_for_declared_uncertainty === false) {
        return withFloorDiagnostic(`Study overlap needs review before the evidence status can be final.${unresolved ? ` Parts of the question still needing support: ${unresolved}.` : ""}`);
      }
      return withFloorDiagnostic(unresolved
        ? `More evidence is needed for: ${unresolved}.`
        : "The evidence covers all required parts of the question after reviewed study overlap is taken into account.");
    }

    function formatNumber(value, digits = 0) {
      if (value === null || value === undefined || value === "") return "—";
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(digits) : "—";
    }

    function formatRange(lower, upper, required, digits = 0) {
      const left = formatNumber(lower, digits);
      const right = formatNumber(upper, digits);
      const target = required !== null && required !== undefined && required !== ""
        ? ` · need ${formatNumber(required, digits)}`
        : "";
      return `${left}–${right}${target}`;
    }

    function formatPercent(value) {
      const number = Number(value);
      return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
    }

    function buildSourcesPanel(sourceRows) {
      const details = document.createElement("details");
      details.className = "sources-box";
      const searchResults = sourceRows.some((source) => source.source_role === "search_result");
      const citedRows = searchResults ? sourceRows : sourceRows.filter((source) => source.cited);
      const displayedRows = citedRows.length ? citedRows : sourceRows;
      details.open = false;
      const summary = document.createElement("summary");
      summary.textContent = searchResults
        ? `Paper matches · ${displayedRows.length}`
        : citedRows.length
          ? `References · ${displayedRows.length} cited`
          : `Retrieved references · ${displayedRows.length} (none cited)`;
      details.appendChild(summary);
      const sources = document.createElement("div");
      sources.className = "source-list";
      const list = document.createElement("ol");
      for (const src of displayedRows) {
        const item = document.createElement("li");
        const titleText = `${src.author || "Unknown"} ${src.year || "n.d."}. ${src.title || "Untitled"}.`;
        const safeTitle = escapeHtml(titleText);
        const titleHtml = src.url
          ? `<a class="source-title" href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer">${safeTitle}</a>`
          : `<span class="source-title">${safeTitle}</span>`;
        const ids = sourceIdentifiers(src);
        const sourceStatus = src.library_status || {};
        const statusHtml = sourceStatus.display
          ? `<span class="source-status ${escapeHtml(sourceStatus.state || "")}" aria-label="${escapeHtml(sourceStatus.label || sourceStatus.display)}">${escapeHtml(sourceStatus.display)}</span>`
          : "";
        const typeAssessment = src.source_type || {};
        const typeCode = sourceTypeCode(typeAssessment);
        const typeHtml = `<span class="source-type"><strong>${escapeHtml(typeCode)}</strong> · ${escapeHtml(typeAssessment.label || "Source type uncertain")} · ${escapeHtml(typeAssessment.confidence || "low")} confidence · automatic</span>`;
        item.innerHTML = `${titleHtml}<span class="source-journal">${escapeHtml(src.journal || "Unknown journal")}</span>`;
        const metadata = document.createElement("details");
        metadata.className = "reference-details";
        metadata.innerHTML = `<summary>Source details</summary>${statusHtml}${ids}${typeHtml}`;
        item.appendChild(metadata);
        if (src.paper_id && window.originragShelfButton) item.appendChild(window.originragShelfButton(src));
        if (searchResults && src.match_reason) {
          const match = document.createElement("span");
          match.className = "source-match";
          match.textContent = `Match: ${src.match_reason}`;
          item.appendChild(match);
        }
        if (src.can_trace_citations && src.paper_id && src.trace_claim_text) {
          const actions = document.createElement("div");
          actions.className = "source-actions";
          const traceButton = document.createElement("button");
          traceButton.type = "button";
          traceButton.className = "source-action";
          traceButton.textContent = "Trace cited sources";
          const trail = document.createElement("div");
          traceButton.addEventListener("click", () => traceCitedSources(src, trail, traceButton));
          actions.appendChild(traceButton);
          item.appendChild(actions);
          item.appendChild(trail);
        }
        list.appendChild(item);
      }
      sources.appendChild(list);
      sources.appendChild(buildSourceLegend());
      details.appendChild(sources);
      return details;
    }

    function buildPaperSearchTable(sourceRows, requestedDetailFields = []) {
      const wrapper = document.createElement("div");
      wrapper.className = "paper-table-wrap";
      const table = document.createElement("table");
      table.className = "paper-table";
      const includeDetails = requestedDetailFields.length > 0
        || sourceRows.some((source) => Object.keys(source.question_fields || {}).length > 0);
      table.innerHTML = `<thead><tr><th>#</th><th>Paper</th><th>Why selected</th>${includeDetails ? "<th>Requested details</th>" : ""}<th>Evidence type</th><th>Availability</th></tr></thead>`;
      const body = document.createElement("tbody");
      sourceRows.forEach((src, index) => {
        const row = document.createElement("tr");
        const rank = document.createElement("td");
        rank.className = "paper-table-rank";
        rank.textContent = String(index + 1).padStart(2, "0");

        const paper = document.createElement("td");
        paper.className = "paper-table-paper";
        const title = src.url ? document.createElement("a") : document.createElement("span");
        title.textContent = src.title || "Untitled";
        if (src.url) {
          title.href = src.url;
          title.target = "_blank";
          title.rel = "noopener noreferrer";
        }
        const meta = document.createElement("span");
        meta.className = "paper-table-meta";
        meta.textContent = `${src.author || "Unknown"} · ${src.year || "n.d."} · ${src.journal || "Unknown journal"}`;
        paper.append(title, meta);
        const identifiers = buildSourceIdentifierLine(src);
        if (identifiers) paper.appendChild(identifiers);
        const familyReports = src.publication_family_reports || [];
        if (familyReports.length > 1) {
          const family = document.createElement("span");
          family.className = "paper-table-meta";
          const reportLabels = familyReports.map((report) => {
            const label = `${report.year || "n.d."}${report.doi ? ` · DOI ${report.doi}` : ""}`;
            return report.paper_id ? `${report.paper_id} (${label})` : label;
          });
          family.textContent = `Publication family: ${familyReports.length} linked reports; this row displays one representative (${reportLabels.join("; ")}).`;
          paper.appendChild(family);
        }

        const reason = document.createElement("td");
        reason.className = "paper-table-reason";
        const reasonText = document.createElement("span");
        reasonText.textContent = src.match_reason || "Relevant title, metadata, or full-text match.";
        reason.appendChild(reasonText);
        if ((src.cited_sources || []).length) {
          const cited = document.createElement("span");
          cited.className = "paper-table-meta";
          cited.appendChild(document.createTextNode("Cited source: "));
          src.cited_sources.forEach((reference, referenceIndex) => {
            if (referenceIndex) cited.appendChild(document.createTextNode("; "));
            const link = reference.url ? document.createElement("a") : document.createElement("span");
            link.textContent = reference.citation || reference.title || "Resolved cited work";
            if (reference.url) {
              link.href = reference.url;
              link.target = "_blank";
              link.rel = "noopener noreferrer";
            }
            cited.appendChild(link);
          });
          reason.appendChild(cited);
        } else if (src.citation_trace_required) {
          const unresolved = document.createElement("span");
          unresolved.className = "paper-table-meta";
          unresolved.textContent = "Main-paper metadata is shown in the Paper column. No directly reporting work cited inside this excerpt could be resolved safely.";
          reason.appendChild(unresolved);
        }

        const type = document.createElement("td");
        const assessment = src.source_type || {};
        type.textContent = `${sourceTypeCode(assessment)} · ${assessment.label || "Source type uncertain"}`;
        type.title = `${assessment.confidence || "low"} confidence · ${(assessment.signals || []).join("; ") || "no high-precision design signal"}`;

        const availability = document.createElement("td");
        const status = src.library_status || {};
        availability.textContent = status.label || status.display || "Unresolved";
        row.append(rank, paper, reason);
        if (includeDetails) {
          row.appendChild(buildPaperQuestionDetails(src, requestedDetailFields));
        }
        row.append(type, availability);
        body.appendChild(row);
      });
      table.appendChild(body);
      wrapper.appendChild(table);
      return wrapper;
    }

    function buildPaperQuestionDetails(source, requestedDetailFields) {
      const cell = document.createElement("td");
      cell.className = "paper-table-details";
      const fields = source.question_fields || {};
      const fieldStatuses = source.question_field_statuses || {};
      const fieldLabels = {
        clinical_phenotype: "Phenotype",
        parasite_feature: "Parasite feature",
        direction_of_association: "Direction",
        cohort: "Cohort",
        assay: "Assay",
        developmental_stage_adjustment: "Stage adjusted"
      };
      const names = (requestedDetailFields.length
        ? requestedDetailFields
        : Object.keys(fields)
      ).filter((name) => Object.hasOwn(fieldLabels, name));
      if (!names.length || !Object.keys(fields).length) {
        const unavailable = document.createElement("span");
        unavailable.className = "paper-table-meta";
        unavailable.textContent = "Requested details were not extracted from the supplied evidence.";
        cell.appendChild(unavailable);
        return cell;
      }
      const grid = document.createElement("div");
      grid.className = "paper-detail-grid";
      for (const name of names) {
        const row = document.createElement("div");
        row.className = "paper-detail-row";
        const label = document.createElement("span");
        label.className = "paper-detail-label";
        label.textContent = fieldLabels[name];
        const value = document.createElement("span");
        const notAssessed = fieldStatuses[name] === "not_assessed_context_budget";
        const text = fields[name] || (notAssessed
          ? "Not assessed because the annotation context budget was exceeded"
          : "not reported in supplied evidence");
        value.className = `paper-detail-value${text === "not reported in supplied evidence" ? " not-reported" : ""}`;
        value.textContent = text;
        value.title = fieldStatuses[name] === "model_extracted_unverified"
          ? "Model-extracted from supplied excerpts; not independently verified"
          : fieldStatuses[name] === "deterministic_excerpt_supported"
            ? "Deterministically supported by an explicit statement in the supplied excerpt"
            : notAssessed
              ? "Not assessed because the annotation context budget was exceeded"
            : "Not extracted from the supplied excerpts";
        row.append(label, value);
        grid.appendChild(row);
      }
      cell.appendChild(grid);
      const note = document.createElement("span");
      note.className = "paper-detail-note";
      note.textContent = source.question_fields_status === "model_extracted_unverified"
        ? "Model-extracted from retrieved excerpts; verify against the full paper."
        : source.question_fields_status === "deterministic_excerpt_supported"
          ? "Requested fields are supported by explicit statements in the retrieved excerpts."
        : source.question_fields_status === "not_assessed_context_budget"
          ? "Annotation was not assessed because its context budget was exceeded. This does not establish that the paper omits these details."
        : source.question_fields_status === "mixed_extraction"
          ? "Fields have mixed provenance; check each field for its assessment status."
          : "Extraction was incomplete; every unresolved field is marked not reported.";
      cell.appendChild(note);
      return cell;
    }

    function buildPaperSearchOriginSummary(assurance) {
      const panel = document.createElement("div");
      panel.className = "citation-trail";
      const families = assurance.publication_family_links || [];
      const shared = assurance.shared_origin_signals || [];
      const heading = document.createElement("strong");
      if (families.length) {
        heading.textContent = "Study overlap needs review";
        panel.appendChild(heading);
        const text = document.createElement("p");
        text.textContent = `${assurance.raw_paper_count || 0} retrieved reports represent at most ${assurance.publication_family_upper_bound || 0} publication families among the detected links; they must not be counted as independent studies. The exact independent-study count remains unresolved.`;
        panel.appendChild(text);
        appendOriginPairs(panel, families);
      } else if (shared.length) {
        heading.textContent = "Study overlap needs review";
        panel.appendChild(heading);
        const text = document.createElement("p");
        text.textContent = "These papers may reuse the same study or dataset. Independent studies remain unknown until the overlap has been reviewed.";
        panel.appendChild(text);
        appendOriginPairs(panel, shared);
      } else {
        heading.textContent = "Study overlap needs review";
        panel.appendChild(heading);
        const text = document.createElement("p");
        text.textContent = "The automatic screen found no explicit overlap, but the returned papers are not automatically treated as independent studies. Unnamed cohort or dataset reuse may remain.";
        panel.appendChild(text);
      }
      return panel;
    }

    function appendOriginPairs(panel, links) {
      const list = document.createElement("ul");
      for (const link of links.slice(0, 6)) {
        const item = document.createElement("li");
        item.textContent = `${link.label_a || link.paper_id_a} ↔ ${link.label_b || link.paper_id_b} · ${(link.relationship_types || []).join(", ")}`;
        list.appendChild(item);
      }
      panel.appendChild(list);
    }

    function formatElapsed(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value)) return "Duration unavailable";
      if (value < 60) return `${value.toFixed(1)} s`;
      const minutes = Math.floor(value / 60);
      return `${minutes} min ${Math.round(value - minutes * 60)} s`;
    }

    async function traceCitedSources(source, target, button) {
      button.disabled = true;
      button.textContent = "Tracing…";
      target.className = "citation-trail";
      target.textContent = "Linking citation markers to the reference list…";
      try {
        const response = await fetch("/api/source-trace", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paper_id: source.paper_id, claim_text: source.trace_claim_text }),
        });
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
        renderCitationTrail(data, target);
        button.textContent = "Trace refreshed";
      } catch (error) {
        target.textContent = `Citation trace unavailable: ${error.message || error}`;
        button.textContent = "Retry source trace";
      } finally {
        button.disabled = false;
      }
    }

    function renderCitationTrail(data, target) {
      target.innerHTML = "";
      const heading = document.createElement("strong");
      heading.textContent = "Excerpt-local cited-reference candidates";
      target.appendChild(heading);
      if (!(data.references || []).length) {
        const empty = document.createElement("p");
        empty.textContent = "No citation could be linked safely. This does not make the carrier paper the primary source.";
        target.appendChild(empty);
        return;
      }
      const list = document.createElement("ol");
      for (const ref of data.references) {
        const item = document.createElement("li");
        const status = ref.library_status || {};
        const citationHtml = ref.url
          ? `<a href="${escapeHtml(ref.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ref.citation || ref.title || "Unresolved reference")}</a>`
          : escapeHtml(ref.citation || ref.title || "Unresolved reference");
        item.innerHTML = `<span class="source-status ${escapeHtml(status.state || "")}">${escapeHtml(status.display || "? Source unresolved")}</span>${citationHtml}<div class="citation-context">${escapeHtml(sourceTypeLabel(ref.source_type))} · ${escapeHtml(directReportLabel(ref.direct_report_status))} · marker ${escapeHtml(ref.marker_text || "?")} · page ${escapeHtml(ref.source_page || "?")} · ${escapeHtml(ref.bibliographic_resolution_status || "unresolved")}</div>`;
        if (ref.can_queue) {
          const queueButton = document.createElement("button");
          queueButton.type = "button";
          queueButton.className = "source-action";
          queueButton.textContent = !ref.identifier_verified
            ? "Verify & add to OA queue"
            : "Add to OA download queue";
          queueButton.addEventListener("click", () => queueCitedSource(ref, queueButton));
          item.appendChild(queueButton);
        }
        list.appendChild(item);
      }
      target.appendChild(list);
      const policy = document.createElement("p");
      policy.textContent = "Citation identity is separate from semantic support and dataset origin; review-required edges are not counted as independent evidence.";
      target.appendChild(policy);
    }

    async function queueCitedSource(reference, button) {
      if (!window.confirm("Verify this source and, only if its identifier matches exactly, add it to the open-access download queue? No PDF is downloaded yet.")) return;
      button.disabled = true;
      try {
        const response = await fetch("/api/source-request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reference }),
        });
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
        button.textContent = (data.added || []).length ? "Queued" : "Already queued";
      } catch (error) {
        button.textContent = `Queue failed: ${error.message || error}`;
        button.disabled = false;
      }
    }

    function sourceIdentifiers(src) {
      const bits = [];
      if (src.doi) {
        const doiUrl = String(src.doi).startsWith("http") ? src.doi : `https://doi.org/${src.doi}`;
        bits.push(`<a href="${escapeHtml(doiUrl)}" target="_blank" rel="noopener noreferrer">DOI: ${escapeHtml(src.doi)}</a>`);
      }
      if (src.pmid) {
        bits.push(`<a href="https://pubmed.ncbi.nlm.nih.gov/${escapeHtml(src.pmid)}/" target="_blank" rel="noopener noreferrer">PMID: ${escapeHtml(src.pmid)}</a>`);
      }
      if (src.pmcid) {
        const pmcid = String(src.pmcid).toUpperCase().startsWith("PMC") ? src.pmcid : `PMC${src.pmcid}`;
        bits.push(`<a href="https://www.ncbi.nlm.nih.gov/pmc/articles/${escapeHtml(pmcid)}/" target="_blank" rel="noopener noreferrer">PMCID: ${escapeHtml(pmcid)}</a>`);
      }
      return bits.length ? ` <span class="source-identifiers">${bits.join(" · ")}</span>` : "";
    }

    function buildSourceIdentifierLine(src) {
      const identifiers = [
        src.doi ? {
          label: `DOI: ${src.doi}`,
          url: String(src.doi).startsWith("http") ? src.doi : `https://doi.org/${src.doi}`,
        } : null,
        src.pmid ? {
          label: `PMID: ${src.pmid}`,
          url: `https://pubmed.ncbi.nlm.nih.gov/${src.pmid}/`,
        } : null,
        src.pmcid ? {
          label: `PMCID: ${src.pmcid}`,
          url: `https://www.ncbi.nlm.nih.gov/pmc/articles/${String(src.pmcid).toUpperCase().startsWith("PMC") ? src.pmcid : `PMC${src.pmcid}`}/`,
        } : null,
      ].filter(Boolean);
      if (!identifiers.length) return null;
      const line = document.createElement("span");
      line.className = "source-identifiers";
      identifiers.forEach((identifier, index) => {
        if (index) line.appendChild(document.createTextNode(" · "));
        const link = document.createElement("a");
        link.href = identifier.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = identifier.label;
        line.appendChild(link);
      });
      return line;
    }

    function showHero() {
      chat.innerHTML = heroTemplate;
      hero = document.getElementById("hero");
    }

    function setSidebarOpen(open) {
      document.body.classList.toggle("sidebar-open", open);
      sidebarToggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function setSidebarCollapsed(collapsed) {
      document.body.classList.toggle("sidebar-collapsed", collapsed);
      window.localStorage.setItem(sidebarCollapsedKey, collapsed ? "1" : "0");
      sidebarToggle.title = collapsed ? "Show sidebar" : "Open chats";
      sidebarToggle.setAttribute("aria-label", collapsed ? "Show sidebar" : "Open chats");
    }

    function setSidebarWidth(width) {
      const bounded = Math.max(220, Math.min(460, Number(width) || 296));
      document.documentElement.style.setProperty("--sidebar-width", `${bounded}px`);
      window.localStorage.setItem(sidebarWidthKey, String(bounded));
    }

    sidebarResizer.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 820px)").matches) return;
      event.preventDefault();
      sidebarResizer.setPointerCapture(event.pointerId);
      document.body.classList.add("sidebar-resizing");
    });
    sidebarResizer.addEventListener("pointermove", (event) => {
      if (!sidebarResizer.hasPointerCapture(event.pointerId)) return;
      setSidebarWidth(event.clientX);
    });
    sidebarResizer.addEventListener("pointerup", (event) => {
      if (sidebarResizer.hasPointerCapture(event.pointerId)) sidebarResizer.releasePointerCapture(event.pointerId);
      document.body.classList.remove("sidebar-resizing");
    });
    sidebarResizer.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"), 10) || 296;
      setSidebarWidth(current + (event.key === 'ArrowRight' ? 16 : -16));
    });

    function escapeHtml(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submittingTurn || libraryBusy || sessionNavigationPending) return;
      const text = message.value.trim();
      if (!text) return;
      const submittedSession = currentSessionDir;
      cancelSubmittedTurn = false;
      addMessage("user", text);
      message.value = "";
      resizeComposer();
      setSubmitting(true);
      const pendingSubmission = addJobMessage({
        job_id: `submission-${Date.now()}`, state: "submitting", progress: 0,
        stage: "Submitting question · waiting for the server to confirm receipt"
      });
      if (queueSummary.textContent === "Queue idle") queueSummary.textContent = "Submitting question";
      try {
        const {requestJSON} = await import('/assets/workspace/request.mjs');
        const data = await requestJSON("/api/chat", {
          timeout: 60000,
          payload: {
            message: text,
            settings: settings(),
            session_dir: submittedSession
          }
        });
        knownJobStates.set(data.job_id, data.state);
        if (data.session_dir === currentSessionDir) {
          addJobMessage(data);
        }
        if (cancelSubmittedTurn) await cancelTurnJob(data.job_id);
        else statusEl.textContent = data.state === "queued" ? "Question queued." : "Question started.";
        loadSessions();
        pollJobs(true);
      } catch (error) {
        if (submittedSession === currentSessionDir) {
          if (!message.value.trim()) { message.value = text; resizeComposer(); }
          const detail = error.retryable ? ' Check the queue before sending again; the server may already have received your question.' : '';
          addMessage("assistant", `${error.message}${detail}`);
          statusEl.textContent = 'Your question has been kept in the input field.';
        }
        pollJobs(true);
      } finally {
        pendingSubmission.remove();
        setSubmitting(false);
        if (queueSummary.textContent === "Submitting question") queueSummary.textContent = "Queue idle";
        if (submittedSession === currentSessionDir && [message, send, document.body].includes(document.activeElement)) message.focus();
        if (sessionReconcilePending) scheduleVisibleSessionReconciliation();
      }
    });

    message.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !submittingTurn) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    message.addEventListener("input", () => {
      resizeComposer();
      if (sessionReconcileTimer !== null) {
        window.clearTimeout(sessionReconcileTimer);
        sessionReconcileTimer = null;
        sessionReconcilePending = true;
      }
    });
    message.addEventListener("blur", () => {
      if (sessionReconcilePending) scheduleVisibleSessionReconciliation();
    });

    function resizeComposer() {
      message.style.height = "auto";
      message.style.height = `${Math.min(message.scrollHeight, 170)}px`;
    }

    newChat.addEventListener("click", async () => {
      const previousSession = currentSessionDir;
      const loadSequence = ++sessionLoadSequence;
      currentSessionDir = "";
      setSessionNavigationPending(true);
      try {
        const response = await fetch("/api/reset", {method: "POST"});
        const data = await response.json();
        if (loadSequence !== sessionLoadSequence) return;
        if (!response.ok) {
          currentSessionDir = previousSession;
          setSessionNavigationPending(false);
          statusEl.textContent = data.error || "Could not create a new chat.";
          return;
        }
        currentSessionDir = data.session_dir || "";
        if (currentSessionDir) {
          window.sessionStorage.setItem(currentSessionStorageKey, currentSessionDir);
        }
        setSessionNavigationPending(false);
        showHero();
        statusEl.textContent = "New chat ready.";
        loadSessions();
        setSidebarOpen(false);
      } catch (error) {
        if (loadSequence === sessionLoadSequence) {
          currentSessionDir = previousSession;
          setSessionNavigationPending(false);
          statusEl.textContent = `New chat failed: ${error.message}`;
        }
      }
    });

    sidebarClose.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 820px)").matches) setSidebarOpen(false);
      else setSidebarCollapsed(true);
    });
    sidebarToggle.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 820px)").matches) {
        setSidebarOpen(!document.body.classList.contains("sidebar-open"));
      } else {
        setSidebarCollapsed(false);
      }
    });
    sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeSessionMenus();
        setSidebarOpen(false);
        if (profileBackdrop.classList.contains("open")) closeProfile();
        if (paperUploadBackdrop.classList.contains("open")) closePaperUpload();
        if (libraryBrowserBackdrop.classList.contains("open")) closeLibraryBrowser();
        if (queueBackdrop.classList.contains("open")) closeQueueDetails();
        closeLibraryStats();
      }
    });

    function setSessionsCollapsed(collapsed) {
      sessionsSection.classList.toggle("collapsed", collapsed);
      sessionsToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      window.localStorage.setItem(sessionsCollapsedKey, collapsed ? "1" : "0");
    }

    sessionsToggle.addEventListener("click", () => {
      setSessionsCollapsed(!sessionsSection.classList.contains("collapsed"));
    });

    function updateQueueSummary(queue = {}) {
      const queued = Number(queue.queued || 0);
      const running = Number(queue.running || 0);
      queueSummary.classList.toggle("busy", queued + running > 0);
      queueSummary.textContent = queued || running
        ? `Queue · ${running} running · ${queued} waiting`
        : (submittingTurn ? "Submitting question" : "Queue idle");
      queueSummary.title = "View who is currently in the queue.";
    }

    function closeQueueDetails() {
      queueBackdrop.classList.remove("open");
      queueBackdrop.setAttribute("aria-hidden", "true");
      queueSummary.setAttribute("aria-expanded", "false");
      queueSummary.focus();
    }

    function queueDetailRow(job) {
      const row = document.createElement("article");
      row.className = "queue-detail-row";
      const meta = document.createElement("div");
      meta.className = "queue-detail-meta";
      const owner = document.createElement("strong");
      owner.className = "queue-detail-owner";
      owner.textContent = job.owner_display_name || "Unknown user";
      if (job.is_current_user) owner.textContent += " (you)";
      if (job.owner_username && job.owner_username !== job.owner_display_name) {
        const username = document.createElement("span");
        username.textContent = ` · @${job.owner_username}`;
        owner.appendChild(username);
      }
      const state = document.createElement("span");
      const position = job.state === "queued" && job.queue_position
        ? ` · position ${job.queue_position}`
        : "";
      const stage = job.stage ? ` · ${job.stage}` : "";
      state.textContent = `${job.state || "active"}${position}${stage}`;
      meta.append(owner, state);
      row.appendChild(meta);
      return row;
    }

    async function loadQueueDetails() {
      queueRefresh.disabled = true;
      queueScopeNote.textContent = "Loading active jobs…";
      queueDetailList.replaceChildren();
      try {
        const response = await fetch("/api/jobs?details=1");
        if (!response.ok) throw new Error("Queue details unavailable");
        const data = await response.json();
        updateQueueSummary(data.queue || {});
        queueScopeNote.textContent = data.queue_scope === "global"
          ? "Active jobs across all users · questions stay private"
          : "Your active jobs · questions stay private";
        const jobs = data.queue_jobs || [];
        if (!jobs.length) {
          const empty = document.createElement("p");
          empty.className = "queue-detail-empty";
          empty.textContent = "No active jobs in this view.";
          queueDetailList.appendChild(empty);
        } else {
          for (const job of jobs) queueDetailList.appendChild(queueDetailRow(job));
        }
      } catch (error) {
        queueScopeNote.textContent = "Could not load queue details.";
        const empty = document.createElement("p");
        empty.className = "queue-detail-empty";
        empty.textContent = error.message || String(error);
        queueDetailList.appendChild(empty);
      } finally {
        queueRefresh.disabled = false;
      }
    }

    function openQueueDetails() {
      queueBackdrop.classList.add("open");
      queueBackdrop.setAttribute("aria-hidden", "false");
      queueSummary.setAttribute("aria-expanded", "true");
      queueClose.focus();
      loadQueueDetails();
    }

    async function pollJobs(force = false) {
      if (jobPollBusy || (!force && Date.now() < nextJobPollAt)) return;
      jobPollBusy = true;
      try {
        const {requestJSON} = await import('/assets/workspace/request.mjs');
        const data = await requestJSON('/api/jobs', {timeout: 15000});
        const jobs = data.jobs || [];
        jobPollFailures = 0;
        const active = jobs.some(job => ['queued', 'running', 'cancelling'].includes(job.state));
        nextJobPollAt = Date.now() + (document.hidden ? 15000 : active || submittingTurn ? 900 : 5000);
        connectionIndicator.classList.add('online');
        connectionIndicator.title = 'Online'; connectionIndicator.setAttribute('aria-label', 'Online');
        for (const job of jobs) rememberTurnJob(job);
        const present = new Set(jobs.map(job => job.job_id));
        for (const id of activeTurnJobs.keys()) if (!present.has(id) && !cancelRequests.has(id)) activeTurnJobs.delete(id);
        updateComposerStop();
        updateQueueSummary(data.queue || {});
        let refreshCurrent = false;
        for (const job of jobs) {
          const previous = knownJobStates.get(job.job_id);
          const wrap = document.querySelector(`[data-job-id="${job.job_id}"]`);
          if (wrap) updateJobMessage(wrap, job);
          if (
            job.session_dir === currentSessionDir
            && !previous
            && !wrap
          ) {


            refreshCurrent = true;
          }
          if (
            job.session_dir === currentSessionDir
            && previous
            && previous !== "complete"
            && job.state === "complete"
          ) {
            refreshCurrent = true;
          }
        }
        const signature = jobs
          .map((job) => `${job.job_id}:${job.state}:${job.queue_position || 0}`)
          .join("|");
        knownJobStates = new Map(jobs.map((job) => [job.job_id, job.state]));
        if (signature !== lastJobSignature) {
          lastJobSignature = signature;
          loadSessions();
        }
        if (refreshCurrent && currentSessionDir) {
          sessionReconcilePending = true;
          scheduleVisibleSessionReconciliation();
        }
      } catch (error) {
        nextJobPollAt = Date.now() + Math.min(30000, 1500 * 2 ** Math.min(jobPollFailures++, 4));
        connectionIndicator.classList.remove('online');
        connectionIndicator.title = 'Connection interrupted · reconnecting';
        connectionIndicator.setAttribute('aria-label', 'Connection interrupted · reconnecting');
        queueSummary.textContent = error.status === 401 ? 'Sign in to reconnect' : 'Reconnecting…';
        queueSummary.title = error.status === 401 ? error.message : 'Live progress is temporarily unavailable. Accepted jobs may still be running.';
      } finally {
        jobPollBusy = false;
      }
    }

    function shouldDeferSessionReconciliation() {
      return Boolean(
        submittingTurn
        || sessionNavigationPending
        || (document.activeElement === message && message.value.trim())
      );
    }

    function scheduleVisibleSessionReconciliation() {
      if (
        document.visibilityState === "hidden"
        || !currentSessionDir
      ) return;
      if (shouldDeferSessionReconciliation()) {
        sessionReconcilePending = true;
        return;
      }
      const requestedSession = currentSessionDir;
      window.clearTimeout(sessionReconcileTimer);
      sessionReconcileTimer = window.setTimeout(async () => {
        sessionReconcileTimer = null;
        if (
          !requestedSession
          || requestedSession !== currentSessionDir
          || shouldDeferSessionReconciliation()
        ) {
          sessionReconcilePending = true;
          return;
        }
        sessionReconcilePending = false;




        await loadSession({session_dir: requestedSession, title: "chat"});
        pollJobs();
      }, 250);
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        pollJobs(true);
        scheduleVisibleSessionReconciliation();
      }
    });
    window.addEventListener('online', () => pollJobs(true));
    window.addEventListener("focus", scheduleVisibleSessionReconciliation);

    async function loadSessions() {
      const requestSequence = ++sessionsLoadSequence;
      try {
        closeSessionMenus();
        document.querySelectorAll("body > .session-menu").forEach((menu) => menu.remove());
        const response = await fetch("/api/sessions");
        const data = await response.json();
        if (requestSequence !== sessionsLoadSequence) return;
        const sessions = data.sessions || [];
        sessionsEl.innerHTML = "";
        if (!sessions.length) {
          sessionsEl.innerHTML = '<div class="empty-sessions">No saved chats yet.</div>';
          return;
        }
        for (const session of sessions) {
          const isCurrent = session.session_dir === currentSessionDir;
          const row = document.createElement("div");
          row.className = `session-row${isCurrent ? " current" : ""}`;
          const jobIndicator = document.createElement("span");
          jobIndicator.className = `session-job-indicator ${session.job_state || ""}`.trim();
          if (session.job_state) {
            const description = session.job_state === "queued" && session.queue_position
              ? `Queued chat, position ${session.queue_position}`
              : `${session.job_state} chat`;
            jobIndicator.title = description;
            jobIndicator.setAttribute("aria-label", description);
          } else {
            jobIndicator.setAttribute("aria-hidden", "true");
          }
          const link = document.createElement("a");
          link.className = "session-link";
          link.href = "#";
          link.textContent = session.title || "Saved chat";
          link.addEventListener("click", (event) => {
            event.preventDefault();
            loadSession(session);
          });
          const menuButton = document.createElement("button");
          menuButton.className = "session-menu-button";
          menuButton.type = "button";
          menuButton.title = "Chat options";
          menuButton.setAttribute("aria-label", "Chat options");
          menuButton.setAttribute("aria-expanded", "false");
          menuButton.textContent = "...";
          menuButton.addEventListener("click", (event) => {
            event.stopPropagation();
            toggleSessionMenu(row, menuButton, menu);
          });
          const menu = document.createElement("div");
          menu.className = "session-menu";
          const rename = document.createElement("button");
          rename.className = "session-menu-item";
          rename.type = "button";
          rename.textContent = "Rename";
          rename.addEventListener("click", (event) => {
            event.stopPropagation();
            closeSessionMenus();
            renameSession(session);
          });
          const remove = document.createElement("button");
          remove.className = "session-menu-item danger";
          remove.type = "button";
          remove.textContent = "Delete";
          remove.disabled = ["queued", "running", "cancelling"].includes(session.job_state);
          if (remove.disabled) remove.title = "Wait for this chat's job to finish before deleting it.";
          remove.addEventListener("click", (event) => {
            event.stopPropagation();
            closeSessionMenus();
            deleteSession(session);
          });
          menu.appendChild(rename);
          menu.appendChild(remove);
          row.appendChild(jobIndicator);
          row.appendChild(link);
          row.appendChild(menuButton);
          sessionsEl.appendChild(row);
          document.body.appendChild(menu);
        }
      } catch {
        if (requestSequence === sessionsLoadSequence) {
          sessionsEl.innerHTML = '<div class="empty-sessions">Could not load chats.</div>';
        }
      }
    }

    function closeSessionMenus() {
      document.querySelectorAll(".session-row.menu-open").forEach((row) => {
        row.classList.remove("menu-open");
        const button = row.querySelector(".session-menu-button");
        if (button) button.setAttribute("aria-expanded", "false");
      });
      document.querySelectorAll(".session-menu.open").forEach((menu) => {
        menu.classList.remove("open");
      });
    }

    function toggleSessionMenu(row, button, menu) {
      const wasOpen = menu.classList.contains("open");
      closeSessionMenus();
      if (!wasOpen) {
        row.classList.add("menu-open");
        button.setAttribute("aria-expanded", "true");
        menu.classList.add("open");
        const anchor = button.getBoundingClientRect();
        const menuWidth = menu.offsetWidth;
        const menuHeight = menu.offsetHeight;
        const left = Math.max(8, Math.min(anchor.right - menuWidth, window.innerWidth - menuWidth - 8));
        const roomBelow = window.innerHeight - anchor.bottom;
        const top = roomBelow >= menuHeight + 8
          ? anchor.bottom + 4
          : Math.max(8, anchor.top - menuHeight - 4);
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
      }
    }

    async function loadSession(session) {
      const previousSession = currentSessionDir;
      const requestedSession = session.session_dir;
      const loadSequence = ++sessionLoadSequence;
      window.clearTimeout(sessionReconcileTimer);
      sessionReconcileTimer = null;
      sessionReconcilePending = false;
      currentSessionDir = requestedSession;
      setSessionNavigationPending(true);
      try {
        statusEl.textContent = "Loading chat...";
        const response = await fetch(`/api/session?session_dir=${encodeURIComponent(requestedSession)}`);
        const data = await response.json();
        if (loadSequence !== sessionLoadSequence || currentSessionDir !== requestedSession) return;
        if (!response.ok) {
          const error = new Error(data.error || "Could not load chat.");
          error.httpStatus = response.status;
          throw error;
        }
        currentSessionDir = data.session_dir || requestedSession;
        window.sessionStorage.setItem(currentSessionStorageKey, currentSessionDir);
        setSessionNavigationPending(false);
        chat.innerHTML = "";
        hero = null;
        const messages = data.messages || [];
        if (!messages.length) {
          showHero();
        } else {
          for (const item of messages) {
            const role = item.role === "assistant" ? "assistant" : "user";
            const extra = role === "assistant" && item.result ? resultMeta(item.result) : null;
            addMessage(role, item.text || "", extra, item.result || null);
          }
        }
        for (const job of data.jobs || []) {
          knownJobStates.set(job.job_id, job.state);
          addJobMessage(job, true);
        }
        const activeJobs = (data.jobs || []).filter((job) => ["queued", "running", "cancelling"].includes(job.state));
        statusEl.textContent = activeJobs.length
          ? `${activeJobs.length} ${activeJobs.length === 1 ? "job is" : "jobs are"} still active in this chat.`
          : "";
        loadSessions();
      } catch (error) {
        if (loadSequence === sessionLoadSequence && currentSessionDir === requestedSession) {
          if (error.httpStatus === 404) {
            const preservedDraft = message.value;
            try {
              const statusResponse = await fetch("/api/status");
              const statusData = await statusResponse.json();
              if (!statusResponse.ok) throw new Error(statusData.error || "status unavailable");
              let replacementSession = statusData.session_dir || "";
              if (!replacementSession || replacementSession === requestedSession) {
                const resetResponse = await fetch("/api/reset", {method: "POST"});
                const resetData = await resetResponse.json();
                if (!resetResponse.ok) throw new Error(resetData.error || "reset failed");
                replacementSession = resetData.session_dir || "";
              }
              if (loadSequence !== sessionLoadSequence || !replacementSession) return;
              currentSessionDir = "";
              setSessionNavigationPending(false);
              await loadSession({session_dir: replacementSession, title: "chat"});
              if (preservedDraft.trim()) {
                statusEl.textContent = (
                  "The open chat was deleted in another tab. Your draft was kept; "
                  + "review it before sending."
                );
              } else {
                statusEl.textContent = "The open chat was deleted in another tab.";
              }
              return;
            } catch (recoveryError) {
              currentSessionDir = "";
              window.sessionStorage.removeItem(currentSessionStorageKey);
              setSessionNavigationPending(false);
              showHero();
              statusEl.textContent = `Chat recovery failed: ${recoveryError.message}`;
              loadSessions();
              return;
            }
          }
          currentSessionDir = previousSession;
          setSessionNavigationPending(false);
          statusEl.textContent = `Load failed: ${error.message}`;
          loadSessions();
        }
      }
    }

    async function renameSession(session) {
      const currentTitle = session.title || "New chat";
      const title = window.prompt("Rename chat", currentTitle);
      if (title === null) return;
      const cleanTitle = title.trim();
      if (!cleanTitle) return;
      try {
        const response = await fetch("/api/session-title", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_dir: session.session_dir, title: cleanTitle})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Rename failed.");
        loadSessions();
      } catch (error) {
        statusEl.textContent = `Rename failed: ${error.message}`;
      }
    }

    async function deleteSession(session) {
      const title = session.title || "this chat";
      if (!window.confirm(`Delete "${title}"?`)) return;
      const deletingCurrent = session.session_dir === currentSessionDir;
      try {
        const response = await fetch("/api/session-delete", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_dir: session.session_dir})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Delete failed.");
        if (deletingCurrent) {
          sessionLoadSequence += 1;
          setSessionNavigationPending(false);
          currentSessionDir = "";
          const replacementSession = data.session_dir || "";
          if (replacementSession) {
            await loadSession({session_dir: replacementSession, title: "chat"});
          } else {
            window.sessionStorage.removeItem(currentSessionStorageKey);
            showHero();
          }
        }
        statusEl.textContent = "Chat deleted.";
        loadSessions();
      } catch (error) {
        statusEl.textContent = `Delete failed: ${error.message}`;
      }
    }

    function readProfileInstruction() {
      return (window.localStorage.getItem(profileStorageKey) || "").trim();
    }

    function openAbout() {
      aboutBackdrop.classList.add("open");
      aboutBackdrop.setAttribute("aria-hidden", "false");
      aboutButton.setAttribute("aria-expanded", "true");
      aboutClose.focus();
    }

    function closeAbout() {
      aboutBackdrop.classList.remove("open");
      aboutBackdrop.setAttribute("aria-hidden", "true");
      aboutButton.setAttribute("aria-expanded", "false");
      aboutButton.focus();
    }

    function openProfile() {
      profileText.value = readProfileInstruction();
      profileBackdrop.classList.add("open");
      profileBackdrop.setAttribute("aria-hidden", "false");
      profileText.focus();
    }

    function openPaperUpload() {
      paperUploadBackdrop.classList.add("open");
      paperUploadBackdrop.setAttribute("aria-hidden", "false");
      paperDropZone.focus();
    }

    function openLibraryBrowser() {
      libraryBrowserBackdrop.classList.add("open");
      libraryBrowserBackdrop.setAttribute("aria-hidden", "false");
      librarySearch.focus();
      loadLibraryPage(true);
    }

    function closeLibraryBrowser() {
      libraryRequestSequence += 1;
      window.clearTimeout(librarySearchTimer);
      libraryBrowserBackdrop.classList.remove("open");
      libraryBrowserBackdrop.setAttribute("aria-hidden", "true");
      libraryButton.focus();
    }

    function libraryPaperCard(paper) {
      const article = document.createElement("article");
      article.className = "library-paper";
      const title = document.createElement("div");
      title.className = "library-paper-title";
      title.textContent = paper.title || paper.paper_id || "Untitled paper";
      const metadata = document.createElement("div");
      metadata.className = "library-paper-meta";
      metadata.textContent = [paper.author, paper.year, paper.journal].filter(Boolean).join(" · ") || "Bibliographic details unavailable";
      const category = document.createElement("span");
      category.className = "library-paper-category";
      category.textContent = paper.falciparum_topic_label || paper.topic_label || libraryCategoryLabel(paper.category || paper.collection);
      const links = document.createElement("div");
      links.className = "library-paper-links";
      if (paper.doi_url) {
        const doi = document.createElement("a");
        doi.href = paper.doi_url;
        doi.target = "_blank";
        doi.rel = "noopener noreferrer";
        doi.textContent = `DOI: ${paper.doi}`;
        links.appendChild(doi);
      }
      const status = document.createElement("span");
      status.className = `library-paper-status ${paper.searchable ? "library-local" : "library-unavailable"}`;
      status.textContent = paper.searchable
        ? "● searchable full text"
        : (paper.local_pdf ? "○ PDF present · indexing pending" : "○ metadata only");
      links.appendChild(status);
      const summarize = document.createElement("button");
      summarize.type = "button";
      summarize.className = "ghost";
      summarize.textContent = "Summarize";
      summarize.disabled = !(paper.summary_available || paper.searchable);
      summarize.title = summarize.disabled ? "Add an accessible PDF and wait for indexing first" : "Read a short summary with source passages";
      summarize.addEventListener("click", () => {
        if (submittingTurn || message.value.trim()) {
          libraryResultCount.textContent = "Send or clear your current draft before starting a summary.";
          return;
        }
        closeLibraryBrowser();
        const prefix = "Summarize this paper: ";
        message.value = prefix + (paper.doi || paper.title);
        resizeComposer();
        form.requestSubmit();
      });
      links.appendChild(summarize);
      if (paper.paper_id && window.originragShelfButton) links.appendChild(window.originragShelfButton(paper));
      article.append(title, metadata, category, links);
      return article;
    }

    function libraryCategoryLabel(category) {
      const key = String(category || "uncategorized");
      return libraryCategoryLabels[key] || key.replaceAll("_", " ");
    }

    function closeLibraryStats() {
      libraryStatsPopover.hidden = true;
      libraryStatsButton.setAttribute("aria-expanded", "false");
    }

    function toggleLibraryStats() {
      const opening = libraryStatsPopover.hidden;
      libraryStatsPopover.hidden = !opening;
      libraryStatsButton.setAttribute("aria-expanded", String(opening));
    }

    async function loadLibraryStats() {
      try {
        const response = await fetch("/api/library-stats");
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Library statistics unavailable.");
        window.dispatchEvent(new CustomEvent("originrag:library-stats", {detail: data}));
        const total = Number(data.total || 0);
        const searchable = Number(data.searchable || 0);
        const localPdf = Number(data.local_pdf || 0);
        libraryStatsTotal.textContent = total.toLocaleString();
        libraryStatsDonutTotal.textContent = total.toLocaleString();
        libraryStatsCoverage.textContent = `${searchable.toLocaleString()} searchable · ${localPdf.toLocaleString()} local PDFs`;
        const selectedScope = libraryScope.value;
        libraryScope.replaceChildren();
        const allOption = document.createElement("option");
        allOption.value = "";
        allOption.textContent = "All papers";
        libraryScope.appendChild(allOption);
        const pfCount = Number(data.falciparum_scope_count || 0);
        const pfOption = document.createElement("option");
        pfOption.value = "p_falciparum";
        pfOption.textContent = `Plasmodium falciparum (${pfCount.toLocaleString()})`;
        libraryScope.appendChild(pfOption);
        for (const item of data.falciparum_topics || []) {
          const option = document.createElement("option");
          option.value = `pf:${item.topic}`;
          option.textContent = `${item.label} (${Number(item.count || 0).toLocaleString()})`;
          libraryScope.appendChild(option);
        }
        if ([...libraryScope.options].some((option) => option.value === selectedScope)) {
          libraryScope.value = selectedScope;
        }
        const colors = ["#46cfc1", "#7aa2f7", "#f2c879", "#ef8a80", "#b18ceb", "#ed91c4", "#95bf70", "#df9f61", "#7da6b5", "#657681"];
        const groups = (data.groups || []).filter((item) => Number(item.count || 0) > 0);
        let position = 0;
        const segments = [];
        libraryStatsGroups.replaceChildren();
        groups.forEach((item, index) => {
          const countValue = Number(item.count || 0);
          const start = position;
          position += total ? (countValue / total) * 100 : 0;
          const color = colors[index % colors.length];
          segments.push(`${color} ${start.toFixed(3)}% ${position.toFixed(3)}%`);
          const row = document.createElement("div");
          row.className = "library-stats-group";
          const swatch = document.createElement("span");
          swatch.className = "library-stats-swatch";
          swatch.style.background = color;
          const label = document.createElement("span");
          label.textContent = item.label || "Other";
          label.title = label.textContent;
          const count = document.createElement("strong");
          count.textContent = countValue.toLocaleString();
          row.append(swatch, label, count);
          libraryStatsGroups.appendChild(row);
        });
        libraryStatsDonut.style.background = segments.length
          ? `conic-gradient(${segments.join(", ")})`
          : "conic-gradient(var(--line-strong) 0 100%)";
        libraryStatsDonut.setAttribute(
          "aria-label",
          `${total.toLocaleString()} papers across ${groups.length} topic groups`
        );
        libraryStatsButton.title = `Library: ${total.toLocaleString()} papers`;
      } catch {
        libraryStatsTotal.textContent = "—";
        libraryStatsDonutTotal.textContent = "—";
        libraryStatsCoverage.textContent = "Could not load the current composition.";
        libraryStatsGroups.replaceChildren();
        libraryStatsDonut.style.background = "conic-gradient(var(--line-strong) 0 100%)";
      }
    }

    async function loadLibraryPage(reset = false) {
      if (libraryLoading && !reset) return;
      const sequence = ++libraryRequestSequence;
      if (reset) {
        libraryOffset = 0;
        libraryHasMore = false;
        libraryResults.replaceChildren();
        libraryResultCount.textContent = "Searching…";
      }
      libraryLoading = true;
      libraryLoadMore.disabled = true;
      try {
        const params = new URLSearchParams({
          query: librarySearch.value.trim(),
          scope: libraryScope.value,
          offset: String(reset ? 0 : libraryOffset),
          limit: "40"
        });
        const response = await fetch(`/api/library?${params}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Library search failed.");
        if (sequence !== libraryRequestSequence) return;
        if (reset) libraryResults.replaceChildren();
        for (const paper of data.items || []) libraryResults.appendChild(libraryPaperCard(paper));
        libraryOffset = Number(data.offset || 0) + (data.items || []).length;
        libraryHasMore = Boolean(data.has_more);
        libraryLoadMore.hidden = !libraryHasMore;
        const total = Number(data.total || 0);
        libraryResultCount.textContent = `${total.toLocaleString()} ${total === 1 ? "paper" : "papers"}`;
        if (!total) {
          const empty = document.createElement("div");
          empty.className = "library-empty";
          empty.textContent = "No matching papers.";
          libraryResults.appendChild(empty);
        }
      } catch (error) {
        if (sequence !== libraryRequestSequence) return;
        libraryResultCount.textContent = "Library unavailable";
        const empty = document.createElement("div");
        empty.className = "library-empty";
        empty.textContent = error.message || String(error);
        libraryResults.replaceChildren(empty);
      } finally {
        if (sequence === libraryRequestSequence) {
          libraryLoading = false;
          libraryLoadMore.disabled = false;
        }
      }
    }

    function closePaperUpload() {
      paperUploadBackdrop.classList.remove("open");
      paperUploadBackdrop.setAttribute("aria-hidden", "true");
      paperUploadButton.focus();
    }

    function uploadListItem(filename, status) {
      const row = document.createElement("div");
      row.className = "paper-upload-item";
      const name = document.createElement("span");
      name.textContent = filename;
      const state = document.createElement("strong");
      state.textContent = status;
      row.append(name, state);
      paperUploadList.appendChild(row);
      return state;
    }

    async function uploadPapers(fileList) {
      const files = [...fileList].filter((file) => file.name.toLowerCase().endsWith(".pdf"));
      if (!files.length) {
        statusEl.textContent = "Choose one or more PDF files.";
        return;
      }
      const suppliedDoi = files.length === 1 ? paperDoi.value.trim() : "";
      for (const file of files) {
        const state = uploadListItem(file.name, "Uploading…");
        try {
          const response = await fetch("/api/library-upload", {
            method: "POST",
            headers: {
              "Content-Type": "application/pdf",
              "X-Paper-Filename": encodeURIComponent(file.name),
              "X-Paper-Defer-Sync": "1",
              ...(suppliedDoi ? {"X-Paper-DOI": suppliedDoi} : {})
            },
            body: file
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Upload failed.");
          state.textContent = "Queued";
        } catch (error) {
          state.textContent = `Failed: ${error.message || error}`;
        }
      }
      try {
        await fetch("/api/library-sync", {method: "POST"});
      } catch {

      }
      paperDoi.value = "";
      paperFileInput.value = "";
      pollLibraryStatus();
    }

    function renderLibraryStatus(data) {
      const state = String(data.state || "idle");
      libraryBusy = state === "queued" || state === "running";
      const batchId = String(data.batch_id || "");
      if (state === "complete" && batchId && batchId !== visibleLibraryBatch) {
        visibleLibraryBatch = batchId;
        libraryCompleteUntil = Date.now() + 10000;
        loadLibraryStats();
      }
      const showComplete = state === "complete" && Date.now() < libraryCompleteUntil;
      const show = libraryBusy || showComplete || state === "error" || Number(data.pending_files || 0) > 0;
      librarySyncStatus.classList.toggle("visible", show);
      librarySyncStatus.classList.toggle("complete", state === "complete");
      librarySyncStatus.classList.toggle("error", state === "error");
      const total = Number(data.total || data.scanned || 0);
      const processed = Number(data.processed || (state === "complete" ? total : 0));
      const bits = [];
      if (state === "complete") {
        librarySyncLabel.textContent = Number(data.imported || 0) ? "Papers are ready" : "Library checked";
      } else {
        librarySyncLabel.textContent = data.stage || "Updating library";
      }
      if (libraryBusy && total) bits.push(`${processed}/${total} checked`);
      if (Number(data.imported || 0)) bits.push(`${data.imported} added and searchable`);
      if (Number(data.duplicates || 0)) bits.push(`${data.duplicates} already present`);
      if (Number(data.review_required || 0)) bits.push(`${data.review_required} need review`);
      if (Number(data.rejected || 0)) bits.push(`${data.rejected} rejected`);
      if (data.error) bits.push(String(data.error));
      librarySyncCounts.textContent = bits.join(" · ");
      if (data.batch_id && paperUploadList.dataset.batchId !== String(data.batch_id)) {
        paperUploadList.dataset.batchId = String(data.batch_id);
        for (const result of data.results || []) {
          const labels = {
            accepted: "Added",
            duplicate: "Already present",
            review_required: "Needs review",
            rejected: "Rejected"
          };
          const state = uploadListItem(result.title || result.source_file || "Uploaded paper", labels[result.status] || result.status);
          if (result.reason) state.title = result.reason;
        }
      }
      send.disabled = submittingTurn || libraryBusy || sessionNavigationPending;
      message.disabled = libraryBusy || sessionNavigationPending;
    }

    async function pollLibraryStatus() {
      try {
        const response = await fetch("/api/library-status");
        if (!response.ok) return;
        renderLibraryStatus(await response.json());
      } catch {

      }
    }

    function closeProfile() {
      profileBackdrop.classList.remove("open");
      profileBackdrop.setAttribute("aria-hidden", "true");
      profileButton.focus();
    }

    aboutButton.addEventListener("click", openAbout);
    aboutClose.addEventListener("click", closeAbout);
    aboutBackdrop.addEventListener("click", (event) => {
      if (event.target === aboutBackdrop) closeAbout();
    });
    aboutBackdrop.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAbout();
      if (event.key === "Tab") {
        event.preventDefault();
        aboutClose.focus();
      }
    });
    profileButton.addEventListener("click", openProfile);
    profileClose.addEventListener("click", closeProfile);
    profileSave.addEventListener("click", () => {
      window.localStorage.setItem(profileStorageKey, profileText.value.trim());
      closeProfile();
      statusEl.textContent = "Profile saved.";
    });
    profileBackdrop.addEventListener("click", (event) => {
      if (event.target === profileBackdrop) closeProfile();
    });
    paperUploadButton.addEventListener("click", openPaperUpload);
    paperUploadClose.addEventListener("click", closePaperUpload);
    paperUploadBackdrop.addEventListener("click", (event) => {
      if (event.target === paperUploadBackdrop) closePaperUpload();
    });
    paperDropZone.addEventListener("click", () => paperFileInput.click());
    paperDropZone.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        paperFileInput.click();
      }
    });
    for (const eventName of ["dragenter", "dragover"]) {
      paperDropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        paperDropZone.classList.add("dragging");
      });
    }
    for (const eventName of ["dragleave", "drop"]) {
      paperDropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        paperDropZone.classList.remove("dragging");
      });
    }
    paperDropZone.addEventListener("drop", (event) => uploadPapers(event.dataTransfer.files));
    paperFileInput.addEventListener("change", () => uploadPapers(paperFileInput.files));
    libraryButton.addEventListener("click", openLibraryBrowser);
    libraryStatsButton.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleLibraryStats();
    });
    libraryStatsPopover.addEventListener("click", (event) => event.stopPropagation());
    libraryBrowserClose.addEventListener("click", closeLibraryBrowser);
    libraryBrowserBackdrop.addEventListener("click", (event) => {
      if (event.target === libraryBrowserBackdrop) closeLibraryBrowser();
    });
    librarySearch.addEventListener("input", () => {
      window.clearTimeout(librarySearchTimer);
      librarySearchTimer = window.setTimeout(() => {
        libraryResults.scrollTop = 0;
        loadLibraryPage(true);
      }, 140);
    });
    libraryScope.addEventListener("change", () => {
      libraryResults.scrollTop = 0;
      loadLibraryPage(true);
    });
    libraryLoadMore.addEventListener("click", () => loadLibraryPage(false));
    libraryResults.addEventListener("scroll", () => {
      const nearBottom = libraryResults.scrollTop + libraryResults.clientHeight >= libraryResults.scrollHeight - 180;
      if (nearBottom && libraryHasMore && !libraryLoading) loadLibraryPage(false);
    }, {passive: true});
    queueSummary.addEventListener("click", openQueueDetails);
    queueRefresh.addEventListener("click", loadQueueDetails);
    queueClose.addEventListener("click", closeQueueDetails);
    queueBackdrop.addEventListener("click", (event) => {
      if (event.target === queueBackdrop) closeQueueDetails();
    });
    queueBackdrop.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeQueueDetails();
    });
    studyAuditClose.addEventListener("click", closeStudyAudit);
    studyAuditBackdrop.addEventListener("click", (event) => {
      if (event.target === studyAuditBackdrop) closeStudyAudit();
    });
    studyAuditBackdrop.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeStudyAudit();
    });
    profileBackdrop.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const focusable = [profileText, profileClose, profileSave];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    document.addEventListener("click", () => {
      closeSessionMenus();
      closeLibraryStats();
    });
    window.addEventListener("resize", closeSessionMenus);
    sessionsEl.addEventListener("scroll", closeSessionMenus, {passive: true});

    async function loadStatus() {
      try {
        setSidebarWidth(window.localStorage.getItem(sidebarWidthKey) || 296);
        setSidebarCollapsed(window.localStorage.getItem(sidebarCollapsedKey) === "1");
        setSessionsCollapsed(window.localStorage.getItem(sessionsCollapsedKey) === "1");
        const response = await fetch("/api/status");
        if (!response.ok) throw new Error("status unavailable");
        const data = await response.json();
        connectionIndicator.classList.add("online");
        connectionIndicator.title = "Online";
        connectionIndicator.setAttribute("aria-label", "Online");
        const initializeSession = !currentSessionDir && !sessionNavigationPending;
        if (initializeSession) {
          currentSessionDir = (
            window.sessionStorage.getItem(currentSessionStorageKey)
            || data.session_dir
            || ""
          );
        }
        updateQueueSummary(data.queue || {});
        knownJobStates = new Map((data.jobs || []).map((job) => [job.job_id, job.state]));
        lastJobSignature = (data.jobs || [])
          .map((job) => `${job.job_id}:${job.state}:${job.queue_position || 0}`)
          .join("|");
        const s = data.settings || {};
        libraryButton.hidden = false;
        if (data.user) {
          userBadge.textContent = data.user.display_name;
          userBadge.hidden = false;
          logoutButton.hidden = false;
        }
        paperUploadButton.hidden = !data.library_access?.enabled;
        for (const key of ["backend", "preset"]) {
          const el = document.getElementById(key);
          if (el && s[key]) el.value = s[key];
        }
        if (s.evidence_selector) evidenceSelector.value = s.evidence_selector;
        if (document.getElementById("repair")) document.getElementById("repair").value = s.repair_attempts || 0;
        if (document.getElementById("fallback")) document.getElementById("fallback").checked = s.template_fallback !== false;
        resizeComposer();
        statusEl.textContent = "";
        loadSessions();
        if (
          currentSessionDir
          && (
            initializeSession
            || (data.jobs || []).some((job) => job.session_dir === currentSessionDir)
          )
        ) {
          await loadSession({session_dir: currentSessionDir, title: "chat"});
        }
        pollJobs();
        pollLibraryStatus();
        loadLibraryStats();
      } catch {
        connectionIndicator.classList.remove("online");
        connectionIndicator.title = "Offline";
        connectionIndicator.setAttribute("aria-label", "Offline");
        setSidebarWidth(window.localStorage.getItem(sidebarWidthKey) || 296);
        setSidebarCollapsed(window.localStorage.getItem(sidebarCollapsedKey) === "1");
        setSessionsCollapsed(window.localStorage.getItem(sessionsCollapsedKey) === "1");
        statusEl.textContent = "";
      }
    }
    window.addEventListener("offline", () => {
      connectionIndicator.classList.remove("online");
      connectionIndicator.title = "Offline";
      connectionIndicator.setAttribute("aria-label", "Offline");
    });
    window.addEventListener("online", loadStatus);
    window.setInterval(pollLibraryStatus, 900);
    window.setInterval(pollJobs, 900);
    loadStatus();
  </script>
</body>
</html>
"""


INDEX_HTML = with_navigation(with_onboarding(with_appearance(with_shelf(INDEX_HTML))))


if __name__ == "__main__":
    raise SystemExit(main())
