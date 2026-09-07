"""Small, scrollback-friendly terminal presentation; no inference or file writes."""
from __future__ import annotations

import math
import io
import sys
import time
from contextlib import contextmanager
from functools import lru_cache

from rich.console import Console, Group
from rich import box
from rich.color import Color
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.style import Style


STAGES = {
    "Planning a focused literature search": "Understanding your question",
    "Searching and ranking the local corpus": "Searching your library",
    "Selecting evidence and auditing study origins": "Checking study overlap",
    "Checking source types and evidence coverage": "Reading the evidence",
    "Explaining why each paper matches the question": "Connecting the papers to your question",
    "Writing a citation-grounded answer": "Writing your answer",
    "Validating claims and citations": "Checking the answer and its sources",
}

HELIX_ANGULAR_SPEED = 1.8
HELIX_FRAMES_PER_TURN = 120


def safe_text(value: object) -> str:

    return "".join(c for c in str(value) if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


def helix_dimensions(columns: int, lines: int) -> tuple[int, int]:
    """Keep a compact footprint, including on large monitors."""
    return max(9, min(17, columns - 31)), max(4, min(8, lines - 6))


def helix(phase: float, *, rows: int = 8, width: int = 17, ascii_only: bool = False) -> Text:
    """Project two rotating tubes and their rungs with perspective and a depth buffer.

    Braille cells provide a 2-by-4 dot grid within each character, instead of
    enlarging the character footprint. Shading and occlusion depend on depth.
    """
    if rows < 1 or width < 3:
        raise ValueError("helix needs at least one row and three columns")
    raster_width, height = width * 2, rows * 4
    x_scale, y_scale = max(1, raster_width - 3) / 18, height / 20
    pixels: dict[tuple[int, int], tuple[float, tuple[int, int, int]]] = {}
    radius = (raster_width - 3) * 0.37
    centre = (raster_width - 1) / 2

    def paint(x: float, y: float, z: float, colour: tuple[int, int, int], size: float):

        scale = 1 / (1 - 0.18 * z)
        px, py = centre + x * scale, y + 0.7 * z * y_scale
        thickness_x = size * scale * x_scale
        thickness_y = size * scale * y_scale
        for iy in range(max(0, math.floor(py - thickness_y)), min(height, math.ceil(py + thickness_y) + 1)):
            for ix in range(max(0, math.floor(px - thickness_x)), min(raster_width, math.ceil(px + thickness_x) + 1)):
                nx, ny = (ix - px) / thickness_x, (iy - py) / thickness_y
                distance = nx * nx + ny * ny



                coverage = min(1.0, max(0.0, (1.18 - distance) / 0.36))
                if coverage < 0.25:
                    continue
                nz = math.sqrt(max(0.0, 1 - distance))
                depth = z + 0.12 * nz
                if pixels.get((ix, iy), (-math.inf, ()))[0] > depth:
                    continue
                light = max(0, -0.4 * nx - 0.35 * ny + 0.85 * nz)
                shade = (0.4 + 0.6 * (z + 1) / 2) * (0.38 + 0.62 * light)
                highlight = light ** 18 * 0.40
                rgb = tuple(max(0, min(255, round((c * shade * (1 - highlight) + 245 * highlight)
                                                  * math.sqrt(coverage))))
                            for c in colour)
                pixels[ix, iy] = depth, rgb



    pitch = 13.0 * y_scale
    for rung in range(math.ceil(20 / 3)):
        y = rung * 3.0 * y_scale
        angle = phase + y * math.tau / pitch
        rung_x, rung_z = radius * math.cos(angle), math.sin(angle)
        for step in range(raster_width * 2 + 1):
            fraction = step / raster_width - 1
            paint(rung_x * fraction, y,
                  rung_z * fraction, (95, 134, 158), 0.3)
    for step in range((height + 4) * 5):
        y = step / 5 - 2
        for offset, colour in ((0, (52, 223, 224)), (math.pi, (174, 116, 245))):
            angle = phase + y * math.tau / pitch + offset
            paint(radius * math.cos(angle), y, math.sin(angle), colour, 0.7)

    result = Text()
    dot_bits = ((1, 8), (2, 16), (4, 32), (64, 128))
    for row in range(rows):
        for column in range(width):
            samples = []
            mask = 0
            for dy in range(4):
                for dx in range(2):
                    pixel = pixels.get((column * 2 + dx, row * 4 + dy))
                    if pixel is not None:
                        mask |= dot_bits[dy][dx]
                        samples.append(pixel)
            if not samples:
                result.append(" ")
                continue


            depth, colour = max(samples, key=lambda sample: sample[0])
            if ascii_only:
                ramp = ".:-=+*#@"
                result.append(ramp[min(7, max(0, round((depth + 1) * 3.5)))])
            else:
                result.append(chr(0x2800 + mask), Style(color=Color.from_rgb(*colour),
                                                      bold=depth > 0.3, dim=depth < -0.3))
        if row < rows - 1:
            result.append("\n")
    return result


@lru_cache(maxsize=128)
def _helix_frame(frame: int, rows: int, width: int, ascii_only: bool) -> Text:
    """Bounded cache of periodic geometry; labels and user text are never cached."""
    return helix(frame * math.tau / HELIX_FRAMES_PER_TURN,
                 rows=rows, width=width, ascii_only=ascii_only)


def animated_helix(phase: float, *, rows: int = 8, width: int = 17,
                   ascii_only: bool = False) -> Text:
    if not (1 <= rows <= 8 and 3 <= width <= 17):
        return helix(phase, rows=rows, width=width, ascii_only=ascii_only)
    frame = round((phase % math.tau) * HELIX_FRAMES_PER_TURN / math.tau) % HELIX_FRAMES_PER_TURN

    return _helix_frame(frame, rows, width, ascii_only).copy()


class WaitingDisplay:
    def __init__(self, ui: "TerminalUI", label: str, *, show_elapsed: bool = True):
        self.ui = ui
        self.label = label
        self.show_elapsed = show_elapsed
        self.started = time.monotonic()
        self.live: Live | None = None

    def update(self, stage: str) -> None:
        label = STAGES.get(stage, safe_text(stage))
        if label == self.label:
            return
        self.label = label
        if self.live is None:
            self.ui.console.print(Text(f"  {label}…", style="dim"))

    def __rich_console__(self, console, options):
        elapsed = max(0, time.monotonic() - self.started)
        label = Text()
        label.append(self.label, style="bold cyan")
        if self.show_elapsed:
            label.append(f"\n{int(elapsed) // 60}:{int(elapsed) % 60:02d} elapsed", style="dim")
        if options.max_width < 48 or console.size.height < 14:
            yield label
            return
        grid = Table.grid(padding=(0, 2))
        width, rows = helix_dimensions(options.max_width, console.size.height)
        grid.add_column(width=width)
        grid.add_column(ratio=1, vertical="middle")
        phase = max(0, time.monotonic() - self.ui.animation_started) * HELIX_ANGULAR_SPEED
        grid.add_row(animated_helix(phase, rows=rows, width=width,
                           ascii_only=self.ui.ascii_only), label)
        yield Panel(grid, border_style="dim cyan", padding=(0, 1))


class TerminalUI:
    def __init__(self, *, console: Console | None = None, plain: bool = False,
                 animate: bool = True):
        self.console = console or Console(no_color=True if plain else None,
                                          force_terminal=False if plain else None,
                                          highlight=False)
        self.plain = plain or not self.console.is_terminal or self.console.is_dumb_terminal
        self.animate = animate and not self.plain
        encoding = getattr(self.console.file, "encoding", None) or "utf-8"
        self.ascii_only = "utf" not in encoding.lower()
        self._prompt_session = None
        self._chat_id = None
        self._input_history = []
        self.animation_started = time.monotonic()
        self._idle_display = WaitingDisplay(self, "Ready when you are.\nType your question below.",
                                            show_elapsed=False)

    def welcome(self, *, preview: bool = False) -> None:
        if self.plain:
            self.console.print("OriginRAG | Follow the evidence", markup=False)
        else:
            title = Text()
            title.append("OriginRAG\n", style="bold cyan")
            title.append("Follow the evidence\n\n", style="white")
            title.append("Ask a question about your papers\n", style="default")
            title.append("/chats  /new  /library  /help  /exit", style="dim")
            self.console.print(Panel(title, border_style="cyan", padding=(1, 2)))
        if preview:
            self.console.print(Text("INTERFACE PREVIEW · example content, no model or library access", style="yellow"))
        elif self.plain:
            self.console.print("Ask a question. /help for commands; /exit to leave.", markup=False)

    def prompt(self) -> str:
        if self.animate and sys.stdin.isatty():
            if self._prompt_session is None:
                self._prompt_session = self._make_prompt_session()
            return self._prompt_session.prompt()
        return self.console.input("\n[bold cyan]You › [/bold cyan]" if not self.plain else "\nYou > ")

    def set_chat(self, identifier: str, title: str, questions: list[str] | None = None) -> None:
        if identifier != self._chat_id:
            self._prompt_session = None
            self._input_history = list(questions or [])
            self._chat_id = identifier
        self._idle_display.label = f"Chat: {safe_text(title)[:60]}\nType your question below"

    def choose_chat(self, chats: list[dict], current_id: str) -> str | None:
        if not chats:
            self.message("No saved chats yet. /new starts a conversation.")
            return None
        if self.plain or not sys.stdin.isatty():
            for number, chat in enumerate(chats, 1):
                active = " *" if chat["id"] == current_id else ""
                self.message(f"{number}{active}. {chat['title']} · {chat['turns']} turns · {chat['id']}")
            self.message("/switch NUMBER or /switch CHAT_ID opens a chat; /new starts another.")
            return None
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
        from prompt_toolkit.shortcuts import radiolist_dialog

        values = [(chat["id"], safe_text(f"{'● ' if chat['id'] == current_id else ''}{chat['title']} "
                                        f"({chat['turns']} turns) · {chat['id']}"))
                  for chat in chats if chat.get("available", True)]
        if not values:
            self.message("Saved chats could not be read. Their files have been retained.", error=True)
            return None
        app = radiolist_dialog(title="Your terminal chats", text="↑/↓ browse · Space selects · Tab → Open · Esc returns",
                               values=values, default=current_id, ok_text="Open", cancel_text="Back")
        keys = KeyBindings()

        @keys.add("escape")
        @keys.add("c-c")
        @keys.add("c-d")
        def cancel(event):
            event.app.exit(result=None)

        app.key_bindings = merge_key_bindings([app.key_bindings, keys])
        app.ttimeoutlen = 0.1
        return app.run()

    def _idle_toolbar(self):
        from prompt_toolkit.formatted_text import ANSI



        stream = io.StringIO()
        console = Console(file=stream, width=self.console.width,
                          height=self.console.size.height, force_terminal=True,
                          color_system=self.console.color_system,
                          no_color=self.console.no_color, highlight=False)
        console.print(self._idle_display)
        return ANSI(stream.getvalue().rstrip("\n"))

    def _make_prompt_session(self, **kwargs):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.filters import is_done
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.output import create_output



        session = PromptSession(
            [("bold ansicyan", "You › ")], refresh_interval=0.05,
            history=kwargs.pop("history", None) or InMemoryHistory(self._input_history),
            output=kwargs.pop("output", None) or create_output(stdout=self.console.file),
            **kwargs,
        )


        session.layout.container = HSplit([
            ConditionalContainer(Window(FormattedTextControl(self._idle_toolbar),
                                        dont_extend_height=True), filter=~is_done),
            session.layout.container,
        ])
        return session

    @contextmanager
    def waiting(self, label: str = "Searching your library", *, show_elapsed: bool = True):
        display = WaitingDisplay(self, label, show_elapsed=show_elapsed)
        if self.animate:

            with Live(display, console=self.console, refresh_per_second=20,
                      transient=True, redirect_stdout=True, redirect_stderr=True) as live:
                display.live = live
                yield display.update
        else:
            self.console.print(Text(f"  {label}…", style="dim"))
            yield display.update

    def message(self, message: str, *, error: bool = False) -> None:
        self.console.print(Text(safe_text(message), style="yellow" if error else "dim"))

    def help(self, sections: list[tuple[str, list[tuple[str, str]]]], *,
             title: str, intro: str, footer: str) -> None:
        """Render a static command guide without taking ownership of input."""
        if self.plain:
            self.console.print(Text(f"\n{title}\n{intro}"))
            for heading, commands in sections:
                self.console.print(Text(f"\n{heading}"))
                for command, description in commands:
                    self.console.print(Text(f"  {command}\n    {description}"))
            self.console.print(Text(f"\n{footer}"))
            return

        contents = [Text(intro), Text("")]
        for index, (heading, commands) in enumerate(sections):
            if index:
                contents.append(Text(""))
            contents.append(Text(heading, style="bold magenta"))
            if self.console.width < 64:
                for command, description in commands:
                    contents.append(Text(command, style="bold cyan"))
                    contents.append(Text(f"  {description}", style="dim"))
            else:
                table = Table.grid(padding=(0, 2), expand=True)
                table.add_column(width=28)
                table.add_column(ratio=1)
                for command, description in commands:
                    table.add_row(Text(command, style="bold cyan"), Text(description))
                contents.append(table)
        contents.extend([Text(""), Text(footer, style="dim")])
        self.console.print(Panel(Group(*contents), title=Text(title, style="bold cyan"),
                                 title_align="left", border_style="dim cyan",
                                 box=box.ASCII if self.ascii_only else box.ROUNDED,
                                 padding=(1, 2)))

    def answer(self, text: str, *, title: str = "OriginRAG") -> None:
        text = safe_text(text)
        if self.plain:
            self.console.print(text, markup=False, highlight=False)
        else:
            self.console.print(Panel(Markdown(text), title=Text(title),
                                     border_style="cyan", padding=(1, 2)))

    def sources(self, chunks: list[dict], *, limit: int = 5) -> None:
        seen = set()
        sources = []
        for chunk in chunks:
            key = chunk.get("paper_id") or chunk.get("doi") or chunk.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            title = safe_text(chunk.get("title") or chunk.get("citation_label") or "Untitled report")
            doi = safe_text(chunk.get("doi") or "")
            sources.append(Text(f"{len(sources) + 1}. {title}" + (f"\n   DOI: {doi}" if doi else "")))
        if not sources:
            return
        heading = f"Selected papers · {len(sources)}"
        displayed = sources[:limit] if limit else sources
        if self.plain:
            self.console.print(heading, markup=False)
            for source in displayed:
                self.console.print(source)
        else:
            self.console.print(Panel(Group(*displayed), title=Text(heading), border_style="dim", padding=(0, 1)))
        if limit and len(sources) > limit:
            self.message("/sources shows the complete selected-paper list.")

    def preview(self) -> None:
        self.welcome(preview=True)
        self.console.print(Text("\nYou › How do I read the study-overlap result?", style="bold"))
        with self.waiting() as update:
            for stage in ("Searching and ranking the local corpus",
                          "Selecting evidence and auditing study origins",
                          "Writing a citation-grounded answer"):
                update(stage)
                if self.animate:
                    time.sleep(2.0)
        self.answer(
            "**Example only.** Two reports may describe the same dataset. "
            "They remain two papers, but they do not automatically supply two "
            "independent studies.\n\n"
            "The chat keeps the answer, its sources and unresolved study overlap visible.",
            title="Interface preview",
        )
        self.message("Preview finished. Start your own chat with ./terminal")
