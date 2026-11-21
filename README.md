# OriginRAG

Search your papers, ask questions and follow the evidence — in a browser or terminal.
OriginRAG links answers to source passages and checks whether papers share a
study, cohort or dataset. Five papers do not necessarily mean five independent studies.

- Search by topic, title, author or DOI, without a language model.
- Find new papers, filter by journal and import available open-access PDFs.
- Chat with your library using a local model through Ollama.
- Inspect citations, source passages and possible study overlap.

## Install and open

The default installation gives you **OriginRAG, Quill, Folio, Library, Bookshelf
and the PDF reader**. The editors and PDF renderer ship in the repository.
The first install needs internet: it sets up Python and its packages, prepares
the TeX compiler and packages for the included templates, and installs or reuses
Ollama and a local Qwen model (about 2.5 GB on a new install). You do not need
to install Node.js, activate Conda or supply your own LaTeX installation.

### Windows

1. **In PowerShell as administrator**, run `wsl --install -d Ubuntu` if you
   do not already have Ubuntu on WSL 2. Restart if prompted, then open **Ubuntu**
   once to create your Linux user. This requires Windows 11 or Windows 10
   build 19041 or later. [Microsoft's instructions](https://learn.microsoft.com/windows/wsl/install)
2. **In Windows**, clone this repository or choose **Code → Download ZIP** on
   GitHub and extract it. Double-click **`OriginRAG.cmd`** inside the folder.
   If using the ZIP for a private repository, Ubuntu may ask you to sign in to GitHub.
3. **In your Windows browser**, use the address printed in the launcher,
   normally `http://localhost:8765`. Keep the terminal open. Next time, just
   double-click the same file. Your working copy is `~/OriginRAG` **inside Ubuntu**.

If you already work in a separate Ubuntu VM, use the Linux instructions **in
that VM** to keep using its library. [More about Windows and WSL](docs/getting_started.md#windows)

### macOS and Linux

Open **Terminal on the Mac**, or a **terminal on the Linux machine** where your
papers will live. You need Git, Bash and curl. If Git is missing on a Mac, run
`xcode-select --install` first. On Ubuntu/Debian, if tools are missing, run
`sudo apt update && sudo apt install git bash curl ca-certificates util-linux`.
From the folder where you want to keep OriginRAG, run:

```bash
git clone https://github.com/JMittelbach/OriginRAG.git
cd OriginRAG
./setup.sh
./web
```

Open the printed address (normally `http://127.0.0.1:8765`) on that machine.
Local use needs no account. Stop with **Ctrl+C**; next time, run `./web` from
the same `OriginRAG` folder. On a remote Linux server, use the
[SSH tunnel instructions](#run-on-a-private-server) instead.

### Install only what you need

Run these commands **inside the repository in a macOS, Linux or Ubuntu/WSL
terminal**. Start any profile with `./web`. All include Library, Bookshelf and
the PDF reader. The Windows double-click launcher always selects the full suite.

| Install command | What it prepares |
| --- | --- |
| `./setup.sh` | Full suite, local model and PDF compilation |
| `./setup.sh --profile rag` | OriginRAG with the local model; no TeX download |
| `./setup.sh --profile write` | Quill with PDF compilation; no model download |
| `./setup.sh --apps-only` | Full suite and Python dependencies, without provisioning Ollama, model weights or TeX |

Setup checks the bundled reader and editors. For Quill it also compiles a test
PDF before reporting success, unless you chose `--apps-only`. Linux and WSL
must permit the compiler sandbox or have a [configured TeX container](docs/writing.md#configure-pdf-compilation).
If setup stops, fix the reported problem and rerun the same command; no need
to delete your workspace. [Detailed installation guide](docs/getting_started.md)

**Updating an existing clone:** wait for edits to save, stop the app, run
`git pull --ff-only` in the working copy (on Windows: `cd ~/OriginRAG` in
Ubuntu), then start the app again and reload the page. Ordinary
code updates need no reinstall and leave your papers and manuscripts in place.
If dependencies change, rerun setup; pass the same `--profile` for a standalone install.

## Add papers and ask questions

A fresh installation starts with an empty library. Your paper lists, PDFs,
extracted text, chunks and search indexes stay local and are ignored by Git.
If an older installation still shows a paper list after an update, run
`python3 scripts/init_local_data.py --check` in the checkout. It identifies
unchanged copies of the old starter catalogue without modifying your library.

Open **Add papers** to upload your PDFs, or **Find papers** to discover and import
accessible PDFs. Accepted papers are organised and indexed in the background.
They become searchable when the update finishes; uncertain imports are flagged
for review. Catalogue-only entries are not full-text papers.

Ask a question in chat, open the cited passages and inspect the evidence status.
For a reading list, try “Show me five papers on microscopy”, then “Five more”.
The next list skips papers already shown. When searching full text, the chat
skips matches found only in passages it identifies as bibliographies.
Questions can be in German; replies default to English. Ask for German explicitly
if you prefer it. Quill keeps the manuscript’s language when suggesting edits.
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

In an SSH session **on the server**, follow the Linux installation above
through `./setup.sh`. Instead of starting the local-only app, run:

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

**Writing on two computers?** In each local installation, open **Settings →
Document sync** and connect the same private cloud or network folder. Choose
**Sync this document** on the first computer, then **Open** on the second.
No terminal is needed. [Device setup and the shared-server alternative](docs/writing.md#continue-on-another-computer).

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
