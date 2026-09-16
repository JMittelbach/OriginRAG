# OriginRAG

[![CI](https://github.com/JMittelbach/OriginRAG/actions/workflows/public-ci.yml/badge.svg)](https://github.com/JMittelbach/OriginRAG/actions/workflows/public-ci.yml)

Search your scientific literature and ask questions in a browser or terminal.
OriginRAG generates answers with source passages and checks whether papers
may share a study, cohort or dataset — because **five papers do not necessarily
mean five independent experiments**.

It keeps relevant reports available while assessing how much independent
support they provide, including when their relationships are uncertain.

- **Search and browse** by title, author or DOI without a language model.
- **Find new papers** on any topic, optionally restrict by journal, and add open-access PDFs to your library.
- **Chat with your papers** using a local model through Ollama.
- **Inspect the evidence:** follow citations, read source passages and review
  possible study overlap.

Library topic groups are generated from your paper titles, with up to ten groups
for larger collections and fewer for small libraries. No subject-specific category
list is required. The evidence-accounting method is domain-general; equivalent
performance in other fields has not been established.

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
./setup.sh
```

Setup creates the `originrag` environment and a local `.env` file.
For virtual-environment installation, see [Getting started](docs/getting_started.md#1-install).

Generated answers additionally need [Ollama](https://ollama.com/), a compatible
text model (Qwen by default) and legally obtained PDFs. Check the
[model and memory guidance](docs/models.md)
before choosing a model. Use a source checkout for the complete application;
the Python wheel alone does not include the launchers or corpus.

## Try it

Search the included bibliographic catalogue immediately — no model or PDFs needed:

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
**Open PDF** opens the copy stored in your corpus directly. **Publisher page**
is available separately; papers without a local PDF are clearly marked.

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

## Understand the evidence

OriginRAG is **research software (0.1.0), not a clinical decision system**.
It distinguishes evidence that remains sufficient despite uncertain study
overlap, evidence that is insufficient, and cases requiring overlap review.

Automatic overlap detection assists review; it does not prove independence.
An unknown independent-study count means that overlap is unresolved.
Certification concerns the selected evidence, not studies retrieval may have
missed. Check cited papers before using generated details in a publication.

## Self-host privately

Install and prepare the corpus on your server, then run:

```bash
ORIGINRAG_WEB_OPEN=0 ./web --auth --strict-port
```

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

## Data, documentation and development

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

CI checks linting, tests, CLI startup, package building and wheel installation.
It does not rerun all manuscript experiments.
