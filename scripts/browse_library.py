#!/usr/bin/env python3
"""Read-only, model-free browsing of the local bibliographic catalogue."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from originrag.paths import OriginRAGConfigError, get_bib_dir  # noqa: E402
from originrag.provenance.library import load_paper_registry  # noqa: E402
from originrag.retrieval.paper_retrieval import read_metadata_rows, safe_year  # noqa: E402
from originrag.utils.terminal_ui import safe_text  # noqa: E402


def normalise(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                            if not unicodedata.combining(c)).split())


def load_catalogue(path: Path) -> list[dict]:
    """Include registered papers that are not yet in the retrieval corpus."""
    rows = read_metadata_rows(path)
    registry = path.with_name("paper_registry.tsv")
    if not registry.is_file():
        return rows
    identifiers = ("paper_id", "bib_key", "local_pdf_relpath")
    seen = {(key, row[key]) for row in rows for key in identifiers if row.get(key)}
    for row in load_paper_registry(registry):
        keys = {(key, row[key]) for key in identifiers if row.get(key)}
        if not keys or keys & seen:
            continue
        rows.append(row)
        seen.update(keys)
    return rows


def filter_papers(rows: list[dict], *, query: str = "", author: str = "", title: str = "") -> list[dict]:
    tokens = normalise(query).split()
    matches = []
    for row in rows:
        if normalise(author) not in normalise(row.get("author", "")):
            continue
        if normalise(title) not in normalise(row.get("title", "")):
            continue
        searchable = normalise(" ".join(row.get(field, "") for field in
                                        ("title", "author", "year", "journal", "doi")))
        if all(token in searchable for token in tokens):
            matches.append(row)
    return sorted(matches, key=lambda row: (-safe_year(row.get("year")),
                                            normalise(row.get("title", "")), row.get("paper_id", "")))


def local_pdf(row: dict, bib_root: Path) -> bool:
    relative = row.get("local_pdf_relpath", "").strip()
    if not relative or Path(relative).is_absolute():
        return False
    root = bib_root.resolve()
    path = (root / relative).resolve()
    return path.is_relative_to(root) and path.suffix.lower() == ".pdf" and path.is_file()


class LibraryBrowser:
    """A searchable catalogue with an explicit exit, independent of any model."""

    HELP = (
        "LIBRARY HELP\n\n"
        "Type an author, title, year, journal or DOI in the Search field.\n"
        "Results update as you type. Multiple words must all match.\n"
        "Search ignores case and accents; it searches metadata, not PDF text.\n\n"
        "Up / Down       Browse result lines without leaving the search field\n"
        "Page Up / Down  Browse by page\n"
        "Ctrl-U          Clear the search and show all papers within the filters\n"
        "Tab / Shift-Tab Move between Search, results and the Exit button\n"
        "Escape          Close the library and return to where you opened it\n"
        "Ctrl-C / Ctrl-D Close the library\n"
        "/exit + Enter   Close the library (exit, quit and /quit also work)\n"
        "/help           Show this help; Ctrl-U returns to all results\n\n"
        "Author/title/local-only filters supplied when opening remain active.\n"
        "PDF local means the file exists on this computer. No local PDF means\n"
        "only its catalogue entry is available. Browsing does not call a model."
    )

    def __init__(self, rows: list[dict], *, total: int, bib_root: Path,
                 query: str = "", filters: str = "", input=None, output=None):
        from prompt_toolkit.application import Application
        from prompt_toolkit.filters import has_focus
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
        from prompt_toolkit.widgets import Button, Frame, TextArea

        self.rows, self.total, self.filters = rows, total, safe_text(filters)
        self.matches: list[dict] = []


        self.entries = {}
        for row in rows:
            lines = [safe_text(row.get("title") or "Untitled paper"),
                     f"{safe_text(row.get('author') or 'Author not recorded')} · "
                     f"{safe_text(row.get('year') or 'Year unknown')}"]
            if row.get("journal"):
                lines.append(safe_text(row["journal"]))
            if row.get("doi"):
                lines.append(f"DOI: {safe_text(row['doi'])}")
            lines.append("PDF local" if local_pdf(row, bib_root) else "No local PDF")
            self.entries[id(row)] = "\n".join(lines)
        self.results = TextArea(read_only=True, scrollbar=True, wrap_lines=True)
        self.search = TextArea(text=query, multiline=False, prompt="Search › ",
                               accept_handler=self._accept)
        self.search.buffer.on_text_changed += lambda _: self._refresh()
        self._refresh()
        bindings = KeyBindings()

        @bindings.add("escape")
        @bindings.add("c-c")
        @bindings.add("c-d")
        def close(event):
            self.close()

        @bindings.add("c-u")
        def clear(event):
            self.search.text = ""
            event.app.layout.focus(self.search)

        @bindings.add("up", filter=has_focus(self.search))
        @bindings.add("down", filter=has_focus(self.search))
        @bindings.add("pageup")
        @bindings.add("pagedown")
        def scroll(event):
            key = event.key_sequence[-1].key
            count = max(1, event.app.output.get_size().rows - 10) if "page" in key else 1
            if key in {"up", "pageup"}:
                self.results.buffer.cursor_up(count=count)
            else:
                self.results.buffer.cursor_down(count=count)

        @bindings.add("tab")
        def next_field(event):
            event.app.layout.focus_next()

        @bindings.add("s-tab")
        def previous_field(event):
            event.app.layout.focus_previous()

        @bindings.add("<any>", filter=has_focus(self.results))
        def type_to_search(event):
            event.app.layout.focus(self.search)
            self.search.buffer.insert_text(event.data)

        @bindings.add("enter", filter=has_focus(self.results))
        def accept_from_results(event):
            event.app.layout.focus(self.search)
            self._accept(self.search.buffer)

        footer = VSplit([
            Window(FormattedTextControl("Type to search · ↑/↓ scroll · PgUp/PgDn\n"
                                        "Esc or /exit + Enter: back · /help"), height=2),
            Button("Exit", handler=self.close),
        ], padding=1)
        layout = HSplit([
            Window(FormattedTextControl(self._heading), height=1, style="class:heading"),
            Frame(self.search, title="Author, title or DOI"),
            Frame(self.results, title="Papers · newest first"),
            footer,
        ])
        self.app = Application(layout=Layout(layout, focused_element=self.search),
                               key_bindings=bindings, full_screen=True, mouse_support=True,
                               style=Style.from_dict({"heading": "bold ansicyan", "frame.border": "ansicyan"}),
                               input=input, output=output)
        self.app.ttimeoutlen = 0.1

    def _heading(self):
        label = f"OriginRAG · Library · {len(self.matches)} of {self.total} papers"
        return label + (f" · {self.filters}" if self.filters else "")

    def _refresh(self):
        query = self.search.text.strip()
        self.matches = filter_papers(self.rows, query=query)
        if query.lower() == "/help":
            self.results.text = self.HELP
        elif self.matches:
            self.results.text = "\n\n".join(f"{n}. {self.entries[id(row)]}"
                                             for n, row in enumerate(self.matches, 1))
        else:
            self.results.text = "No matching papers. Try a shorter title or surname.\nCtrl-U clears the search."

    def _accept(self, buffer):
        if buffer.text.strip().lower() in {"exit", "/exit", "quit", "/quit", "q", "/q", "/back"}:
            self.close()
        return True

    def close(self):
        self.app.exit()

    def run(self):
        self.app.run()


def print_library(console: Console, rows: list[dict], *, total: int, bib_root: Path) -> None:
    console.print(Text(f"OriginRAG · Library · {len(rows)} of {total} catalogued papers", style="bold cyan"))
    console.print(Text("Newest first. ‘PDF local’ means the file exists on this computer.\n", style="dim"))
    if not rows:
        console.print("No matching papers. Try a shorter title or an author's surname.", markup=False)
    for number, row in enumerate(rows, 1):
        console.print(Text(f"{number}. {safe_text(row.get('title') or 'Untitled paper')}", style="bold"))
        console.print(Text(f"   {safe_text(row.get('author') or 'Author not recorded')} · "
                           f"{safe_text(row.get('year') or 'Year unknown')}", style="cyan"))
        if row.get("journal"):
            console.print(Text(f"   {safe_text(row['journal'])}"))
        if row.get("doi"):
            console.print(Text(f"   DOI: {safe_text(row['doi'])}"))
        status = "PDF local" if local_pdf(row, bib_root) else "No local PDF"
        console.print(Text(f"   {status}\n", style="dim"))


def main(argv: list[str] | None = None, *, console: Console | None = None) -> int:
    parser = argparse.ArgumentParser(prog="library", description="Browse your paper catalogue without chat or a model.")
    parser.add_argument("query", nargs="*", help="Words from author, title, year, journal or DOI")
    parser.add_argument("--author", default="", help="Author name contains this text (case-insensitive)")
    parser.add_argument("--title", default="", help="Title contains this text (case-insensitive)")
    parser.add_argument("--papers", type=Path, default=ROOT / "data/metadata/papers.tsv")
    parser.add_argument("--local-only", action="store_true", help="Only papers whose PDF exists locally")
    parser.add_argument("--no-pager", "--no-browser", action="store_true", help="Print without the interactive browser")
    args = parser.parse_args(argv)
    if not args.papers.is_file():
        parser.error(f"Paper catalogue not found: {args.papers}. Import your PDFs first with originrag import-local.")
    try:
        bib_root = get_bib_dir()
    except OriginRAGConfigError:
        bib_root = ROOT / "bib"
    rows = load_catalogue(args.papers)
    filtered = filter_papers(rows, author=args.author, title=args.title)
    if args.local_only:
        filtered = [row for row in filtered if local_pdf(row, bib_root)]
    console = console or Console(highlight=False)
    if not args.no_pager and console.is_terminal and not console.is_dumb_terminal and sys.stdin.isatty():
        from prompt_toolkit.output import create_output

        filters = "; ".join(value for value in (f"author: {args.author}" if args.author else "",
                                              f"title: {args.title}" if args.title else "",
                                              "local PDFs only" if args.local_only else "") if value)
        LibraryBrowser(filtered, total=len(rows), bib_root=bib_root, query=" ".join(args.query),
                       filters=filters, output=create_output(stdout=console.file)).run()
    else:
        print_library(console, filter_papers(filtered, query=" ".join(args.query)),
                      total=len(rows), bib_root=bib_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        raise SystemExit(0)
