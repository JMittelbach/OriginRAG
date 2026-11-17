# OriginRAG

Search your papers, ask questions and follow the evidence — in a browser or terminal.
OriginRAG links answers to source passages and checks whether papers share a
study, cohort or dataset. Five papers do not necessarily mean five independent studies.

- Search by topic, title, author or DOI, without a language model.
- Find new papers, filter by journal and import available open-access PDFs.
- Chat with your library using a local model through Ollama.
- Inspect citations, source passages and possible study overlap.

## Install and open

Requires **macOS or Linux, Git, Bash and Python 3.11**. With Conda or Miniforge installed:

```bash
git clone https://github.com/JMittelbach/OriginRAG.git
cd OriginRAG
./setup.sh --profile full
./web
```

Open the address printed in the terminal, normally `http://127.0.0.1:8765`.
Local use needs no account. Setup preserves existing settings and saves your
chosen profile for the next launch.

| Profile | Applications |
| --- | --- |
| `full` | OriginRAG, Quill and Folio |
| `rag` | OriginRAG only |
| `write` | Quill only, without RAG dependencies |

All profiles include Library, Bookshelf and the PDF reader. Replace `full` in
the setup command to choose a standalone installation.

Setup does **not** download a model or install a TeX compiler. Search and writing
work without a model; generated answers need [Ollama](https://ollama.com/) and a
local text model. **Models** in the web app shows installed models, downloads
and memory estimates. Quill's PDF preview needs an
[isolated TeX compiler](docs/writing.md#configure-pdf-compilation).

See [Getting started](docs/getting_started.md) for virtual-environment setup,
model configuration and troubleshooting. Clone the repository for the complete
web application; the Python wheel alone does not include the launchers or corpus.

## Add papers and ask questions

Open **Add papers** to upload your PDFs, or **Find papers** to discover and import
accessible PDFs. Accepted papers are organised and indexed in the background.
They become searchable when the update finishes; uncertain imports are flagged
for review. Catalogue-only entries are not full-text papers.

Ask a question in chat, open the cited passages and inspect the evidence status.
Use **Stop** to cancel your request. **Summarize** creates a reading card for one
paper, while **Save to shelf** keeps it close at hand.

Prefer the terminal?

```bash
./originrag search-papers "single-cell methods"
./terminal
```

Inside terminal chat, `/library` opens your papers, `/find` discovers new ones,
`/new` starts a conversation and `/help` lists commands. `./terminal --demo`
previews the interface without a model. For bulk local imports, follow the
[import guide](docs/getting_started.md#4-import-and-index-pdfs); use the web app
to add papers while the service is running.

## Extra tools

- **Library:** browse the corpus and organise papers into personal project collections.
- **Bookshelf and reader:** keep current reads, highlight PDFs, save private notes
  and mark papers as finished.
- **Quill:** write LaTeX with autosave, PDF preview, templates and ZIP import/export.
  Link a collection to keep references in sync and autocomplete citation keys.
  An optional project chat proposes edits for you to accept or reject.
- **Folio:** write visually with formatting, tables and images, then export DOCX,
  HTML, text or PDF. Complex Word layouts may not survive conversion exactly.

The full installation connects these tools in one navigation bar. Standalone
installations keep their own tools without the suite switcher. See the
[writing guide](docs/writing.md) for templates, collaboration, chat and backups.

## Run on a private server

Install on the server, then start with account protection:

```bash
ORIGINRAG_WEB_OPEN=0 ./web --auth --strict-port
```

From your computer, open a tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@SERVER
```

Visit `http://127.0.0.1:8765` and register. One account covers all enabled tools.
Keep the server on loopback; do not expose the built-in Python server directly
to the public internet.

For a persistent Linux service, complete the
[one-time systemd setup](docs/getting_started.md#keep-the-server-running-with-systemd), then use:

```bash
systemctl --user enable --now originrag.service
systemctl --user status originrag.service
systemctl --user stop originrag.service
systemctl --user start originrag.service
```

The guide also explains how to keep it running after logout and reboot.

## Your data and the evidence

Personal PDFs, chats, notes and generated indexes stay in ignored local storage.
Setup enables Git checks against accidentally committing or pushing private
data. Papers are not bundled with the repository; their redistribution rights
remain separate from the software licence. See the [data policy](docs/data_policy.md).

OriginRAG is research software, not a clinical decision system. Study-overlap
checks support human review but do not prove independence. Answers depend on the retrieved
evidence and can miss relevant studies. Check the cited papers before using
generated details in a publication.

## More

[Getting started](docs/getting_started.md) · [Web guide](docs/web_chat.md) ·
[Models](docs/models.md) · [Writing](docs/writing.md) ·
[Contributing](CONTRIBUTING.md) · [Cite OriginRAG](CITATION.cff) · [MIT licence](LICENSE)

For development checks: `python -m pip install -e '.[dev]'`, then `make check`.
