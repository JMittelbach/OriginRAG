# Your research workspace

Start the web application with `./web`. In the full installation, the same top
navigation connects the enabled areas. Standalone Quill or OriginRAG installations
omit the suite switcher and retain their own Library, Bookshelf, **Appearance**,
**Settings** and, on installations requiring sign-in, account controls. Local
use without sign-in hides the account button across all enabled areas. An
authenticated server retains it, including when accessed through an SSH tunnel.
`/workspace` opens your preferred start page, chosen in **Settings**.

- **OriginRAG**: search, ask questions and inspect study overlap.
- **Bookshelf**: your saved papers, linked to the existing reader and private notes.
- **Library**: search the shared corpus and create personal project collections.
- **Quill**: edit a LaTeX project with its PDF preview alongside the source.
- **Folio** (full installation): write formatted documents visually and exchange
  common document content with Word through DOCX.

## Write a formatted document with Folio

Open **Folio → New document**, choose a starter, or import a DOCX or Folio JSON
file. Use the ribbon to format text and paragraphs. Changes save automatically;
wait for **Saved** before closing the page. Ctrl/Cmd+S saves immediately.

**Home → Format painter** copies the first selected text's formatting and
paragraph spacing. Select the destination text to apply it once, or press
Escape to cancel. Links, note identities and heading structure stay intact.
Use Undo to revert it. Type a font size directly (6–96 points), or use the
grow/shrink buttons. The **¶** button shows paragraph endings only in the editor,
not in printed or exported documents.

To save a file on your computer, choose **File → Save as…** or press
**Ctrl/Cmd+Shift+S**, enter a filename and choose **DOCX**, **HTML**, **text** or
**JSON**. Autosave still saves the document in Origin. The local file is an
exported copy, not a file that Folio keeps automatically in sync; use **Save as…**
again to export later changes.

Supporting desktop Chromium browsers can show a native save picker in a secure
context, using the [File System Access API](https://developer.chrome.com/docs/capabilities/web-apis/file-system-access).
Firefox, Safari and browsers without that API use a download instead. The
browser's download preferences determine whether it asks for a destination or
saves to its usual folder; Folio cannot force an operating-system picker.
For PDF, choose **File → Print or save PDF** and select a PDF destination in the
browser or system print dialog.

JSON preserves the editor document for backup and recovery, not arbitrary Word
features. Documents belong to the signed-in account, or the local workspace
when no account is required. Their database is excluded from Git.

Select an image to open **Picture**. Change its dimensions and description,
lock or unlock the aspect ratio, reset its size, add a caption, or remove it.
**Page width** and **Half page** resize proportionally to the writing area.
Wide images scale down to fit the available space. These controls resize the
image; they do not crop it or turn it into a floating object.

Place the cursor in a table to open **Table**. Insert or delete rows and columns,
select cells, merge or split them, change cell shading, and toggle header rows
or columns. Drag column boundaries to resize columns. Captions are ordinary
editable paragraphs with manual numbering, not automatic Word caption fields.
**Insert → Table** also offers a size grid: hover or use the arrow keys, then
click or press Enter. Numeric fields support up to 20 rows and 10 columns,
with an optional header row.

**Draw** opens a canvas with pens, a highlighter, an eraser, colour and thickness
controls, and undo/redo. Use the pen presets to start quickly. Mouse, trackpad,
pen and touch input are supported. Inserted sketches become embedded PNG
pictures; their individual strokes are not editable afterwards.

**Insert → Shapes** adds a rectangle, rounded rectangle or ellipse. Right-click
it and choose **Format Shape**, or double-click it. The pane on the right lets
you change its fill, outline and dimensions. Select another object to keep
working in the same pane. Pictures and drawings open **Format Picture** for
size, aspect ratio and alt text. Undo works for pane edits.
Shapes stay in the text flow and export as editable DOCX shapes.

**Design** offers four document-wide formatting sets with previews. They format
existing text and headings while retaining emphasis, links and notes. Undo
restores the previous formatting. These are explicit formatting presets, not
Word's inherited document themes. **Layout → Paragraph spacing** adjusts space
before and after paragraphs, line spacing and indentation.

**References** inserts footnotes and endnotes with separate automatic numbering.
Click a note marker or its text to edit, change type or remove it. Notes contain
plain text, up to 4,000 characters each and 500 notes per document. They appear
after the document in the editor and browser printing. DOCX exports contain
native footnotes and endnotes for Word to lay out in their usual positions.

Apply heading styles, then choose **References → Table of contents** to insert
linked contents at the start of the document. **Update table of contents**
refreshes the entries after heading changes. **Insert cross-reference** inserts
linked heading text. These are ordinary hyperlinks, not automatically updated
Word fields: reinsert a cross-reference if its heading is renamed. Contents
do not invent page numbers. **Insert → Symbol** provides common mathematical
and Greek characters without treating them as HTML.

**Review** provides document comments and browser spelling checks. Comments
retain a quote from the selection, not an anchor that tracks subsequent edits.
Select a passage to see its word count in the status bar. Click the count
to compare words and characters in the selection and the whole document.
**View** provides the heading outline and zoom. The editing surface is continuous;
page settings and explicit page breaks control the printed output. Pagination
and font metrics can differ from Word, and depend on installed fonts and browser.

In **Find and replace**, Enter moves to the next match and Shift+Enter to the
previous one, keeping the search field ready for typing. Escape closes the
search panel and returns to the document.
With a short passage selected, Ctrl/Cmd+F starts at that occurrence. Pressing
it again in the panel refocuses the search without clearing your replacement.
Matches are highlighted without changing document formatting. **Match case**
and **Whole words** narrow the search; **Replace current** changes one match,
while **Replace all** remains a single undoable action.

If saving fails, keep the tab open. Retry **Save**, or use **File → Save a copy**
after a conflict to create a separate document in Origin. **Download recovery JSON**
preserves the current buffer even when the server cannot save it. A newer saved
revision is never silently replaced.

### Word compatibility

Folio is an independent editor, not an implementation of every Word feature.
The comparison below separates supported editing from features that require a
different document or layout model. Microsoft also distinguishes its
[desktop and web feature sets](https://support.microsoft.com/en-us/word/word-features-comparison-word-for-the-web-vs-desktop.).

| Area | Folio support and boundaries |
| --- | --- |
| Text and paragraphs | Common character formatting, fonts, colours, headings, lists, alignment, before/after spacing and indentation. Arbitrary Word themes and custom style inheritance are not reproduced. |
| Design | Four document-wide formatting presets with previews and undo. Word theme inheritance, watermarks and page borders are unsupported. Origin's Appearance setting changes the interface rather than the document. |
| Drawing | Pen, highlighter, eraser, colour, thickness, undo/redo, and mouse/trackpad/touch input on a drawing canvas. Sketches are inserted as PNG pictures, not editable Word ink. |
| Insert objects | Basic in-flow shapes, pictures, drawings, tables, links, dates, symbols and manual captions are supported. Floating shapes, shape text, SmartArt, charts and editable equations are unsupported. |
| Images | Embedded PNG, JPEG and WebP, size, aspect ratio and descriptions. No floating text wrap, cropping, rotation, SVG/EMF editing or grouped drawing objects. |
| Tables | Editable cells, shading, header cells, column widths and merged/nested tables. No formulas, Excel embedding or Word's complete table-style system. Marking a header does not guarantee repeated headers across browser-printed pages. |
| Layout/pages | A4/Letter, orientation, margin presets and explicit page breaks. No section breaks, section-specific layouts, text columns, running headers/footers or dynamic page-number fields. |
| References | Numbered plain-text footnotes/endnotes with native DOCX export, refreshable linked contents, heading cross-references and manual captions. No Word TOC/citation/cross-reference fields, page-numbered figure lists or index generation. Browser printing collects notes at the end. Quill's bibliography integration is separate. |
| Review | Quoted document notes and browser spelling checks. No tracked changes, document comparison, grammar engine or revision-history browser. |
| Collaboration | Private per-account documents and conflict detection. No shared document permissions or live coauthoring in Folio. |
| Other Word features | No mail merge, macros, ActiveX/OLE objects, dictation or password-protected DOCX editing. |

Importing creates a new Folio document and does not modify the source file.
Keep the original DOCX, especially for complex manuscripts. Inspect import
warnings: unsupported structures can be simplified or omitted. A successful
import means that editable content was loaded, not that the original layout,
all document parts or every Word feature survived. Externally edited DOCX files
are parsed again rather than trusting stale Folio round-trip metadata.

These distinctions matter particularly for Word's
[anchored and wrapped pictures](https://support.microsoft.com/en-gb/word/wrap-text-and-move-pictures-in-word),
[automatic captions](https://support.microsoft.com/en-gb/word/add-format-or-delete-captions-in-word)
and [repeated table headings](https://support.microsoft.com/en-gb/word/repeat-table-header-on-subsequent-pages).
The corresponding Folio controls should not be interpreted as full support for
those mechanisms.

## Write a LaTeX manuscript with Quill

1. Open **Quill → New manuscript**, or import a LaTeX project ZIP.
2. Edit the source. Changes save automatically; wait for **All changes saved**
   before closing the page. Ctrl/Cmd+S saves immediately.
3. Add text files or upload figures and supporting LaTeX files. Choose the main
   `.tex` document in the file sidebar.
   **↑ Upload** accepts `.tex`, `.bib`, `.sty`, `.cls`, `.bst`, `.bbl`, `.txt`,
   `.md`, `.csv`, PNG, JPEG and PDF files. Individual uploads are limited to 8 MB,
   with 200 files and 12 MB total per project. A batch is checked before any file
   is added; duplicate filenames (including case-only differences) are rejected.
   These are LaTeX project assets, not editable Word documents. Use Folio for DOCX.
4. Click **Recompile**, or enable **Auto-compile**. Auto-compile waits briefly
   after typing and compiles saved source, not an older unsaved buffer.
5. Inspect **Errors, warnings & compile log**. File/line diagnostics jump to the
   corresponding source; error lines are marked in red. Drag the panel's top
   edge to resize it, or focus the divider and use the up/down arrows.
   Quill offers an immediate quick fix only for a few clear single-line mistakes,
   such as an extra closing brace. The error panel never calls a language model.
   **Apply fix** makes one undoable edit and recompiles. For anything ambiguous,
   only the compiler diagnostic is shown. A failed build keeps the previous PDF
   labelled as out of date.
6. **Download LaTeX ZIP** saves the manuscript first, then downloads all source
   files, bibliographies, classes, styles and figures in one archive. **PDF ↓** downloads the last
   successfully compiled PDF. **Download draft** preserves the current buffers
   as JSON, including unsaved changes, for recovery after a save conflict.

If a connection or temporary server error interrupts saving, the draft stays
in the editor. Choose **Retry saving** to resume. Quill checks for concurrent
edits before accepting the save, including when an earlier save reached the
server but its confirmation was lost. Conflicting edits still require review.
Saving unchanged content keeps the existing revision and avoids rewriting the
project database. A retried save that has already been applied behaves the same
way; revision and conflict checks still run.

Unchanged successful builds reuse a cached PDF from your own project.
**Shift-click Recompile** requests a fresh build. A bibliography error or unresolved citation
is reported even if the LaTeX engine produced a PDF; it is not presented as a
successful new preview.

Independent manuscripts can compile simultaneously. The default is two compiler
workers (one on a single-CPU host); the host can set
`ORIGINWRITE_MAX_PARALLEL_BUILDS=4` before starting the service. Values are limited
to 1–8 workers per web-service process. Up to 16 additional builds can wait in
arrival order, for at most 60 seconds, instead of immediately returning a busy
error. A full or expired queue still returns a retry message. Each active build
retains its existing isolation, 45-second execution and resource limits.
Four workers therefore allow four builds at once, not unlimited compilation.
Rootless Linux limits each engine process's virtual address space to 768 MiB;
container builds limit memory per container to 768 MiB. These are not aggregate
RAM guarantees for the application. Choose the worker count to leave room for
the web process, other users and additional services.

Simultaneous requests for identical sources in the same account and project
share one running build, including repeated **Recompile** clicks. Different
projects or accounts never share build results. Successful completed builds
remain eligible for the existing bounded cache; errors are not cached. Shared
build waiters are also bounded so repeated clicks cannot create unlimited
waiting requests. This coordination is process-local: multiple service
processes each have their own worker limit.

## Choose a template

The template gallery includes Research article, Bachelor/Master thesis, Bioinformatics, G3, PLOS ONE,
PLOS Computational Biology and PLOS Genetics. Search by journal or filter by
publisher. PLOS entries are lightweight Quill starters with the official
PLOS BibTeX style, not publisher-endorsed submission checks. Every template opens
as a new manuscript, leaving the current project unchanged.

The thesis starter uses KOMA-Script (`scrbook`) with an independent, neutral
Quill style: restrained sans-serif headings, a serif body, fine running-header
rules, a spacious title page, author–year references and separate chapter files.
Abstract, contents, figure/table lists, a list of abbreviations and an appendix
are included. Edit `frontmatter/abbreviations.tex` to replace the example entries
with your own abbreviations in alphabetical order. The list is included in the
contents and needs no separate glossary/index build step. There are
no logos or fixed institutional details. Edit `metadata.tex` to set the author,
subtitle, supervisor and Bachelor or Master degree. University, department and
second supervisor are optional and hidden when empty. It is a generic starter,
not an approved university format:
check your own formatting rules and required declarations. Prepare its compiler
packages with `python scripts/prepare_writing_templates.py --warm-cache --template thesis`.

Hosts preparing an offline compiler can warm just the new templates once with
`python scripts/prepare_writing_templates.py --warm-cache --template plos-one --template plos-compbiol --template plos-genetics`.
This allows package downloads only for the trusted bundled starters. Normal
manuscript compilation remains offline.

## Connect your papers

Personal libraries are collections of papers from the shared corpus, private to
your account. A paper can belong to several libraries without duplicating its
PDF. Removing it from a personal library does not remove the corpus paper or its
bookshelf bookmark.

In **Library**, open a collection and select **Browse corpus** to add existing
papers directly. You can also drag a paper onto a collection in the sidebar.
Drop PDFs onto a collection or its card under **Quill → My libraries**:
accepted PDFs are imported into the shared corpus and linked to your private
collection automatically. Imports still require the host to enable PDF uploads.
Papers needing review are not silently added. For PDFs already in the corpus,
use **Browse corpus** to link the existing record.

Library search updates as you type and keeps the query in the page URL. A search
that finds no papers offers a clear-search action and paper import. A connection
error offers **Retry search** without navigating away.

**Find or import papers** shows import progress and recent import results. After
your PDFs have uploaded, you can close the panel and return to check progress.
Temporary connection failures trigger another progress check automatically.
Failed downloads offer **Retry import**. If uploaded files are waiting for
processing, choose **Resume library update**. When one file in a batch fails to
upload, files already accepted by the server are still sent for processing.

The manuscript list shows the last edit time, **Download ZIP** and, on a shared
server, **Share → Add a collaborator**. Only the owner can delete a manuscript,
and deletion asks for confirmation twice. Collections and corpus papers remain.

Inside a manuscript, place the cursor where the citation should go before opening
**References**:

1. Open **References** and choose a **Personal library**, or enter a name and
   select **+ New personal library**.
2. Open **Search corpus**, search by title, author or journal, and choose
   **Add to personal library** beside the papers you need.
3. Return to **Your library** and choose **Insert citation** for a paper.
   Automatic sync normally already supplies its bibliography entry, so Quill
   inserts `\cite{...}` directly at your editing position.
4. If you deliberately use manual bibliography mode, review the preview and
   choose **Apply sync and insert citation** first.

**Keep bibliography synced automatically** is on by default. Linking a personal
library populates the bibliography, and later library membership changes update
its linked manuscripts in the same database transaction, including closed ones.
New papers are added and unedited generated entries receive current metadata.
The editor checks for updates about every 15 seconds while visible, and on
returning to the tab. Opening, saving, compiling and ZIP export reconcile the
linked bibliography again. No LLM or always-running background worker is needed.
Metadata changed outside the application is reconciled on access, not by a
continuous filesystem watcher.

The default target is `references.bib`. An existing single bibliography target
is respected. For multiple files or a custom setup, enter the desired `.bib`
filename in **References → Bibliography file** and choose **Save sync settings**.
Straightforward documents are connected automatically on the first citation,
when they have no bibliography configuration. Merely collecting papers does not
start an empty BibTeX run. Existing configurations are not replaced; custom
classes or dynamic includes may need a manual connection in the source.

Handwritten entries and manually modified generated entries are never silently
overwritten. Conflicting or malformed entries trigger a persistent **Review
references** warning. Other safe additions can still sync, but compilation is
held until you resolve the issue or deliberately select manual mode. Recovery
ZIPs and draft exports remain available. Missing corpus records are reported,
not used as a reason to erase saved references. Entries for removed papers are
also retained, so existing citations do not break.

For a deliberately manual bibliography, clear **Keep bibliography synced
automatically** and choose **Save sync settings**. The existing preview/apply
workflow remains available. Concurrent edits are merged only when safe; a
conflict keeps the draft and requires review rather than overwriting it.

New citation keys use the first author's family name and year, for example
`Doe2026`. If that name is taken, Quill allocates `Doe2026a`, `Doe2026b`, and so on.
Keys are stored per account and reused across personal libraries. Later metadata
corrections or collection changes do not rename them. Already-used keys from
older Quill projects are preserved, as are imported or handwritten `.bib` keys.

After syncing references, type `\cite{`, `\citep{`, `\citet{`, `\parencite{` or
`\textcite{` directly in the source. Suggestions use the project's `.bib` files
and display author/year details with the title on selection. Type part of a key,
use the arrow keys, then Enter to insert it; Ctrl+Space requests suggestions and
Escape closes them. Comma-separated citations are supported. This runs locally
in the browser and does not query a model. Unsynced corpus papers must first be
added through **References** so that the citation has a real bibliography entry.
Quill does not invent an entry just because a key was typed.

Citation keys remain stable for the same corpus paper. The references panel
shows each key and flags incomplete metadata. Check author names, titles,
publication types and other fields before submission. Personal libraries can
also be managed from **Library** and reused across manuscripts.

## Start from a template

Browse the scrollable **Templates** gallery from Quill or an open project.
Choose a template to create a **new manuscript** with its source, class files,
bibliography styles and licences already included. The current project is saved
and kept separately; selecting a template does not replace its files.

The bundled gallery includes:

- **Research article**: a lightweight, general-purpose LaTeX starter.
- **Bachelor / Master thesis**: configurable degree details and separate chapter files.
- **Bioinformatics**: a Quill starter using the official OUP authoring class.
- **G3 · OUP authoring**: a starter using the generic OUP class offered by G3,
  not the separate GSA template.

Templates load directly inside Quill, without downloading a ZIP yourself.
The included OUP distribution retains its publisher licence and original files.
Check the journal's author instructions before submission; a working template
does not establish compliance with every journal requirement. Other lawfully
obtained LaTeX templates can still be imported as project ZIPs.

## Make the editor yours

Drag the separators to resize the file sidebar and the code/PDF panes. Choose
**Side by side** to adjust pane widths, or **Stacked** to adjust their heights.
Each arrangement remembers its own proportions. Double-click the code/PDF divider
for equal panes, or focus a separator and use the arrow keys. **Files** hides or restores the
sidebar. **Code & PDF**, **Code only** and **PDF only** control which panes are
visible; **⇄** swaps their positions. Small screens stack the panes automatically.

Change the code font size with its zoom controls, independently of the PDF zoom.
Neither changes the document's printed font size or page layout. **◐** switches
the code editor between light and dark. Display preferences are remembered in
this browser, not saved as manuscript content.

The code font-size field accepts any positive finite pixel value, including
decimals such as `9.5` or `32`. There is no fixed 11–24 px preset limit. Enter
applies the value; Escape restores the previous size. **−** and **+** adjust it
in one-pixel steps, or halve/double values below one pixel. Zero, negative and
invalid values leave the last valid setting intact. **Reset layout** restores
11 px if you zoom too far. New editor views default to 11 px unless you have
saved another code font size in this browser. This does not change the LaTeX
document's printed font size.

The editor includes LaTeX syntax colours, line numbers, bracket matching, command
suggestions, and separate undo history for each open file. Click a heading in
**File outline** to jump to its source. Multiline titles are shown together;
headings in commented lines and common verbatim environments are skipped.
The toolbar provides bold/italic wrapping,
commenting, undo/redo and find/replace. **References** connects papers to the
manuscript; project settings remain below the file tree.

- Ctrl/Cmd+F: find and replace in the current file.
- Ctrl/Cmd+Z / Ctrl/Cmd+Shift+Z: undo / redo.
- Ctrl/Cmd+/: toggle a LaTeX comment.
- Ctrl/Cmd+S: save; Ctrl/Cmd+Enter: recompile.
- PDF **Fit width** or percentage zoom changes the preview without recompiling.

**Download LaTeX ZIP** is in the manuscript toolbar. Draft download and
**Reset layout** are in the **•••** project menu.
The editor is bundled locally using CodeMirror. There are no CDN requests or
additional runtime dependencies. Bundled licences are in
`assets/workspace/EDITOR-LICENSES.txt`. To rebuild the bundle with Node.js, run
`npm ci --ignore-scripts` and `npm run build` from `assets/workspace/editor-build`.
The lockfile records the exact dependency versions. Normal installation does not
require this build step.

Projects and personal collections are stored per account in `.writing.sqlite3`
under the existing private user-data directory, excluded from Git. They are not
stored in the shared corpus. Back up that directory along with account data.

## Back up a manuscript to Dropbox or another folder

In an open manuscript, choose **••• → Backups / Dropbox folder**. **Download
backup ZIP** works in every supported browser. It saves the current manuscript
first and exports a revision-checked ZIP with all source files, bibliographies
and uploaded figures. Each backup has a timestamp, project identifier and
revision in its filename. Import a backup using Quill's **Import LaTeX ZIP** action.

Chromium browsers on localhost or HTTPS also offer **Choose backup folder…**.
Select a folder inside the Dropbox folder on **your own computer**, then click
**Save backup**. The Dropbox desktop application handles uploading that folder.
This works even when Quill itself runs on a shared server. It is a local-folder
integration, not Dropbox OAuth or a direct cloud-upload service; Quill neither
stores Dropbox credentials nor verifies remote sync completion.

Optionally enable backups of changed revisions every five minutes while the tab
is open. Folder access and this setting last for that tab only. Closing the tab,
losing access to the folder or a failed backup stops automatic copies. Firefox
and Safari use ZIP downloads instead. Existing backup files are never deleted
automatically; monitor space in the chosen folder.
Unchanged revisions are skipped only after a successful copy to the selected
folder. A separate ZIP download does not replace that folder backup.

Only the open manuscript is included, not the shared PDF corpus, user accounts,
shelves or private reader annotations. Treat exported ZIPs as private research
data. A shared server still needs its own administrator-managed backup.

## Ask about selected text

Select a passage in the editor, right-click and choose **Ask Quill…**, or use
the **Ask Quill** toolbar button. The project chat opens with that passage attached
and an empty input. Type your own request, such as a spelling correction,
a clearer formulation, a translation or an explanation. No fixed proofreading
mode, prewritten prompt or automatic model call is involved.

The attached passage is visible and removable. While attached, proposed changes
are restricted to its exact position in its file, including when the same text
occurs elsewhere. It supports selections up to 8,000 UTF-16 units. Other relevant
project files may still be supplied as context; they cannot be changed by this
selection-scoped request. If the passage changes before sending, select it again.
Remove the selection chip to ask about the whole project instead.

Answers stream into the chat. Suggested text appears directly at its location in
the LaTeX editor: the original is marked and the replacement is shown underneath.
Choose **Accept** or **Reject** there. Accepting needs no second browser confirmation.
During generation the preview is provisional and Accept stays disabled. It is not
part of the document, autosave, compilation or exports until accepted.
**Stop** or closing a running chat discards unfinished previews. Completed
suggestions remain reviewable in the editor when the chat is closed. Normal
autosave, conflict checks and per-file Undo remain available. Generated wording,
claims and LaTeX can be wrong.

## Chat about a Quill project

Inside a manuscript, choose **Project chat**. Ask about the source or request a
rewrite, a LaTeX table or an explanation. The panel is separate from OriginRAG's
evidence-synthesis chat. Drag its title to move it within Quill, drag either lower
corner to resize it, or choose **↗** to open the same conversation in a separate browser window.
Resize grips also support arrow keys. Detached windows resize using the browser's
normal window edges.
You can move that window onto another screen. **↙** docks it back without losing
the conversation. A browser that blocks pop-ups leaves the docked chat usable.
No second model or duplicate inference request is started by moving the window.
The window stays connected to its open editor. Closing the editor or switching
projects clears the detached window's project context and conversation; a link
lets you reopen the original manuscript. Closing only the detached window keeps
the conversation in the editor session, ready to reopen.

Choose **+ New chat** to start another conversation about the same project.
Click the chat name to open a compact list: switch chats, rename one with **✎**,
or delete one with **×** and an inline confirmation. Names initially come from
the first question. Each chat keeps its own history, selected passage, unsent
draft and suggestions. New chats receive the current project but no other chat's
conversation. Deleting a chat stops its reply and removes its unaccepted
suggestions; document edits already accepted and editor Undo are untouched.
Chat switching and deletion pause briefly while an accepted change is saved.

Replies continue when you switch chats or hide the panel. The list shows
**Running** or **New reply**. **Stop** affects only the selected chat. Only its
suggestions appear in the editor; background chats cannot replace the current
preview. If another accepted change or manual edit makes a proposal stale, it
cannot be applied. Ask again using the updated draft.
Quill admits at most two concurrent project-chat requests per web service;
further requests receive a busy message. Ollama may process admitted requests
in parallel or queue them according to its own configuration and available memory.
Opening another chat never downloads or creates a second copy of the model.

Write your own question or change request. The chat uses the current project and
the workspace's selected model automatically. There are no file pickers, model
menus or spelling-check modes in the chat. Opening it does not wait for model or
context-list requests. Enter sends a question; Shift+Enter inserts a line break.
Questions ask for explanations; explicit change requests can produce reviewable
proposals. Nothing is applied automatically.
The assistant has no terminal tools, web search or access to other projects.

Context is assembled from the saved current draft for each request. Small projects
are supplied in full. Larger projects use bounded, located source excerpts,
prioritising the main preamble, named/current files and any attached selection.
The model is told which text is partial and must not claim to have read omitted
lines. Changes to unsupplied text are rejected. Large inventories and older whole
conversation turns are bounded too; the visible transcript is kept. PDFs and
images contribute filenames only, not their contents. Corpus PDFs and personal
reading notes are not automatically sent. Use the normal **Library** and
**References** tools to find papers and synchronise project bibliography keys.
This is a writing aid, not OriginRAG's evidence-sufficiency certification.

**ORIGINWRITE_CHAT_CONTEXT** sets the requested window (32,768 tokens by default,
8,192–65,536 allowed); the byte bound intentionally leaves room for response and
template tokens and can reject some text that a tokenizer would fit.

Replies stream into the panel; code previews stay in the editor, not the chat.
Each inline proposal has **Accept** and **Reject** controls. **Next** navigates
between proposals in different files without applying them. Accept changes only
that proposal, then preserves and repositions the remaining proposals. Proposals
must match unique existing passages in their allowed scope (the attached selection,
if present, otherwise the included file); they cannot create,
rename or delete files. Invalid or overlapping proposals are discarded as a set.
Numbers and citation changes receive a review warning. The project version and
access permissions are checked before delivery and again before applying.
Repeated text is disambiguated using the validated selection position, not a
global search-and-replace.
Introduced citation keys must exist in an included project bibliography.
Typing, remote source updates or Undo invalidate stale pending previews rather
than applying them to a changed draft. Changes use normal autosave, conflict
handling and per-file Undo. Use the toolbar's Undo/Redo buttons or Cmd+Z on macOS
(Ctrl+Z on Linux) in the editor. Compile through
Quill's normal isolated compiler to check generated LaTeX; the model does not
execute code or establish scientific correctness.

Change the workspace model through Quill's normal **Models** button. Regular Qwen
can be used; a Coder model is optional, not a guarantee of valid LaTeX. If the
configured model is unavailable and exactly one Ollama model is installed, that
model can be used for the request without changing the shared selection. If a
choice is needed, the reply offers a **Models** button instead of leaving Send
permanently disabled. Missing models are never downloaded automatically.
Named chats stay in this project's open editor session,
are not stored in the project or shared with collaborators, and are lost when
the editor is closed, reloaded or switched to another project.
Only a bounded set of recent complete messages accompanies each request. The same
**ORIGINWRITE_LANGUAGE_CHECK=0** switch disables both writing assistants.

There is no fixed number of turns and no automatic summary or compaction of old
messages. Each request includes at most the latest eight messages (usually four
question/answer pairs), capped at 12,000 characters in total and 4,000 per message.
The project-context budget can reduce that further. Older messages remain visible
in the panel but are no longer model context; repeat important instructions when
needed. Project text is assembled afresh, independently of this conversation
window. New chat resets the conversation, not the model installation or your files.

## Write together

On an authenticated server, open a manuscript and choose **Share**. Search for
an existing account by username or display name, then add it as an editor.
The owner can add up to 20 editors and remove access again. Copy the project
link to send it to them. A link alone does not grant access: recipients must
sign in to an account that has been added to that manuscript.

Shared manuscripts appear on each editor's writing desk. Editors can work on
the source and figures, compile the manuscript, and download its LaTeX ZIP.
They cannot invite other people or browse the owner's private collections,
bookshelf or notes. The owner manages the linked personal library and its
bibliography sync; the shared `.bib` files remain editable by all collaborators.
Account search exposes only usernames and display names, not email addresses.

Changes synchronise automatically every few seconds while the manuscript tab
is visible. Edits to different files or separate passages are merged. If two
drafts change the same passage incompatibly, saving pauses and the local draft
stays in the editor. Download that draft before explicitly reloading the saved
version and reconciling the conflicting passage. A long-disconnected tab may
also need this step. This is automatic document synchronisation, not a
per-keystroke remote-cursor view.

Removing an editor blocks subsequent project requests. It cannot retract copies
that person has already downloaded. Personal local use needs no account; project
sharing requires a server started with `./web --auth`.

The sharing database, `.writing-collaboration.sqlite3`, lives beside the private
user-data directories. Back it up together with those directories and the account
database. Manuscript source remains in the owner's existing writing database;
sharing does not duplicate it into every collaborator's account.

## Importing and exporting projects

Projects accept UTF-8 `.tex`, `.bib`, `.sty`, `.cls`, `.bst`, `.bbl`, `.txt`,
`.md` and `.csv` files, plus `.pdf`, `.png`, `.jpg` and `.jpeg` assets. ZIPs with
other files, symlinks, unsafe paths or duplicate paths are rejected, not partially
imported. Remove unsupported packaging files from a copy if needed. Limits:
12 MB and 200 files per project, 100 MB of serialised source data per account,
150 projects/collections per account, 500 papers per collection. The browser
preview supports up to 1,000 PDF pages and keeps at most four page canvases
resident at a time. Distant pages are released and rendered again as you scroll;
the PDF download contains all pages.

## Configure PDF compilation

Editing and ZIP export work without TeX. Compilation requires isolation and
never falls back to unrestricted execution on the host. Compiler status describes
configuration, not a guarantee that the engine and all required packages are ready.
The following setup is for the person hosting the application, not each user.

With Tectonic installed, download the packages needed by the bundled templates
once, using their trusted starter documents:

```bash
python scripts/prepare_writing_templates.py --warm-cache
```

For a custom cache, set `TECTONIC_CACHE_DIR` to the same directory as
`ORIGINWRITE_TECTONIC_CACHE` when running that command. Use `--tectonic` to select
an executable outside `PATH`. Package preparation requires internet access;
ordinary manuscript compilation does not. Imported projects may require
additional packages, which the host must prepare separately with trusted source.
Use a fast local cache when the project storage is on a network filesystem.

After updating cache contents, update a small `.originwrite-cache-revision` file
inside that dedicated cache, or restart the web service, to invalidate cached
builds. For a changed container image under the same tag, update
`ORIGINWRITE_TEX_REVISION` or restart the service. Immutable image digests avoid
that ambiguity. Cached builds are private to the account and project, held in
memory for at most 30 minutes, and discarded on restart. The shared build cache
defaults to 32 MiB; set `ORIGINWRITE_BUILD_CACHE_MIB` between `0` (disabled) and
`128` to change that budget. This is a cache budget, not a limit on total server
or compiler memory. A fresh build is still available with **Shift-click Recompile**.

### macOS

Install Tectonic and warm its package cache using a **trusted** example document
with your required packages. For example, create a small local article and run:

```bash
tectonic --untrusted /path/to/trusted-example.tex
```

The web compiler uses the installed Tectonic inside a macOS filesystem/network
sandbox, with `--untrusted --only-cached`. It cannot fetch packages while building
an uploaded document. A missing package must be installed through the trusted
cache-preparation step. The standard cache is
`~/Library/Caches/TectonicProject.Tectonic`; older installs may use
`~/Library/Caches/tectonic`. Set `ORIGINWRITE_TECTONIC_CACHE` to a dedicated existing
cache directory if necessary. Never point it at your home or project directory.

The worker can read compiler/system resources and the chosen package cache,
and can write only in its temporary build directory. CPU and output-file limits
are enforced by the OS; wall time, total output size and resident memory are
monitored. The macOS memory watchdog is not a hard container memory quota.

### Linux / shared server

Keep Quill's small SQLite databases on durable local storage rather than a slow
network filesystem. PDFs and the shared corpus can remain on network storage.
For an existing installation, stop the web service and back up its workspace
directory first, then run:

```bash
python scripts/migrate_workspace_storage.py --service-stopped \
  --source-root /absolute/path/to/workspace-data \
  --destination /absolute/path/to/local/quill-state
```

This verifies each copied database and leaves the originals untouched. Set
`ORIGINRAG_WORKSPACE_STATE_DIR=/absolute/path/to/local/quill-state` in the service
environment before restarting. Include this new directory in regular backups.
Do not point it at temporary storage, and do not remove the setting to roll back:
after new edits, the original databases are stale. Migration is a maintenance
operation, never something to run while users are editing.

If the host permits unprivileged user namespaces, Tectonic can run in a rootless
Bubblewrap sandbox without Docker daemon access. Install administrator-approved
copies of Bubblewrap and Tectonic (system-wide or in your account), prepare a
dedicated package cache using a trusted document, then configure:

```bash
export ORIGINWRITE_BWRAP=/absolute/path/to/bwrap
export ORIGINWRITE_TECTONIC=/absolute/path/to/tectonic
export ORIGINWRITE_TECTONIC_CACHE=/absolute/path/to/dedicated/tex-cache
./web --auth
```

The sandbox has no host home directory, shared corpus, account databases, host
network or inherited secrets. Source and package-cache mounts are read-only.
Only temporary build/output directories are writable. It has a private process
namespace and no capabilities. Builds use `--untrusted --only-cached`, OS limits
on CPU, address space (768 MiB), file size and open files, plus the wall-time and
output-size watchdog. No namespace or compiler failure triggers an unrestricted
fallback. The configured cache must contain only compiler packages, not user data.
This setup is intended for private servers.

Alternatively, use a container engine:

Use a prebuilt local container image containing TeX Live and `latexmk`. A starting
Dockerfile is provided at `containers/originwrite/Dockerfile`:

```bash
docker build -t originwrite-tex:local containers/originwrite
export ORIGINWRITE_TEX_IMAGE=originwrite-tex:local
./web --auth
```

For a managed service, pass the same environment variable in its service
configuration. Use an administrator-reviewed image, preferably pinned by digest.
Building the image installs packages and needs network access. Document builds
do not: containers have networking disabled, a read-only root filesystem, a
read-only project mount, a separate output mount, no extra capabilities, no shell
escape, and process/CPU/memory limits. Images are never automatically pulled when
a user clicks Compile. A missing image or stopped daemon produces an error.
Docker daemon access is a host-administration privilege. Use a dedicated service
account and private deployment. Public multi-tenant hosting requires additional
security review.

Compilations use the bounded worker pool and queue described above. Each build is limited to 45 seconds,
768 MiB memory (container quota, per-process address-space limit with Bubblewrap,
watchdog on macOS), and bounded
output size. Temporary files are discarded after the response. Build logs are
returned only to the requesting account. LaTeX engine/package differences mean
that not every existing Overleaf project will compile unchanged.

## Continue on another computer

**Two local installations, no terminal:** on both computers open **Settings →
Document sync** and connect the same private folder in iCloud Drive, Dropbox,
Nextcloud or on a network drive. Select a suggested location or enter its
existing local path (it can differ between devices). The folder service must
already be set up on both machines and approved for your documents. Anyone with
access to that folder can read the shared files.

1. Open a Quill or Folio document on the first computer and click **Sync this document**.
2. Let the folder provider finish uploading before shutting that computer down.
   On the other computer, choose **Refresh → Open** in Document sync.
3. The linked editor sends saved changes to the folder after a short pause.
   Before closing the tab, click **Sync this document** to send the latest edits
   immediately. Use the same button to receive changes in an open editor.

Quill shares its sources, bibliography files and project assets. Folio shares
its text, layout, images and comments. This does not transfer chats, collection
links, accounts, revision history or the paper corpus. Conflicting offline
edits are not silently merged: **Open as copy** keeps the incoming version
separate. Disconnecting does not delete any documents. This is not a backup
history or live co-editing, and Origin cannot confirm a cloud provider's upload.

**One shared host instead:**
Keep one copy of the workspace on an always-on computer, such as a workstation
or a private server. On the first computer, wait for **All changes saved**
(Quill) or **Saved** (Folio). Open **Settings → SSH access (advanced)**
and enter the host's SSH name, for example `martin@workstation`. On the second
computer, run the displayed command, leave the terminal open, then open the
displayed address. Bookmark it to return to the same manuscript next time.
Only the browser and an SSH client are needed on the second computer.

The host must be awake, running Origin and reachable by an authorised SSH
connection (for example over your work VPN). If SSH reports the local port is
in use, stop your other local Origin instance or conflicting tunnel before
continuing. A shared host needs `--auth`; use the same Origin account on both
computers. A personal no-account workspace is separate from a signed-in
workspace. To move an existing document to a shared server, export and import
it once, then work on the server copy from both places.

**Working offline instead?** Export a Quill LaTeX ZIP or a Folio JSON file
and import it on the other installation. That's a copy, not a merge: edit on
one device at a time and transfer the latest version back. Don't sync a live
Origin database or the entire clone with rsync or a cloud folder.

## Local writing and remote retrieval

You can run an installation locally and keep a separate RAG installation on your
server. Accounts and collections stay on their respective installations.
Manuscripts stay local unless you explicitly link them in **Document sync**
or transfer an exported copy. The local sync helper only manages Origin's
document files in the selected folder.
