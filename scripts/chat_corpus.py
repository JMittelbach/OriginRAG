from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ask_corpus  # noqa: E402
import browse_library  # noqa: E402
from pfempire.analysis.chat import answer_context, build_chat_retrieval_question, resolve_chat_task  # noqa: E402
from pfempire.analysis.terminal_sessions import SessionStore, snapshot_result, visible_answer  # noqa: E402
from pfempire.utils.terminal_ui import TerminalUI  # noqa: E402


@dataclass
class ChatState:
    backend: str
    preset: str
    task: str
    repair_attempts: int
    template_fallback: bool
    chunks_dir: str
    papers: str
    category: str | None
    query_mode: str
    include_low_quality: bool
    exclude_tables: bool
    exclude_figures: bool
    exclude_supplement: bool
    allow_missing_doi_sources: bool
    limit: int | None
    candidate_multiplier: int | None
    max_per_paper: int | None
    max_chars_per_chunk: int | None
    min_score: float
    evidence_selector: str
    trace_lineage_graph: str | None
    trace_token_budget: int
    answer_timeout: int


def main() -> int:
    args = parse_args()
    ui = TerminalUI(plain=args.plain, animate=not args.no_animation)
    if args.demo:
        try:
            ui.preview()
        except KeyboardInterrupt:
            ui.message("Preview closed.")
        return 0
    state = ChatState(
        backend=args.backend,
        preset=args.preset,
        task=args.task,
        repair_attempts=args.repair_attempts,
        template_fallback=not args.no_template_fallback,
        chunks_dir=args.chunks_dir,
        papers=args.papers,
        category=args.category,
        query_mode=args.query_mode,
        include_low_quality=args.include_low_quality,
        exclude_tables=args.exclude_tables,
        exclude_figures=args.exclude_figures,
        exclude_supplement=args.exclude_supplement,
        allow_missing_doi_sources=args.allow_missing_doi_sources,
        limit=args.limit,
        candidate_multiplier=args.candidate_multiplier,
        max_per_paper=args.max_per_paper,
        max_chars_per_chunk=args.max_chars_per_chunk,
        min_score=args.min_score,
        evidence_selector=args.evidence_selector,
        trace_lineage_graph=args.trace_lineage_graph,
        trace_token_budget=args.trace_token_budget,
        answer_timeout=args.answer_timeout,
    )
    base = Path(args.out_dir).expanduser()
    store = SessionStore(base if base.is_absolute() else REPO_ROOT / base)
    try:
        if args.resume:
            state = restored_state(state, store.switch(args.resume).settings)
        else:
            store.create(state.__dict__)
        return run_chat(ui, store, state)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        ui.message(f"Chat could not be saved or opened: {exc}", error=True)
        return 2
    finally:
        store.close()


def run_chat(ui: TerminalUI, store: SessionStore, state: ChatState) -> int:
    session = store.current
    session_dir = session.path
    ui.welcome()
    ui.set_chat(session.id, session.title, [turn["question"] for turn in session.turns])
    ui.message(f"Chat: {session.title} · /chats to switch · /new for a new conversation")
    if session.turns:
        show_history(session, ui, last_only=True)
    if state.backend == "queue":
        ui.message("Using the local worker queue. /status shows your configuration.")

    previous_turns = session.contexts()
    last_result, last_turn_dir = session.last_result()
    turn_index = session.next_turn
    listed_chats = []
    while True:
        try:
            user_text = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            ui.message("\nGoodbye.")
            return 0
        if not user_text:
            continue
        if user_text.startswith("/"):
            name = user_text.split(maxsplit=1)[0].lower()
            if name in {"/chats", "/switch", "/new", "/rename", "/history"}:
                try:
                    value = " ".join(shlex.split(user_text)[1:])
                    target = None
                    if name == "/history":
                        show_history(session, ui)
                        continue
                    if name == "/rename":
                        if not value:
                            raise ValueError("Usage: /rename My chat title")
                        session.title, session.auto_title = value[:120], False
                        store.save()
                    elif name == "/new":
                        session = store.create(state.__dict__, value[:120])
                    elif name == "/chats":
                        listed_chats = store.list()
                        target = ui.choose_chat(listed_chats, session.id)
                        if not target:
                            continue
                    elif name == "/switch":
                        if not value:
                            raise ValueError("Usage: /switch CHAT_ID or /switch NUMBER from /chats")
                        if value.isdecimal():
                            listed_chats = listed_chats or store.list()
                            index = int(value) - 1
                            if not 0 <= index < len(listed_chats):
                                raise ValueError("No chat has that number; open /chats")
                            target = listed_chats[index]["id"]
                        else:
                            target = value
                    if target:

                        next_state = restored_state(state, store.read(target).settings)
                        session = store.switch(target)
                        state = next_state
                    session_dir = session.path
                    previous_turns = session.contexts()
                    last_result, last_turn_dir = session.last_result()
                    turn_index = session.next_turn
                    ui.set_chat(session.id, session.title, [turn["question"] for turn in session.turns])
                    ui.message(f"Chat: {session.title} · {session.id}")
                    if name != "/rename":
                        show_history(session, ui, last_only=True)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    ui.message(f"Could not change chat: {exc}", error=True)
                continue
            if user_text.split(maxsplit=1)[0].lower() == "/library":
                open_library(user_text, state, ui)
                continue
            if user_text.lower() in {"/sources", "/details"}:
                if last_result is None:
                    ui.message("Ask a question first.")
                elif user_text.lower() == "/sources":
                    ui.sources(list((last_result.get("pack") or {}).get("chunks", [])), limit=0)
                else:
                    print_result_details(last_result, last_turn_dir, ui)
                continue
            should_continue = handle_command(user_text, state, session_dir, ui=ui)
            session.settings = dict(state.__dict__)
            store.save()
            if not should_continue:
                return 0
            continue

        retrieval_question = build_chat_retrieval_question(user_text, previous_turns)
        resolved_task = resolve_chat_task(user_text, state.task,
                                          previous_turns[-1].resolved_task if previous_turns else None)
        turn_dir = session_dir / f"turn_{turn_index:03d}_{ask_corpus.slugify(user_text)}"
        if turn_dir.exists():
            raise ValueError("The next turn directory already exists; saved outputs will not be overwritten")
        turn_args = build_ask_args(
            state,
            user_text,
            resolved_task,
            retrieval_question=retrieval_question,
        )
        turn_args.conversation_context = answer_context(previous_turns)
        if session.auto_title:
            session.title, session.auto_title = " ".join(user_text.split())[:80], False
            ui.set_chat(session.id, session.title)
        record = {"question": user_text, "retrieval_question": retrieval_question,
                  "task": resolved_task, "directory": turn_dir.name, "status": "pending", "answer": ""}
        session.turns.append(record)
        session.next_turn = turn_index + 1
        store.save()
        try:
            with ui.waiting() as progress:
                result = ask_corpus.run_question(turn_args, turn_dir, progress_callback=progress)
        except KeyboardInterrupt:
            record["status"] = "interrupted"
            store.save()
            ui.message("Interrupted locally. An already submitted model call may still be finishing.")
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            record.update(status="error", error=str(exc))
            store.save()
            ui.message(f"The question could not be completed: {exc}", error=True)
            ui.message("Check ./originrag doctor and try again. The chat is still open.")
            turn_index += 1
            continue
        answer, status = visible_answer(result, session_dir)
        record.update(answer=answer, status=status, result=snapshot_result(result, session_dir))
        store.save()
        previous_turns = session.contexts()
        write_turn_summary(
            session_dir=session_dir,
            turn_index=turn_index,
            user_text=user_text,
            retrieval_question=retrieval_question,
            resolved_task=resolved_task,
            result=result,
            turn_dir=turn_dir,
        )
        print_turn_result(result, turn_dir, ui)
        last_result, last_turn_dir = result, turn_dir
        turn_index += 1


def restored_state(default: ChatState, saved: dict) -> ChatState:
    values = {item.name: saved[item.name] for item in fields(default) if item.name in saved}
    state = replace(default, **values)
    if state.backend not in {"template", "queue", "deepseek", "qwen", "ollama", "none"}:
        raise ValueError("Saved chat has an invalid backend")
    if state.task not in {"auto", "answer", "gaps", "hypothesis", "sources"} or state.preset not in ask_corpus.PRESETS:
        raise ValueError("Saved chat has invalid task/preset settings")
    if state.evidence_selector not in {"current", "trace_flow"}:
        raise ValueError("Saved chat has an invalid evidence selector")
    return state


def show_history(session, ui: TerminalUI, *, last_only: bool = False) -> None:
    turns = session.turns[-1:] if last_only else session.turns
    if not turns:
        ui.message("This chat is empty. Ask your first question.")
    for turn in turns:
        ui.message(f"You › {turn['question']}")
        if turn.get("answer"):
            label = {"partial": "Partial answer · review needed", "withheld": "More evidence needed"}.get(
                turn.get("status"), "OriginRAG")
            ui.answer(turn["answer"], title=label)
        else:
            ui.message(f"No saved answer ({turn.get('status', 'unavailable')}).")


def open_library(command: str, state: ChatState, ui: TerminalUI) -> None:
    """Browse without starting a model turn or changing conversation context."""
    try:
        arguments = shlex.split(command)[1:]
        papers = Path(state.papers).expanduser()
        if not papers.is_absolute():
            papers = REPO_ROOT / papers
        browse_library.main(["--papers", str(papers), *arguments], console=ui.console)
    except SystemExit:

        pass
    except KeyboardInterrupt:
        ui.message("Library closed")
    except (OSError, ValueError) as exc:
        ui.message(f"Could not open the library: {exc}", error=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive local OriginRAG chat over the active corpus.")
    parser.add_argument("--demo", action="store_true", help="Preview the interface without a model, corpus or saved session.")
    parser.add_argument("--plain", action="store_true", help="Use plain text without colour or animation.")
    parser.add_argument("--no-animation", action="store_true", help="Keep styled output but disable the loading animation.")
    parser.add_argument("--resume", help="Reopen a saved terminal chat by its ID (also available through /chats).")
    parser.add_argument(
        "--backend",
        choices=["template", "queue", "deepseek", "qwen", "ollama", "none"],
        default="qwen",
    )
    parser.add_argument("--preset", choices=sorted(ask_corpus.PRESETS), default="balanced")
    parser.add_argument(
        "--task",
        choices=["auto", "answer", "gaps", "hypothesis", "sources"],
        default="auto",
    )
    parser.add_argument("--repair-attempts", type=int, default=0)
    parser.add_argument("--no-template-fallback", action="store_true")
    parser.add_argument("--chunks-dir", default="data/chunks")
    parser.add_argument("--papers", default="data/metadata/papers.tsv")
    parser.add_argument("--out-dir", default="outputs/chat_sessions")
    parser.add_argument("--category")
    parser.add_argument("--query-mode", choices=["evidence", "methods", "background"], default="evidence")
    parser.add_argument("--include-low-quality", action="store_true")
    parser.add_argument("--exclude-tables", action="store_true")
    parser.add_argument("--exclude-figures", action="store_true")
    parser.add_argument("--exclude-supplement", action="store_true")
    parser.add_argument("--allow-missing-doi-sources", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--candidate-multiplier", type=int, default=None)
    parser.add_argument("--max-per-paper", type=int, default=None)
    parser.add_argument("--max-chars-per-chunk", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument(
        "--evidence-selector",
        choices=["current", "trace_flow"],
        default="trace_flow",
        help="Default to fail-closed scientific-origin auditing; current is the paper-count baseline.",
    )
    parser.add_argument("--trace-lineage-graph")
    parser.add_argument("--trace-token-budget", type=int, default=8000)
    parser.add_argument("--answer-timeout", type=int, default=600)
    return parser.parse_args()


def handle_command(command: str, state: ChatState, session_dir: Path,
                   *, ui: TerminalUI | None = None) -> bool:
    parts = command.split()
    name = parts[0].lower()
    value = parts[1].lower() if len(parts) > 1 else None
    if name in {"/exit", "/quit", "/q"}:
        print("bye")
        return False
    if name == "/help":
        print_chat_help(ui, topic=" ".join(parts[1:]).lower())
        return True
    if name == "/status":
        print(
            f"backend={state.backend}, preset={state.preset}, task={state.task}, "
            f"repair_attempts={state.repair_attempts}, template_fallback={state.template_fallback}, "
            f"evidence_selector={state.evidence_selector}"
        )
        print(f"session={relative_path(session_dir)}")
        return True
    if name == "/task" and value in {"auto", "answer", "gaps", "hypothesis", "sources"}:
        state.task = value
        print(f"task={state.task}")
        return True
    if name == "/backend" and value in {"template", "queue", "deepseek", "qwen", "ollama", "none"}:
        state.backend = value
        print(f"backend={state.backend}")
        return True
    if name == "/preset" and value in ask_corpus.PRESETS:
        state.preset = value
        print(f"preset={state.preset}")
        return True
    if name == "/selector" and value in {"current", "trace_flow"}:
        state.evidence_selector = value
        print(f"evidence_selector={state.evidence_selector}")
        return True
    if name == "/repair" and value is not None:
        try:
            state.repair_attempts = max(0, int(value))
        except ValueError:
            print(f"Invalid repair count: {value}")
            return True
        print(f"repair_attempts={state.repair_attempts}")
        return True
    print(f"Unknown command: {command}. Type /help for all commands.")
    return True


def print_chat_help(ui: TerminalUI | None = None, *, topic: str = "") -> None:
    ui = ui or TerminalUI()
    if topic not in {"", "library", "import", "advanced", "all"}:
        ui.message("Help topics: /help, /help library, /help import, /help advanced, /help all", error=True)
        return
    everyday = [
        ("Your papers", [
            ("/library", "Open the library and type to search"),
            ("/library Turner 2013", "Search by author, title or DOI; no model call"),
            ("/sources", "Selected papers from your last answer"),
            ("/help import", "Add papers and update your library"),
        ]),
        ("Your conversations", [
            ("/chats", "Browse and open saved terminal chats"),
            ("/new [title]", "Start a separate conversation"),
            ("/switch NUMBER|CHAT_ID", "Open a chat from the /chats list"),
            ("/rename title", "Rename this conversation"),
            ("/history", "Read this chat's previous questions and answers"),
        ]),
        ("Answers and controls", [
            ("/details", "Saved answer files and evidence checks"),
            ("/status", "Current settings and save location"),
            ("/help", "Show this guide"),
            ("/exit", "Leave chat; /quit and /q also work"),
        ]),
    ]
    library = [
        ("Find a paper", [
            ("/library", "Open the library and type to search"),
            ("/library Turner 2013", "Start with search words already entered"),
            ('/library --author "Turner"', "Filter by author"),
            ('/library --title "malaria"', "Filter by title"),
            ("/library --local-only", "Only papers with a PDF on this computer"),
            ("/library --help", "All library filters and options"),
        ]),
        ("Inside the library", [
            ("Type in Search", "Filter by author, title or DOI as you type"),
            ("Up/Down or Page Up/Down", "Browse the matching papers"),
            ("Ctrl-U", "Clear the search"),
            ("Esc or Exit", "Return to chat; /exit + Enter works too"),
            ("/help", "Show the library's keyboard guide"),
        ]),
    ]
    imports = [
        ("Add papers in the web app", [
            ("Add papers", "Sign in and upload PDFs while the service is running"),
            ("Automatic import", "Checks, names, sorts and indexes accepted papers"),
            ("Library updated", "New papers are searchable; no restart needed"),
        ]),
        ("Local files - run in your shell", [
            ("./originrag import-local --source-dir DIR", "Preview a PDF import"),
            ("./originrag import-local --source-dir DIR --apply", "Copy accepted papers into the library"),
            ("./originrag bib", "Refresh the library metadata after import"),
            ("./originrag prepare-corpus", "Extract text and rebuild the search index"),
        ]),
        ("Open-access discovery - run in your shell", [
            ("./originrag scrape", "Find candidate papers without downloading"),
            ("./originrag scrape-download 10", "Download up to 10 available OA papers and index them"),
            ("./originrag watch", "One discovery/metadata pass; no continuous watch or indexing"),
        ]),
    ]
    import_notes = (
        "Folders are not continuously monitored. Uncertain files need review.\n"
        "For shell imports, stop the web service first and restart it afterwards.\n"
        "Use Add papers for live updates. Recurring discovery needs an external scheduler."
    )
    advanced = [
        ("Answer settings", [
            ("/task MODE", "auto | answer | gaps | hypothesis | sources\n"
             "Auto chooses the task from your question"),
            ("/backend NAME", "qwen | ollama | queue | deepseek | template | none\n"
             "Choose a backend that is already configured"),
            ("/preset NAME", " | ".join(sorted(ask_corpus.PRESETS)) +
             "\nChoose retrieval and answer settings"),
            ("/selector NAME", "trace_flow | current\n"
             "Origin-aware selection or the legacy paper-count baseline"),
            ("/repair N", "Maximum answer-repair attempts; 0 disables repair"),
        ]),
        ("Shell options", [
            ("./terminal --help", "All startup options"),
            ("./terminal --resume CHAT_ID", "Reopen a saved terminal chat"),
            ("./terminal --no-animation", "Turn off motion"),
            ("./terminal --plain", "Use plain text"),
        ]),
    ]
    if topic == "library":
        sections, title = library, "OriginRAG / Library help"
        intro = "Find papers without asking the model"
        footer = "Browsing preserves your conversation and sources.\n/help returns to the chat guide."
    elif topic == "import":
        sections, title = imports, "OriginRAG / Add papers"
        intro = "Build and update your local library"
        footer = import_notes + "\n/help returns to the chat guide."
    elif topic == "advanced":
        sections, title = advanced, "OriginRAG / Advanced help"
        intro = "Optional settings for subsequent questions"
        footer = "Changing settings does not rerun earlier answers.\n/help returns to the chat guide."
    elif topic == "all":
        sections, title = everyday + library + imports + advanced, "OriginRAG / All commands"
        intro = "Your complete terminal reference"
        footer = (import_notes + "\n\n"
                  "Chats are saved locally; switching is available between questions.\n"
                  "The model uses limited recent context, not unlimited chat memory.\n"
                  "Ctrl-C or Ctrl-D at empty input leaves chat.")
    else:
        sections, title = everyday, "OriginRAG / Help"
        intro = "Just type a question to get started"
        footer = ("/help library   Search filters and keyboard shortcuts\n"
                  "/help import    Add papers and automatic web imports\n"
                  "/help advanced  Model settings and startup options\n"
                  "/help all       The complete command reference\n\n"
                  "Esc returns from the library. Up/Down recalls questions in the animated interface.\n"
                  "Chats are saved locally. Ctrl-C or Ctrl-D at empty input leaves chat.")
    ui.help(sections, title=title, intro=intro, footer=footer)


def build_ask_args(
    state: ChatState,
    question: str,
    task: str,
    retrieval_question: str | None = None,
) -> SimpleNamespace:
    evidence_selector = "current" if task == "sources" else state.evidence_selector
    return SimpleNamespace(
        question=question,
        answer_question=question,
        retrieval_question=retrieval_question or question,
        chunks_dir=state.chunks_dir,
        papers=state.papers,
        limit=state.limit,
        candidate_multiplier=state.candidate_multiplier,
        max_per_paper=state.max_per_paper,
        max_chars_per_chunk=state.max_chars_per_chunk,
        preset=state.preset,
        backend=state.backend,
        task=task,
        out_dir="",
        category=state.category,
        include_low_quality=state.include_low_quality,
        show_context=False,
        query_mode=state.query_mode,
        min_score=state.min_score,
        exclude_tables=state.exclude_tables,
        exclude_figures=state.exclude_figures,
        exclude_supplement=state.exclude_supplement,
        allow_missing_doi_sources=state.allow_missing_doi_sources,
        repair_attempts=state.repair_attempts,
        no_template_fallback=not state.template_fallback,
        answer_timeout=state.answer_timeout,
        task_mode="evidence_qa",
        evidence_selector=evidence_selector,
        trace_config="config/trace_rag.yaml",
        trace_blueprint=None,
        trace_lineage_graph=state.trace_lineage_graph,
        trace_token_budget=state.trace_token_budget,
        trace_selector_algorithm="lazy_greedy",
        show_trace_certificate=False,
    )


def write_turn_summary(
    session_dir: Path,
    turn_index: int,
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
                f"\n## Turn {turn_index}",
                f"User: {user_text}",
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


def print_turn_result(result: dict, turn_dir: Path, ui: TerminalUI | None = None) -> None:
    ui = ui or TerminalUI()

    for key, label in (("answer_path", "OriginRAG"),
                       ("partial_answer_path", "Partial answer · review needed"),
                       ("withheld_answer_path", "More evidence needed")):
        path = result.get(key)
        if path and Path(path).is_file():
            ui.answer(Path(path).read_text(encoding="utf-8").strip(), title=label)
            break
    else:
        ui.message("No final answer is available. /details shows the saved evidence and diagnostics.", error=True)
    ui.sources(list((result.get("pack") or {}).get("chunks", [])))
    ui.message("/sources · selected papers    /details · saved files and checks")


def print_result_details(result: dict, turn_dir: Path, ui: TerminalUI) -> None:
    qc = result.get("answer_qc") or {}
    attempts = ", ".join(str(item.get("kind")) for item in result.get("answer_attempts", []))
    ui.message(f"Response checks passed: {qc.get('passed')} | attempts: {attempts or 'none'}")
    ui.message(f"Saved files: {relative_path(turn_dir)}")
    evidence_table_path = result.get("evidence_table_tsv_path")
    if evidence_table_path:
        ui.message(f"Evidence table: {relative_path(evidence_table_path)}")


def append_session_line(session_dir: Path, text: str) -> None:
    with (session_dir / "session.md").open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
