# Web application

The browser interface uses the same local retrieval, evidence selection,
source checking, and answer pipeline as the command-line interface.

## Start locally

```bash
./web
```

The launcher opens a loopback-only service and selects a nearby port if `8765`
is unavailable. Generated sessions remain below `outputs/web_chat_sessions/`
and are ignored by Git.

## First visit and adding literature

A five-step quick tour introduces uploads, literature search, chat, study overlap
and personal settings. Skip it at any time or reopen it with **? → Quick tour**.
Completion is remembered for this account in this browser. Clearing browser
storage shows the tour again.

## Reading and marking papers

**Mark as read** in the PDF reader adds the paper to **Bookshelf → Finished**,
your personal reading history. It records the date and counts each paper once.
Opening or scrolling a PDF never marks it as read automatically. The confirmation
offers **Also remove from bookshelf**, unchecked by default. Removing a shelf
bookmark does not delete the paper, highlights, or notes.

The **Finished** view shows how many papers you have marked as read and lets you
search past reads by title, author, or DOI. You can reopen papers, save them back
to your shelf, or undo the status with **Mark as unread**. Undoing the status does
not change shelf membership. Reading history is private to your account on a
shared server. Local use without accounts has one local history. It is stored
alongside your bookmarks in `.personal_shelf.sqlite3`, so backing up your user
directory preserves both.

Choose a saved paper in **My shelf → Open reader**. The reader displays the
corpus PDF inside OriginRAG, with page navigation, zoom, a reading theme, and
personal notes. Select text, choose a marker colour and press **Highlight
selection**. Add a comment beside the highlight and press **Save note**.
Click a note's page button to return to that passage. Search your notes or filter
by highlight colour without losing unsaved edits. Each note offers **Copy quote**
and a colour selector. Colour changes are applied with **Save note**.
**Export notes** downloads all saved highlights and comments as JSON or readable
Markdown, including PDF page numbers. It does not include unsaved drafts or modify
the original PDF. **Download PDF** saves the unmodified document.

Use **Fit page** to see a whole page or **Fit width** for larger text. Drag the
divider to resize the notes panel, or focus it and use the arrow keys. **Notes**
hides the panel. **Focus view** hides the footer while keeping the shared
navigation, reading controls and notes available, without entering browser full screen.
Use **Exit focus**, **F**, or **Esc** to return to the normal view. The footer
shows the current page and scroll position. **?** opens the keyboard guide.

The notes sidebar has separate **Highlights** and **Paper notes** tabs. Paper notes
are private, free-form text linked to the paper, not to a marked passage or PDF
version. They save after a brief typing pause or with **Save** / **Ctrl/Cmd+S**.
**Export text** downloads your current draft. If saving fails, the draft stays in
the open reader with a visible status and retry controls. Conflicting edits from
another window require an explicit choice instead of silently overwriting notes.
These notes use the same per-account notes database as highlights and are included
in its backups. Unsaved drafts are not stored in shared browser storage.

**Find** (Ctrl/Cmd+F) searches the PDF's selectable text and marks matching
passages. Move between matches with the arrows or Enter/Shift+Enter in the search
field. Esc closes search. Searching stays local in the browser. Scanned text is
not searchable without OCR. Search reports partial results if it reaches 1,000
matches or 2,000 pages, or cannot read a page. **Contents** opens the PDF's embedded
table of contents when available.

Selecting text alone never saves a highlight. The selection toolbar offers
**Copy** plus **Yellow**, **Green**, **Red**, and **Rose** highlights. Choose a
colour to save a highlight, or **+ Note** to save it and start writing immediately.
Left-click a saved highlight to open its comment in the notes panel. Dragging
or double-clicking highlighted text still selects it for copying, without saving
another highlight or moving focus into a comment. **H** highlights
the current selection; Ctrl/Cmd+S saves the focused note. Left/right arrow keys
turn pages when not editing or selecting text. Zoom preserves the reading
position on the current page.

Notes are private to each account on an authenticated server. Local unauthenticated
use has one local notes collection. They are stored in `.reader_notes.sqlite3`
inside the user's chat directory, ignored by Git. Back up that directory to keep
notes when moving an installation. Removing a shelf bookmark does not erase notes.
Notes are tied to the exact PDF contents. If a paper's PDF is replaced, earlier
notes remain stored separately rather than appearing on the wrong passage.
The reader warns about unsaved comments and rejects stale edits from another tab.
The notes panel shows saving, unsaved and unconfirmed-save states. If a save is
not confirmed, keep the tab open and reload notes to check the stored state before
retrying. Existing drafts are kept when notes are refreshed.
If a highlight was deleted in another window, its unsaved draft stays visible
with **Save draft copy** so that it can be recovered before dismissal.

PDF.js is bundled locally, including its fonts and codecs. No external PDF service
or model call is involved. Use localhost (including an SSH tunnel) or HTTPS.
The reader accepts PDFs up to 64 MB. Pages form a continuous scrollable document;
the page indicator follows the reading position. Nearby pages are rendered on demand,
and distant canvases are released to limit memory use. Scans need a
searchable text layer for text highlighting. **Original PDF** remains available
for unsupported documents and your browser's print/download controls.

## Adding papers

If the library is empty, or contains only bibliographic records, the quick tour
explains what is missing. **Add papers** accepts PDFs locally without an account,
or after signing in when the server uses authentication.
Accepted uploads are checked, named, categorised and indexed automatically.

The library overview groups papers automatically from their titles. Larger
collections have up to ten groups; smaller or repetitive collections have fewer.
Labels are extracted from recurring words and phrases, not from a fixed list of
disciplines. Grouping runs locally without an LLM or external metadata service.
It refreshes when the catalogue is refreshed after an import, so labels and
membership can change as the collection grows. These are approximate browsing
groups, not expert annotations. Papers without informative titles are shown as
Unclassified. Original storage categories, paper IDs and retrieval inputs remain
unchanged.
Check the update status for duplicates, rejected files or files needing review.
Open the local URL printed by `./web`: **Find papers → Add to library** works
without `--auth` too. A catalogue entry alone is not full text available for chat.

Account-free imports require both the listener and the client connection to
use loopback. The server validates the local Host, port, Origin and browser
fetch metadata and requires a random per-server request token for downloads,
uploads and library updates. Foreign-site requests and unauthenticated network
bindings cannot import. This is a single-user mode, not isolation from other
local OS users or SSH users with access to the same loopback service. Use
`--auth` for shared deployments. Restart the web process and reload the browser
after upgrading from a version that required sign-in for all imports.

**Find papers** searches external literature by any topic, independently of the
local malaria-oriented discovery profiles. Topic examples cover sciences,
engineering, social sciences and humanities, but do not restrict the query.
Choose a starting year and sort by relevance or publication date. Journal
articles are the default. Other options include conference papers, posted
content (including preprints), or all publication types including books.
The [Crossref search API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
receives only the search query and filters, not the local library or chats.
Coverage varies by discipline and registered publication metadata.

To restrict a search, type a journal name or ISSN under **Only these journals**
and tick up to five matches. Selected journals appear as removable buttons.
The search includes only records carrying one of their ISSNs, including print
and electronic identifiers. Leave the topic empty to browse their recent
papers. Names come from Crossref's journal directory, with a one-hour cache;
no local journal database needs to be installed. Journals missing from that
directory cannot be selected by name. This filter does not assess journal
quality or peer-review status.

Search returns up to 12 bibliographic records. Locally, or in an upload-enabled account,
click **Add to library** on a result to find and import an open-access PDF.
The DOI is sent to OpenAlex, Europe PMC and Crossref to locate open-access
copies, including publisher PDF links registered with Crossref. Open-access
article pages are also checked for DOI-matched PDF links when
direct provider links fail, within the same bounded download budget. Available
PMC copies are retrieved through the official [PMC Open Data cloud service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/),
provided by the US National Library of Medicine. The latest deposited version
must have matching identifiers and be marked open access and not retracted.
Article licences still apply. If the host configures
`ORIGINRAG_UNPAYWALL_EMAIL`, Unpaywall is also consulted. An optional
`ORIGINRAG_OPENALEX_API_KEY` increases OpenAlex's available request budget.
Provider terms and usage limits apply. See [OpenAlex authentication](https://help.openalex.org/api/authentication/)
and the [Unpaywall API](https://unpaywall.org/api).

The import continues on the server when the dialog or browser tab is closed.
Reopen **Find papers** to see recent import progress. Two downloads can run at
once, with at most eight active or queued imports and a 40 MB limit per PDF.
Downloads use public HTTPS addresses only, with revalidated redirects and
connections pinned to a checked public IP. An accessible PDF must also contain
the requested DOI on its first page. Scanned copies, alternate versions or
PDFs that fail this check are not automatically accepted.

Accepted PDFs go through the same identifier, duplicate, naming and indexing
checks as uploads. A metadata-only record is supplemented while retaining its
paper ID and citation key. Existing assets are not overwritten. A downloaded
file is not reported as ready until library processing succeeds.

**Add to library** runs the complete download, classification, naming and
indexing process without an upload step. The button shows progress and changes
to **In library** only after indexing succeeds. If no suitable PDF can be
obtained, the import shows a failure and **Retry import**, not an upload prompt
or a success message. Browser access can work even when a publisher blocks
automated requests. OriginRAG does not copy browser cookies or bypass access
checks. The separate **Add papers** action remains available for local files.
An unavailable copy does not necessarily mean the paper is paywalled: provider
outages, download restrictions and identity-check failures can also cause this.
Import status is held for the running server process, not across a restart.
Already indexed papers and staged inbox files remain on disk; unfinished
downloads are not automatically restarted. Recent statuses are bounded to
128 jobs and are visible only to the account that requested them.

Existing catalogue entries are marked. The search/import does not bypass paywalls,
determine peer-review status or imply that a result supports the current answer.
It uses no language model. Network failures are shown without blocking chat.

## Appearance

Use **Appearance** in the top bar to choose Light, Dark, Black or your system
setting. **City cat** is the default wallpaper. Other choices are Mountains & moon,
Lunar horizon, Digital DNA, Moonlit forest and DNA artwork. Wallpapers are
softened in Light mode; **DNA artwork** switches to its matching light image.
Existing saved preferences are retained. For a plain background, choose slate, blue, green or
plum; Black mode uses solid black. Changes apply immediately and are
saved in this browser, independently of your account and chats.

## Reading answers

The evidence status stays visible below each answer. Expand it to inspect study
overlap, or open **References** for the cited papers. Source metadata and
**Search & citation checks** can be expanded separately. Warnings about withheld
or provisional answers and unchecked or failed citations remain visible.

## Accounts

Enable local accounts with:

```bash
./web --auth
```

Open `http://127.0.0.1:8765` and create the first account, or manage accounts
from the terminal:

```bash
./originrag users create USERNAME
./originrag users list
./originrag users reset-password USERNAME
./originrag users disable USERNAME
./originrag users enable USERNAME
```

Passwords are stored as salted `scrypt` hashes. Sessions use HTTP-only,
SameSite-Strict cookies and session-bound CSRF tokens. The account database is
local, ignored by Git, and created with owner-only permissions where supported.
There is no email-based recovery; an administrator resets forgotten
passwords.

Authenticated mode accepts only loopback binding. Use an SSH tunnel for access
from another machine. Do not expose the built-in Python server directly to the
internet.

## Jobs and queue

**■ Stop** appears beside a request's progress and above the chat input. It removes
your waiting request or stops your running request, including its model connection.
The chat shows **Stopping…** until the request has finished stopping. A calculation
already in progress may take a moment to stop. Other users' requests are unaffected,
and an unsent draft stays in the input. Loaded models may remain in memory for later
questions. Deployments using the separate queue worker must update that worker too.

Submitting a question creates a server-side job. It continues if the user
switches chats, reloads the page, or closes the submitting tab. Questions in
the same chat remain ordered, while another chat may submit a second queued
question.

The default is one global first-in, first-out model queue. The header shows the
number of running and waiting jobs. Clicking it opens a list of active users,
states, positions, and processing stages without exposing their questions.
The current user's chat list shows a spinner for queued or running work.

A process restart marks unfinished work as interrupted instead of silently
reissuing an unknown partial model request. Deleting a chat with active work is
rejected.

## Evidence display

Ordinary research chat retrieves passages and cites the papers it uses. It does
not require a certificate of independent corroboration. If the answer cannot
be validated, the reader gets excerpts with their source and page when
available, or a note that this search did not locate a matching passage.

Start the service with `./web --evidence-selector trace_flow` to opt in to the
separate study-origin audit. Its primary outcomes are:

- `Supported`
- `More evidence needed`
- `Study overlap needs review`

Implementation details remain behind the evidence and study-overlap views.
`Independent studies: ?` means that publication, cohort, trial, or dataset
overlap has not been resolved sufficiently for an exact count.

Paper-search questions run the same automatic overlap screen, but do not claim
to have run the joint sufficiency certificate used for synthesized answers.
Extracted study fields are grounded in the displayed excerpts and are not
independently adjudicated full-paper annotations.

## Sources and outputs

Inline citations use one linked author-year label. The link opens the DOI,
PubMed, or PubMed Central record when available. The source panel identifies
local and missing papers and provides a conservative document-design label.

Each completed turn saves inspectable artifacts, including the answer,
retrieved passages, evidence table, quality-control result, and run
configuration. These files are local operational data and must not be
committed.

Automatic publication-family and shared-resource signals are display and
review aids. They do not silently convert an uncertain relation into a trusted
scientific-origin assignment. Absence of an overlap signal is not proof of
independence.

## First-answer walkthrough

Complete the PDF import and index steps in [Getting started](getting_started.md)
before this walkthrough. For a malaria collection, enter:

```text
What evidence links var genes to severe malaria?
```

1. Read the answer and open a linked author-year citation. Confirm the report's
   identity and inspect the source passage used for the statement. An excerpt
   is a passage from a report, not another study.
2. In optional audit mode, read the evidence status. `Supported` refers to the declared evidence
   criteria; `More evidence needed` means these were not met. `Study overlap
   needs review` means that unresolved source relationships matter. Available
   reports, selected passages and cited reports can have different counts.
3. In that mode, open **View study overlap**. Inspect the reported publication-family or
   shared-resource signals. Two versions of one publication do not supply
   two independent studies. An unknown independent-study count remains
   unknown until the available provenance supports a stronger conclusion.
4. Check the cited full text before reusing a study detail in a publication.
   The About dialog explains these distinctions without requiring knowledge
   of the mathematical certificate.

This is a walkthrough of the interface, not a saved answer or a promise that
every corpus will contain matching evidence. As a concrete publication-family
example, the manuscript's exploratory control used the Guillochon preprint
(`10.1101/2021.11.08.467248`) and journal version (`10.1093/infdis/jiac086`).
If both are in your collection, their common title and dataset accession can
be inspected in the overlap view. The retained control treated them as one
publication family while leaving other origin relations unresolved. Merely
finding two DOI records does not establish an independent-study count.

## Recommended first questions

```text
What evidence links var genes to severe malaria?
```

```text
Which PfEMP1 features are associated with cerebral malaria?
```

```text
Find papers about RIFIN binding to human immune receptors.
```
