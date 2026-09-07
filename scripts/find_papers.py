from __future__ import annotations

import argparse
import io
from pathlib import Path
import shlex
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from originrag.discovery.oa_import import ManualUploadRequired, download_open_pdf  # noqa: E402
from originrag.discovery.topic_search import DiscoveryUnavailable, search_journals, search_topic  # noqa: E402
from originrag.paths import load_project_env  # noqa: E402
from originrag.utils.terminal_ui import TerminalUI, safe_text  # noqa: E402


GUIDE = """Type a research topic to search across disciplines
/journals NAME   Find journals by name or ISSN
/select 1 2      Add journals from the last journal list (up to five)
/clear          Remove all journal restrictions
/search         Search the selected journals without a topic
/since YEAR     Restrict publication year; /since clears it
/sort newest    newest | relevance
/type TYPE      journal-article | proceedings-article | posted-content | all
/add 1          Import an open-access copy of a paper from the last results
/import PATH    Import your own PDF, using quotes for paths with spaces
/help           Show this guide
/exit           Return to chat or your shell

Search terms and filters go to Crossref. Adding a DOI checks OpenAlex,
Europe PMC and, if configured, Unpaywall for an accessible PDF.
Nothing is imported until you choose it and confirm.
"""


def import_paper(ui: TerminalUI, *, doi: str = "", source: Path | None = None) -> bool:
    ui.message("This updates the local corpus. Stop the web service and other corpus-writing jobs first. Restart them after importing.")
    reply = ui.console.input("Confirm they are stopped and import this paper? [y/N] ").strip().casefold()
    if reply not in {"y", "yes"}:
        ui.message("Import cancelled. Nothing changed.")
        return False
    if source is not None:
        source = source.expanduser().resolve(strict=True)
        if source.suffix.lower() != ".pdf" or not source.is_file() or source.stat().st_size > 40 * 1024 * 1024:
            raise ValueError("Choose a PDF no larger than 40 MB.")
        with source.open("rb") as handle:
            content = handle.read(40 * 1024 * 1024 + 1)
        if len(content) > 40 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise ValueError("Choose a PDF no larger than 40 MB.")
        filename = source.name
    else:
        with ui.waiting("Finding an accessible PDF"):
            content = download_open_pdf(doi, lambda stage: None)
        filename = "discovered.pdf"
    import web_chat

    inbox_root = ROOT / "bib" / "inbox"
    inbox_root.mkdir(parents=True, exist_ok=True)
    inbox = Path(tempfile.mkdtemp(prefix="terminal-", dir=inbox_root))
    manager = web_chat.LibrarySyncManager(inbox, auto_worker=False)
    manager.store_upload(io.BytesIO(content), content_length=len(content), filename=filename,
                         doi=doi, uploaded_by="terminal", schedule_import=False)
    with ui.waiting("Checking and indexing your paper"):
        summary = manager.run_pending_now()
    for result in summary.get("results", []):
        ui.message(f"{result.get('status', '')}: {result.get('reason', '')}")
    accepted = int(summary.get("imported") or 0) > 0
    ui.message("Added to your library and indexed." if accepted else "Not added. Review the import report or try a different PDF.")
    ui.message(f"Import report and any files needing review: {inbox}")
    return accepted


def run_finder(ui: TerminalUI, *, query: str = "", since: str = "", order: str = "relevance", issns=(), allow_import: bool = True) -> None:
    papers, journals, selected = [], [], {}
    kind = "journal-article"
    ui.message(GUIDE)
    pending = query or ("/search" if issns else "")
    while True:
        try:
            command = pending or ui.console.input("Find papers › ").strip()
            pending = ""
            if not command:
                continue
            parts = shlex.split(command)
            if not parts:
                continue
            name, values = parts[0].lower(), parts[1:]
            if name in {"/exit", "/quit", "exit", "quit"}:
                return
            if name == "/help":
                ui.message(GUIDE)
                continue
            if name == "/journals":
                journals = []
                with ui.waiting("Finding journals"):
                    journals = search_journals(" ".join(values))["items"]
                for index, journal in enumerate(journals, 1):
                    ui.message(f"{index}. {journal['title']} · {journal['publisher']} · {' / '.join(journal['issns'])}")
                ui.message("Use /select 1 2 to add journals; /clear removes restrictions." if journals else "No journals found. Try a full title or ISSN.")
                continue
            if name == "/select":
                chosen = [int(value) - 1 for value in values]
                if not chosen or any(i < 0 or i >= len(journals) for i in chosen):
                    raise ValueError("Choose numbers from the last /journals list.")
                updated = {**selected, **{tuple(journals[i]["issns"]): journals[i] for i in chosen}}
                combined = set(issns) | {issn for key in updated for issn in key}
                if len(updated) > 5 or len(combined) > 20:
                    raise ValueError("Choose up to five journals (20 ISSNs).")
                selected = updated
                ui.message("Selected: " + "; ".join(j["title"] for j in selected.values()))
                continue
            if name == "/clear":
                selected, issns = {}, ()
                ui.message("Journal restrictions cleared.")
                continue
            if name == "/since":
                if len(values) > 1 or (values and (not values[0].isdigit() or len(values[0]) != 4)):
                    raise ValueError("Use /since YEAR or /since to clear.")
                since = values[0] if values else ""
                ui.message(f"Published since: {since or 'any year'}")
                continue
            if name in {"/sort", "/type"}:
                choices = {"newest", "relevance"} if name == "/sort" else {"journal-article", "proceedings-article", "posted-content", "all"}
                if len(values) != 1 or values[0] not in choices:
                    raise ValueError("Choose " + " | ".join(sorted(choices)))
                if name == "/sort":
                    order = values[0]
                else:
                    kind = values[0]
                ui.message(f"{name[1:].capitalize()}: {values[0]}")
                continue
            if name in {"/add", "/import"}:
                if not allow_import:
                    raise ValueError("This chat uses custom corpus paths. Import into that corpus with its configured tools, or start ./originrag find-papers for the default local corpus.")
                if len(values) != 1:
                    raise ValueError("Use /add NUMBER or /import \"path/to/paper.pdf\".")
                if name == "/import":
                    import_paper(ui, source=Path(values[0]))
                else:
                    index = int(values[0]) - 1
                    if not 0 <= index < len(papers):
                        raise ValueError("Choose a number from the last paper results.")
                    ui.message(f"Import: {papers[index]['title']} · {papers[index]['doi']}")
                    import_paper(ui, doi=papers[index]["doi"])
                continue
            if name.startswith("/") and name != "/search":
                raise ValueError("Unknown command. Type /help or /exit.")
            papers = []
            query = " ".join(values) if name == "/search" else command
            restricted = sorted(set(issns) | {issn for key in selected for issn in key})
            with ui.waiting("Searching literature"):
                papers = search_topic(query, since=since, order=order, kind=kind, issns=restricted)["items"]
            ui.message(f"{len(papers)} results · " + ("selected journals only" if restricted else "all journals"))
            for index, paper in enumerate(papers, 1):
                ui.console.print(safe_text(f"{index}. {paper['title']}"), style="bold", markup=False, highlight=False)
                ui.message(f"   {paper['author']} · {paper['year']} · {paper['journal']}\n   {paper['url']}")
            ui.message("Use /add NUMBER to import an accessible PDF, or type another topic.")
        except (EOFError, KeyboardInterrupt):
            ui.message("Paper finder closed.")
            return
        except (ValueError, OSError, DiscoveryUnavailable, ManualUploadRequired) as exc:
            ui.message(str(exc), error=True)


def main(argv=None, *, ui=None, allow_import=True) -> int:
    parser = argparse.ArgumentParser(description="Find papers across disciplines, choose journals and import accessible PDFs.")
    parser.add_argument("query", nargs="*")
    parser.add_argument("--journal", action="append", default=[], metavar="ISSN", help="Restrict to an ISSN; repeat for more journals, or use /journals inside the finder.")
    parser.add_argument("--since", default="")
    parser.add_argument("--sort", choices=["relevance", "newest"], default="relevance")
    args = parser.parse_args(argv)
    load_project_env(ROOT / ".env")
    run_finder(ui or TerminalUI(), query=" ".join(args.query), since=args.since, order=args.sort, issns=args.journal, allow_import=allow_import)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
