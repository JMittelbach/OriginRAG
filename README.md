# OriginRAG

Search your scientific literature and ask questions in a browser or terminal.
OriginRAG generates answers with source passages and checks whether papers
may share a study, cohort or dataset — because **five papers do not necessarily
mean five independent experiments**.

It keeps relevant reports available while assessing how much independent
support they provide, including when their relationships are uncertain.

- **Search and browse** by title, author or DOI without a language model.
- **Find new papers** on any topic, optionally restrict by journal, and add open-access PDFs to your library.
- **Chat with your papers** using a local model through Ollama, with optional
  experimental Colibri support.
- **Inspect the evidence:** follow citations, read source passages and review
  possible study overlap.

The evidence-accounting method is domain-general. Equivalent performance in
other fields has not yet been established.

## Features

OriginRAG is the main application. The same installation can optionally provide
a few practical tools around the corpus:

- **Library:** browse the shared corpus, find new papers, and create private
  collections for projects. Topic groups are generated from your paper titles;
  you choose which papers belong in each personal collection.
- **Bookshelf:** save papers you are actively reading for quick access to the
  reader, summaries and personal notes.
- **Quill (optional):** a small LaTeX workspace with source and PDF side by
  side. It can connect a manuscript to a Library collection and insert its
  references. Quill is useful for writing, but it is separate from the
  OriginRAG evidence workflow and does not require a language model.
- **Folio (full installation):** a visual document editor with a ribbon,
  text formatting, tables, images, comments and DOCX import/export. Write
  directly on the page without LaTeX or a language model.

## Install

Personal PDFs, chats, bibliography updates and indexes stay in ignored local
directories. They are not included by normal Git commits. Setup also enables
checks against accidentally committing or pushing local data. See the
[data policy](docs/data_policy.md).

Requires **macOS or Linux, Git, Bash and Python 3.11**. The recommended setup
uses Conda or Miniforge:

```bash
git clone https://github.com/JMittelbach/OriginRAG.git
cd OriginRAG
./setup.sh --profile full
./web
```

Choose the modules you need:

| Profile | Included |
| --- | --- |
| `full` | OriginRAG, Quill and the Folio document editor |
| `rag` | OriginRAG |
| `write` | Quill writing workspace, without OriginRAG or a language model |

The full installation has a shared application switcher. Standalone installations
show only their own application tools, without the suite navigation.

All profiles include Library, Bookshelf and PDF Reader. On a shared server,
one account covers every enabled module. Use `./setup.sh --profile write` or
`./setup.sh --profile rag` to enable only the selected application. The `write`
profile also omits the RAG dependencies and does not load a search index. Setup
saves the choice in `.env`; `./web` reuses it. `./web --profile full` overrides
it for one run, provided the corresponding dependencies are installed.

Setup creates the `originrag` Conda environment and preserves existing private
settings. It does not download a language model or install a TeX compiler.
For a virtual environment, see [Getting started](docs/getting_started.md#1-install).

Generated answers additionally need [Ollama](https://ollama.com/), a compatible
text model (Qwen by default) and legally obtained PDFs. Check the
[model and memory guidance](docs/models.md)
before choosing a model. In the web app, **Settings → Models & runtimes** lets
you see installed models, download supported models and measure their speed.
On a shared server, administrators control model selection; hosts can grant
download access to specific accounts.
Use a source checkout for the complete application;
the Python wheel alone does not include the launchers or corpus.

## Try it

In the `rag` or `full` profile, search the included bibliographic catalogue
immediately — no model or PDFs needed:

```bash
./originrag search-papers "EPCR-binding PfEMP1"
./terminal library
```

In the library, type an author or title to filter, use arrow keys to browse,
and press Esc to leave. Add `--local-only` to show only locally available PDFs.
Catalogue entries are not necessarily available as full text for chat.

To preview the terminal interface without a model:

```bash
./terminal --demo
```

## Ask about your own papers

This section applies to `rag` and `full`.

Set `ORIGINRAG_BIB_DIR` in `.env` to an absolute path for your PDF library.
Then configure the model, preview the import, apply it and build the index:

```bash
./originrag setup-model
./originrag import-local --source-dir /path/to/papers
./originrag import-local --source-dir /path/to/papers --apply
./originrag bib
./originrag prepare-corpus
./originrag doctor
./web
```

Use `./terminal` for terminal chat, or ask a single question:

```bash
./originrag ask "Which PfEMP1 features are associated with severe malaria?"
```

In terminal chat, `/library` opens your papers, `/chats` opens saved
conversations, `/new` starts a new one and `/help` opens the command guide.
Use `/find` to search new papers, choose journals and import PDFs, or start
`./terminal find-papers` directly. Inside the finder, `/help` lists commands
and `/exit` returns to chat. Local imports require the web service and other
corpus-writing jobs to be stopped; use the web app for live imports.
Recent conversation context supports follow-up questions; previous answers
are not treated as scientific evidence. Use `--no-animation` for a static
terminal interface or `--plain` for plain text.

In model-backed chat, questions are prepared for search across languages, including
obvious typing errors. You can also ask which methods, topics or families appear
in your papers: the overview links named examples to retrieved sources and does
not claim to exhaust the library or count independent studies.

Choose **Summarize** beside a paper in **Library**, or enter `/summary DOI or exact title`
in terminal chat. A short reading card covers the question, approach, findings and
limitations, with source passages. It clearly marks abstract-only coverage and does
not claim to have read omitted text. Web summaries use the existing chat queue.
Saved summaries are reused when their text basis and model are unchanged.
Follow-up questions stay in chat.

Use **■ Stop** beside the progress indicator or above the chat input to cancel
your request. Other users' requests are unaffected.

Use **Save to shelf** in Library or chat references to keep papers close at hand.
The **My shelf** button at the top opens your personal bookshelf, with quick access
to each paper and its summary. On shared servers, shelves belong to individual
accounts. Removing a bookmark does not remove the paper from the corpus.
**Open reader** opens your corpus PDF in a quiet reading space with zoom,
text search, a table of contents where available, text highlights and personal notes.
Scroll continuously through the pages with your mouse wheel, trackpad or touch.
The page indicator follows your position; page controls and search still let you jump directly.
Select a passage to reveal the quick marker toolbar. Highlights stay separate from the original
PDF and are saved to your account. Search or colour-filter your notes, copy quotes,
and export saved annotations as Markdown or JSON. Fit a whole page, resize the
notes panel, or use **Focus view** for more reading space within the window.
The shared navigation stays visible, so you can return to your library or bookshelf.
**?** shows the keyboard shortcuts.
Selecting text to copy does not save a highlight. Choose a colour explicitly,
then click the saved highlight whenever you want to add or edit a comment.
Use the **Paper notes** tab for free-form thoughts about the whole paper.
Notes save automatically to your account, with a **Save** button and text export.
Use **Mark as read** to build your personal **Bookshelf → Finished** list, with
dates, search and a count of papers finished. You can optionally remove the paper
from your shelf without deleting its PDF or notes, and undo the read status later.
**Original PDF** and **Publisher page** remain available separately.
Scanned pages need OCR for text highlighting; the reader supports PDFs up to 64 MB.

For later additions, use **Add papers** in the web app. Local use needs no
account; sign in when using an authenticated shared server. While the
service is running, uploads are automatically checked, named, categorised,
and indexed in the background. Accepted papers become searchable when the
library update completes; uncertain files are flagged for review.
For local files, repeat the import commands above. Arbitrary folders are not
continuously monitored. In terminal chat, `/help import` explains both paths.

See the [first-answer walkthrough](docs/web_chat.md#first-answer-walkthrough)
and [corpus guide](docs/getting_started.md#4-import-and-index-pdfs) for filing,
paper discovery and open-access downloads.

## Writing tools

If Quill is enabled, open it from the application switcher or at `/write`.
It provides a compact LaTeX editor with a PDF preview, autosave, templates and
ZIP import/export. A manuscript can use a private Library collection for its
bibliography. PDF compilation requires an isolated TeX setup; editing and export
work without it. See [the writing guide](docs/writing.md) for details.

In the full installation, **Folio** opens from the same switcher or at `/folio`.
Documents autosave to your local workspace, or to your own account when the
server requires sign-in. **File → Save as…** exports DOCX, HTML, text or JSON to
your computer; **Print or save PDF** provides PDF output.
Folio is a separate editor, not Microsoft Word: DOCX conversion supports the
editor's formatting but does not preserve every Word feature or exact page
layout. Keep the original when importing complex documents. Live co-editing
and tracked changes are not included; conflicting saves do not silently
overwrite a newer version.
See [Folio's editing guide and Word compatibility](docs/writing.md#write-a-formatted-document-with-folio)
for picture/table controls, recovery and supported document features.

## Understand the evidence

OriginRAG is **research software (0.1.0), not a clinical decision system**.
It distinguishes evidence that remains sufficient despite uncertain study
overlap, evidence that is insufficient, and cases requiring overlap review.

Automatic overlap detection assists review; it does not prove independence.
An unknown independent-study count means that overlap is unresolved.
Certification concerns the selected evidence, not studies retrieval may have
missed. Check cited papers before using generated details in a publication.

## Self-host privately

Install your chosen profile on the server, then run:

```bash
ORIGINRAG_WEB_OPEN=0 ./web --auth --strict-port
```

The same command serves `full`, `rag` or `write`, using the saved profile.
Only RAG answers require a model and an indexed corpus. Quill can be
hosted independently, with the same private accounts and libraries.

From your own computer, connect through an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@SERVER
```

Open `http://127.0.0.1:8765` locally and register the first account.
Keep the service on loopback; do not expose the Python server directly to the
internet. Use the SSH tunnel for remote access.
[Server setup details](docs/getting_started.md#6-self-host-on-a-private-server)

For continuous operation on Linux, configure the
[systemd user service](docs/getting_started.md#keep-the-server-running-with-systemd).
After the one-time setup:

```bash
systemctl --user enable --now originrag.service
systemctl --user status originrag.service
journalctl --user -u originrag.service -f
```

The service restarts after a failure. Enable user lingering as described in
the guide so it also stays available after logout and starts after a reboot.
Use `systemctl --user stop originrag.service` before maintenance, and
`systemctl --user start originrag.service` afterwards. The SSH tunnel is still
required for remote access.

## Data and documentation

OriginRAG builds your local corpus from imported PDFs or open-access downloads,
organising papers and generating the retrieval index. PDFs and generated corpus
files are not bundled with the repository. The software licence does not grant
redistribution rights to third-party papers.

- [Getting started](docs/getting_started.md): setup, library, terminal commands and troubleshooting
- [Web application](docs/web_chat.md): accounts, queue, sources and evidence display
- [Data policy](docs/data_policy.md): local storage and safe transfer
- [Contributing](CONTRIBUTING.md) · [Citation metadata](CITATION.cff) · [MIT License](LICENSE)

To run the software checks:

```bash
python -m pip install -e '.[dev]'
make check
```

The repository checks cover linting, tests, CLI startup, package building and
wheel installation. They do not rerun all manuscript experiments.
