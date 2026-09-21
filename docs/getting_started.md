# Getting started

This guide installs OriginRAG from source, prepares a private literature
collection, and runs the first search and answer. The repository does not
distribute publisher PDFs, extracted full text, generated answers, account
data, or a retrieval index.

## 1. Install

OriginRAG supports macOS and Linux with Python 3.11. The recommended Conda setup
is:

```bash
git clone https://github.com/JMittelbach/OriginRAG.git
cd OriginRAG
./setup.sh --profile full
conda activate originrag
```

Choose `full` for OriginRAG and Quill, `rag` for OriginRAG, or `write`
for Quill. Library, Bookshelf, PDF Reader and accounts are shared in
every profile. A new `write` installation contains only the shared Python
dependencies (python-dotenv and PyMuPDF), not the retrieval/terminal dependencies
or a language model. A fresh full/rag installation adds those dependencies.

```bash
./setup.sh --profile write
./web
```

Setup records `ORIGINRAG_APP_PROFILE` in `.env`, preserving other settings.
Without `--profile`, an existing choice is reused; a fresh installation
defaults to `full`. Updating a profile does not remove unrelated packages
or delete accounts, papers or manuscripts. To obtain a separate lean environment:

```bash
ORIGINRAG_CONDA_ENV=originwrite ./setup.sh --profile write
ORIGINRAG_CONDA_ENV=originwrite ./web
```

Use `./setup.sh --recreate` only when deliberately replacing the selected
Conda environment. A standard virtual environment is also supported:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[full]'
python scripts/configure_app_profile.py --profile full
./web
```

For Quill alone, replace `full` with `write` in both installation and
configuration commands. Use `rag` for OriginRAG alone. To install another
module later, install the corresponding extra and update the saved profile.
`./web --profile write` selects a profile for just that process; it does not
install dependencies or change the saved configuration.

The launcher uses an active virtual environment, a local `.venv`, or the
`originrag` Conda environment, in that order.

Quill editing and ZIP export are available immediately. PDF compilation
requires a separately prepared, isolated TeX compiler and cached packages;
see [Quill setup](writing.md). There is no model requirement for writing,
reference management or reading PDFs. Skip the model and RAG-indexing sections
below when using the `write` profile.

## 2. Configure the local model

For `rag` and `full` profiles only.

Generated answers use Ollama, with Qwen as the default model family. Other
Ollama text models are configurable. See [Choosing a model](models.md) for
hardware starting points, supported backends and safe model switching.
Install Ollama using its platform instructions, then run:

```bash
./originrag setup-model
```

The default is `qwen3:30b` with a 65,536-token context, which is not suitable
for every machine. The smaller profile selects `qwen3:14b` and requests a
16,384-token context unless an existing context override takes precedence:

```bash
./originrag setup-model --balanced
```

After switching profiles, set the planner, answer and chat models and context
in `.env` to the values you want to use.
[Configuration example](models.md#configure-a-model)

Check the installation, corpus, Ollama server, and configured model with:

```bash
./originrag doctor
```

Add `--test-call` to make one small generation request. Metadata search does
not require Ollama.

### Model memory

Choose a model and context size using the [hardware guide](models.md#hardware-guide).
Check memory pressure during a representative question, not just at startup.
On GPU servers, monitor host RAM and GPU memory separately. On Apple Silicon,
the model shares memory with the operating system and other applications.

Use `ollama ps` to inspect the loaded model and CPU/GPU placement. Reduce the
model or context size if memory pressure or swapping becomes high.
`./originrag doctor --test-call` checks basic generation; it does not measure
peak memory during a full answer or establish scientific answer quality.

## 3. Configure the local bibliography

Set an absolute path in `.env`:

```dotenv
ORIGINRAG_BIB_DIR=/absolute/path/to/OriginRAG/bib
```

The ignored `bib/` directory inside the clone is a convenient choice. Store
only papers that you are legally entitled to process. Subscription access does
not grant permission to redistribute full text.

Search the committed bibliographic metadata without a model call:

```bash
./originrag search-papers "severe malaria EPCR"
./originrag search-papers "10.1038/nature12216"
```

## 4. Import and index PDFs

First inspect a directory without changing the library:

```bash
./originrag import-local --source-dir /path/to/papers
```

Then apply accepted imports and build the local corpus:

```bash
./originrag import-local --source-dir /path/to/papers --apply
./originrag bib
./originrag prepare-corpus
```

The importer checks hashes, DOI identity, normalized titles, and basic article
structure. Uncertain identities are retained for review. The browser's **Add
papers** action is the preferred routine workflow for DOI-identified PDFs.

### How papers are organised

Import assigns a topic using metadata and keyword rules, then places the PDF
in a corresponding folder. The local importer uses existing categories;
unrecognised topics go to `papers/other`, not a guessed scientific discipline.
The browser's inbox import can additionally create `papers/topics/...` folders
from publisher subject metadata when no existing category matches. Those
folders survive subsequent `./originrag bib` refreshes.

This is rule-based filing, not a learned topic taxonomy or a guarantee of
correct classification. Putting PDFs directly in `bib/` does not automatically
start an import. `./originrag bib` inventories and organises known metadata,
but is not a semantic classifier for arbitrary unregistered files. Prefer
**Add papers** or import from a separate source folder and inspect the report.

### Add papers while the server is running

In the web app, **Add papers** starts a background import. Local use needs no
account; authenticated shared servers require sign-in. Accepted
PDFs are checked for identity and duplicates, named, categorised, converted to
text and indexed. The running service refreshes its library and retrieval
caches after import; no manual reindex or restart is needed. Wait for the
library update to complete before asking about the new papers. Files needing
review are not silently accepted.

The service also processes pending lowercase `.pdf` files in its application
directory's `bib/inbox` at startup. It does not continuously watch that folder
or arbitrary library directories for files copied in later. For routine live
updates, use **Add papers** rather than running a separate importer against
the active service's index.

### Discover open-access papers

Search any topic and choose journals interactively:

```bash
./terminal find-papers "graph theory"
```

This is also available through `/find` inside terminal chat. In the finder,
use `/journals Nature`, then `/select 1` to restrict the search. Type a topic,
or `/search` to browse the selected journals. `/add 1` looks for a verified
open-access PDF of the first result and imports it after confirmation.
`/import "/path/to/paper.pdf"` imports your own copy. Accepted PDFs are named,
categorised and indexed through the same importer as web uploads. `/help`
lists the filters and commands; `/exit` returns to chat or the shell.
These local imports require the web service and other corpus-writing jobs
to be stopped. Use the web app for live additions instead.

For configured discovery profiles:

```bash
./originrag scrape
./originrag scrape-download 10
```

The second command downloads only available open-access candidates and then
rebuilds the local corpus.

`./originrag watch` performs one discovery/download and metadata-refresh pass;
it is not a persistent folder watcher and does not rebuild the retrieval index.
Recurring discovery requires an external scheduler. Run command-line corpus
updates while the web service is stopped, then restart it to load the changes.

## Browse your library without chat

```bash
./terminal library
./terminal library --author "Turner"
./terminal library --title "severe malaria"
./terminal library "Turner 2013"
./terminal library --local-only
```

`./originrag library` is equivalent. The interactive browser shows complete titles,
recorded authors, years, journals, DOIs and actual local-PDF availability,
newest first. Start typing an author or title in the focused Search field;
results update immediately. Use ↑/↓ or Page Up/Down to browse, Ctrl-U to
clear the search, and Tab to move between controls. Author and title
filters ignore case and accents and can be combined. Searches use the stored
bibliographic metadata, not PDF full text or a language model.

Already in terminal chat? Type `/library`, `/library Turner 2013`, or
`/library --title "severe malaria"`. The same viewer opens within your session.
Press Esc, use the Exit button, or type `exit` or `/exit` and press Enter
to return to chat; your conversation and selected sources are
preserved. Browsing does not submit a question to the model. `/library --help`
shows the available filters without leaving chat.
Inside the search field, `/help` explains the library controls. Ctrl-C and
Ctrl-D also close the library, not the surrounding chat. The old pager has
been replaced; a space or the letter `q` now belongs to your search text
(`q` followed by Enter remains an exit shortcut).

Use `--no-pager` to print into normal terminal scrollback. Redirected output
does not open a viewer. `--no-browser` is an equivalent flag.
`--papers PATH` selects another metadata catalogue.
No files are imported or changed by browsing. To add PDFs, use `import-local`
first, then `./originrag bib` to refresh the catalogue. Registered papers
without a retrieval ID are included too. Copying files into a directory alone
does not add catalogue entries.
`./originrag bib` remains the command for refreshing the bibliography; its old
`library` alias now opens this read-only view.

## 5. Ask a question

Type `/help` in chat for a compact guide to papers, conversations and answers.
Use `/help library` for search filters and keyboard shortcuts, `/help import`
for adding papers and automatic web imports, `/help advanced`
for optional settings, or `/help all` for the complete command reference.
Help and library commands never submit a question to the model.

### Separate terminal conversations

- `/chats`: open the saved-chat picker. Browse with ↑/↓, select with Space,
  then Tab to Open. Esc returns without switching. In plain/piped mode,
  the command prints numbered chats; use `/switch NUMBER` afterwards.
- `/new [title]`: start a separate conversation. Untitled chats use their
  first question as the title.
- `/switch NUMBER` or `/switch CHAT_ID`: open another chat. Numbers refer to
  the last `/chats` list; IDs are stable across restarts.
- `/rename title`: change the current title.
- `/history`: display the current chat's saved questions and visible answers.

The last exchange is shown when a chat is reopened; `/sources` and `/details`
refer to that chat, not the one you just left. Model/task settings and keyboard
history are separate too. Reopen a chat directly with
`./terminal --resume CHAT_ID`. Starting without `--resume` creates a new chat.

Conversations are stored under `outputs/chat_sessions/` (or `--out-dir`), not
uploaded. Snapshots are written atomically. An active chat is write-locked so
two terminal processes cannot overwrite it; switch away or close the other
terminal before opening that same chat. This feature covers terminal chats,
not the separate authenticated web-chat store. Switching is available between
questions; inference still runs synchronously, without background chat jobs.

For each answer, up to four recent exchanges are supplied to the answer and
repair prompts, with a 6,000-character budget for the serialized conversation
data. Individual questions, answer excerpts and paper-title lists are also
bounded. Older turns remain visible in `/history` but are not unlimited model
memory. Earlier answers are explicitly marked as fallible conversational
context; only the current evidence pack can support scientific claims. Old
evidence IDs are masked so they cannot be confused with newly assigned IDs.
Existing follow-up retrieval also uses source titles and answer excerpts,
without recursively nesting previous retrieval queries.

Earlier terminal sessions are reconstructed from their retained run configs,
final answer files and evidence packs. Rejected drafts are excluded. Sessions
without those artifacts cannot recover missing answers. Legacy files and
failed/interrupted turn directories are retained; damaged sessions are listed
as unreadable rather than silently overwritten.

To preview the terminal appearance without a model, corpus or saved session:

```bash
./terminal --demo
```

The rotating helix has shaded strands and perspective depth. It accompanies
actual processing stages and elapsed time,
not an estimated percentage. Answers retain Markdown formatting; `/sources`
lists selected papers and `/details` shows saved files and checks. Use
`--no-animation` to disable motion or `--plain` for unstyled text. Redirected
output is plain text automatically. The preview uses clearly labelled example
content and makes no model calls.

The loading helix stays compact at 17 columns by eight lines, even in a large
window. Fine Unicode dots retain detail inside each character cell. It rotates
continuously both while typing and while a question is processing. Input is
available immediately, with no startup delay. The input editor supports
Unicode, bracketed paste, cursor movement and in-memory question history
with ↑/↓. Ctrl-C or Ctrl-D at an empty prompt leaves the chat. The preview
runs long enough to show a complete rotation but does not open a chat.

Start the browser or terminal interface:

```bash
./web
# or
./terminal
```

Run one non-interactive question with:

```bash
./originrag ask \
  "Which PfEMP1 features are associated with severe malaria?"
```

Generated artifacts are written below `outputs/` and remain local. The default
answer route automatically screens for publication versions, shared cohorts,
and reused datasets. It may return a supported answer, request study-overlap
review, report insufficient evidence, or abstain.

Retrieved paper and excerpt counts are not independent-study counts.
`Independent studies: ?` means that the available origin relations do not
support a defensible exact count.

## 6. Self-host on a private server

Install the required profile on the server, then start the authenticated
loopback service. The same command works for full, RAG-only and writing-only
installations:

```bash
ORIGINRAG_WEB_OPEN=0 ./web --auth --strict-port
```

The saved `ORIGINRAG_APP_PROFILE` chooses the available modules. All enabled
modules use the same login and personal library; separate service processes
or accounts are not required. Add `--profile write`, `--profile rag` or
`--profile full` to override the choice for this run. RAG answers additionally
need a model and indexed papers. Quill additionally needs an isolated
TeX compiler only for PDF compilation.

From an authorised workstation, create an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@SERVER
```

Open `http://127.0.0.1:8765` locally. The first account can be registered in
the browser, or an administrator can create one interactively:

```bash
./originrag users create USERNAME
```

Back up `.env`, `bib/`, `data/extracted_text/`, `data/chunks/`,
`data/indexes/`, `data/local_auth/`, and `outputs/web_chat_sessions/`. Do not
bind the built-in server to `0.0.0.0` or expose it directly to the internet.
A public deployment requires a reviewed HTTPS reverse proxy, secure cookies,
access controls, backups, and a production application server.

### Keep the server running with systemd

On a Linux server with systemd, a user service keeps OriginRAG running without
an open SSH session. First verify that `./web --auth --strict-port` starts
successfully, then stop that foreground process before enabling the service.
Keep Ollama, or your chosen model backend, running separately when using
RAG answers. Writing-only servers do not need a model service.

Create `~/.config/systemd/user/originrag.service` with the following content.
Replace **all three** `/absolute/path/to/OriginRAG` paths with your installation
directory. Set `ORIGINRAG_PYTHON` to the actual Python interpreter that has
OriginRAG's dependencies installed, for example the `.venv` interpreter shown
below or `/absolute/path/to/conda/envs/originrag/bin/python`. Do not rely on an
interactive Conda activation or use `~` in these paths.

```ini
[Unit]
Description=Origin research workspace

[Service]
Type=simple
WorkingDirectory=/absolute/path/to/OriginRAG
Environment=ORIGINRAG_PYTHON=/absolute/path/to/OriginRAG/.venv/bin/python
Environment=ORIGINRAG_WEB_OPEN=0
Environment=ORIGINRAG_APP_PROFILE=full
ExecStart=/absolute/path/to/OriginRAG/web --auth --strict-port
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
UMask=0077

[Install]
WantedBy=default.target
```

Load and start it:

Set the profile above to `write` or `rag` for a module-specific server.

```bash
systemctl --user daemon-reload
systemctl --user enable --now originrag.service
systemctl --user status originrag.service
journalctl --user -u originrag.service -f
```

For operation after logout and automatic startup after reboot, enable
[user lingering](https://man7.org/linux/man-pages/man1/loginctl.1.html):

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

The second command should report `Linger=yes`. If permission is denied, ask
your server administrator to enable it for your account. On systems without a
systemd user manager, use the service manager provided by the administrator.
Do not run a second watchdog or foreground web process on the same port.

Before an update, let queued requests and imports finish, then stop the service:

```bash
systemctl --user stop originrag.service
# Update application files/dependencies, preserving your local corpus and .env.
systemctl --user start originrag.service
```

Use `systemctl --user restart originrag.service` to load configuration changes.
To disable automatic startup, run `systemctl --user disable --now originrag.service`.
The application remains bound to loopback and requires an SSH tunnel as above.

## Troubleshooting

### Retrieval index missing

Confirm that PDFs exist below `ORIGINRAG_BIB_DIR`, then run:

```bash
./originrag bib
./originrag prepare-corpus
```

### Ollama or the model is missing

Run `./originrag setup-model`, then `./originrag doctor --test-call`. Inspect
`outputs/runtime/ollama.log` if the local server does not start.

### Answers are slow or memory use is high

Use the balanced model profile, keep model concurrency at one, and use a
bounded context. A loaded model and its key-value cache may reserve much more
memory than the visible prompt text.

### A scientific answer is withheld

Inspect the cited passages and study-overlap view. Withholding can be correct
when evidence is off-scope, required facets cannot be supported together, or
unresolved provenance prevents a robust certificate.
