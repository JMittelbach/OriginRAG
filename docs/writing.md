# Your research workspace

Start the web application with `./web`, then choose **Workspace** in the header,
or open `http://127.0.0.1:8765/workspace` (use the port printed by your launcher).
The existing chat remains available at `/` and `/chat`.

- **OriginRAG**: search, ask questions and inspect study overlap.
- **Bookshelf**: your saved papers, linked to the existing reader and private notes.
- **Library**: search the shared corpus and create personal project collections.
- **OriginWrite**: edit a LaTeX project with its PDF preview alongside the source.

## Write a manuscript

1. Open **OriginWrite → New manuscript**, or import a LaTeX project ZIP.
2. Edit the source. Changes save automatically; wait for **All changes saved**
   before closing the page. Ctrl/Cmd+S saves immediately.
3. Add text files or upload figures and supporting LaTeX files. Choose the main
   `.tex` document in the file sidebar.
4. Click **Recompile**, or enable **Auto-compile**. Auto-compile waits briefly
   after typing and compiles saved source, not an older unsaved buffer.
5. Inspect **Errors, warnings & compile log**. File/line diagnostics jump to the
   corresponding source when the engine supplies a location. A failed build
   keeps the previous preview, explicitly labelled as potentially out of date.
6. **Download LaTeX ZIP** saves the manuscript first, then downloads all source
   files, bibliographies, classes, styles and figures in one archive. **PDF ↓** downloads the last
   successfully compiled PDF. **Download draft** preserves the current buffers
   as JSON, including unsaved changes, for recovery after a save conflict.

Unchanged successful builds can reuse a cached PDF from your own project.
**Recompile** requests a fresh build. A bibliography error or unresolved citation
is reported even if the LaTeX engine produced a PDF; it is not presented as a
successful new preview.

## Connect your papers

Personal libraries are collections of papers from the shared corpus, private to
your account. A paper can belong to several libraries without duplicating its
PDF. Removing it from a personal library does not remove the corpus paper or its
bookshelf bookmark.

Inside a manuscript, place the cursor where the citation should go before opening
**References**:

1. Open **References** and choose a **Personal library**, or enter a name and
   select **+ New personal library**.
2. Open **Search corpus**, search by title, author or journal, and choose
   **Add to personal library** beside the papers you need.
3. Return to **Your library** and choose **Insert citation** for a paper.
   When its BibTeX entry is missing, a preview
   shows the proposed bibliography changes first.
4. Review the preview, then choose **Apply sync and insert citation**. This adds
   the reference and inserts `\cite{...}` at your editing position.

The default target is `references.bib`; another `.bib` filename can be selected.
**Connect to the main document if it has no bibliography yet** can add the
bibliography commands for a straightforward project. Existing bibliography
configurations are left alone. Imported projects with custom classes, `biblatex`
or complex includes may need their existing setup adjusted manually.

To refresh references later, use **Preview bibliography sync**, inspect added
and updated entries, then **Apply bibliography sync**. Linking a library alone
does not rewrite the manuscript. Existing entries are retained, including those
for papers removed from the collection. Handwritten entries and manually edited
generated entries are never silently overwritten: conflicting keys block the
sync until you resolve them. If another window changes the project or library,
preview again before applying.

Citation keys remain stable for the same corpus paper. The references panel
shows each key and flags incomplete metadata. Check author names, titles,
publication types and other fields before submission. Personal libraries can
also be managed from **Library** and reused across manuscripts.

## Start from a template

Browse the scrollable **Templates** gallery from OriginWrite or an open project.
Choose a template to create a **new manuscript** with its source, class files,
bibliography styles and licences already included. The current project is saved
and kept separately; selecting a template does not replace its files.

The bundled gallery includes:

- **Research article**: a lightweight, general-purpose LaTeX starter.
- **Bioinformatics**: an OriginWrite starter using the official OUP authoring class.
- **G3 · OUP authoring**: a starter using the generic OUP class offered by G3,
  not the separate GSA template.

Templates load directly inside OriginWrite, without downloading a ZIP yourself.
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

The editor includes LaTeX syntax colours, line numbers, bracket matching, command
suggestions, and separate undo history for each open file. Click a heading in
**File outline** to jump to its source. The toolbar provides bold/italic wrapping,
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
or compiler memory. A fresh build is still available with **Recompile**.

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

One compilation runs at a time per web-server process. Additional requests get a
visible busy response, not a hidden queue. Each build is limited to 45 seconds,
768 MiB memory (container quota, per-process address-space limit with Bubblewrap,
watchdog on macOS), and bounded
output size. Temporary files are discarded after the response. Build logs are
returned only to the requesting account. LaTeX engine/package differences mean
that not every existing Overleaf project will compile unchanged.

## Local writing and remote retrieval

You can run an installation locally and keep a separate RAG installation on your
server. Separate installations do **not** synchronise accounts, collections or manuscript
edits between them. Transfer projects explicitly with ZIP export/import. No
background synchronisation or access to arbitrary files on your computer is enabled.
Local file access happens only through the browser's explicit file picker.
