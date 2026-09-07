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
./setup.sh
conda activate originrag
```

Use `./setup.sh --recreate` to rebuild the environment. A standard virtual
environment is also supported:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

The launcher uses an active virtual environment, a local `.venv`, or the
`originrag` Conda environment, in that order.

## 2. Configure the local model

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

### Model profiles and recorded memory

The following are observed runs, not minimum hardware requirements. Both used
Linux with NVIDIA GPU offload, `qwen3:32b` in Q4_K_M quantisation and a
16,384-token context. The retained records do not identify the CPU or GPU
model, so no specific hardware configuration is claimed as validated.

| Recorded run | Workload | Peak Ollama host RAM (RSS) | GPU allocation |
| --- | --- | ---: | ---: |
| 2026-08-28, E062 | One answer-model call | 1.81 GiB | 25.33 GiB (NVIDIA measurement) |
| 2026-08-31, E064 | Five queued web jobs, one active at a time | 2.22 GiB | Not retained in this record |

Exact measurements, units, model identity and source-file hashes are in
[`model_memory_observations.json`](model_memory_observations.json). The low host
RSS reflects GPU offload and must not be interpreted as total model memory or
as a requirement for CPU-only or Apple unified-memory execution. The 48-GiB
watchdog used during these runs was a stop threshold, not measured consumption.

No comparable memory measurements are retained for the default `qwen3:30b`
65,536-token profile or the balanced `qwen3:14b` profile. A smaller model or
context is configurable, but suitability for a particular machine should be
checked with `./originrag doctor --test-call` while observing both host and GPU
memory. This is a setup check, not evidence of scientific answer quality.

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

In the signed-in web app, **Add papers** starts a background import. Accepted
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

Candidate discovery is explicit:

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

Install OriginRAG, Ollama, and the corpus on the server, then start the
authenticated loopback service:

```bash
ORIGINRAG_WEB_OPEN=0 ./web --auth --strict-port
```

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
