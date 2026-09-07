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

If the library is empty, or contains only bibliographic records, the quick tour
explains what is missing. **Add papers** accepts PDFs locally without an account,
or after signing in when the server uses authentication.
Accepted uploads are checked, named, categorised and indexed automatically.
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

Search returns up to 12 bibliographic records. In an upload-enabled account,
click **Add to library** on a result to find and import an open-access PDF.
The DOI is sent to OpenAlex and Europe PMC. If the host configures
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
PDFs that fail this check require manual upload rather than automatic acceptance.

Accepted PDFs go through the same identifier, duplicate, naming and indexing
checks as uploads. A metadata-only record is supplemented while retaining its
paper ID and citation key. Existing assets are not overwritten. A downloaded
file is not reported as ready until library processing succeeds.

If no suitable copy can be obtained, **Upload PDF** provides the manual route.
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

The normal interface chooses the evidence workflow automatically. Users do not
select a separate comparison or study-counting mode. The primary outcomes are:

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
2. Read the evidence status. `Supported` refers to the declared evidence
   criteria; `More evidence needed` means these were not met. `Study overlap
   needs review` means that unresolved source relationships matter. Available
   reports, selected passages and cited reports can have different counts.
3. Open **View study overlap**. Inspect the reported publication-family or
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
