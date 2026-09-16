# Your research workspace

Start the web application with `./web`, then choose **Workspace** in the header,
or open `http://127.0.0.1:8765/workspace` (use the port printed by your launcher).
The existing chat remains available at `/` and `/chat`.

- **OriginRAG**: search, ask questions and inspect study overlap.
- **Bookshelf**: your saved papers, linked to the existing reader and private notes.
- **Library**: search the shared corpus and create personal project collections.
- **OriginWrite**: edit a LaTeX project with its PDF preview alongside the source.

This is an initial writing prototype, not a replacement for all collaborative
editing features of Overleaf. The existing chat and reader are unchanged.

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
6. **Export ZIP** downloads saved source and assets. **PDF ↓** downloads the last
   successfully compiled PDF. **Download draft** preserves the current buffers
   as JSON, including unsaved changes, for recovery after a save conflict.

### Make the editor yours

Drag the separators to resize the file sidebar and the code/PDF panes. Double-click
a separator to reset its width, or focus it and use the arrow keys. **Files** hides
or restores the sidebar. Use the layout selector for **Code & PDF**, **Code only**
or **PDF only**, and **⇄** to swap their positions. On narrow screens the panes stack
vertically. **◐** switches the code editor between light and dark. These display
preferences are remembered in this browser, not saved as manuscript content.

The editor includes LaTeX syntax colours, line numbers, bracket matching, command
suggestions, and separate undo history for each open file. Click a heading in
**File outline** to jump to its source. The toolbar provides bold/italic wrapping,
commenting, undo/redo and find/replace. Project settings and the collection-based
bibliography controls are in the collapsible section below the file tree.

- Ctrl/Cmd+F: find and replace in the current file.
- Ctrl/Cmd+Z / Ctrl/Cmd+Shift+Z: undo / redo.
- Ctrl/Cmd+/: toggle a LaTeX comment.
- Ctrl/Cmd+S: save; Ctrl/Cmd+Enter: recompile.
- PDF **Fit width** or percentage zoom changes the preview without recompiling.

ZIP export, draft download and **Reset layout** are in the **•••** project menu.
The editor is bundled locally using CodeMirror. There are no CDN requests or
additional runtime dependencies. Bundled licences are in
`assets/workspace/EDITOR-LICENSES.txt`. To rebuild the bundle with Node.js, run
`npm ci --ignore-scripts` and `npm run build` from `assets/workspace/editor-build`.
The lockfile records the exact dependency versions. Normal installation does not
require this build step.

Projects and personal collections are stored per account in `.writing.sqlite3`
under the existing private user-data directory, excluded from Git. They are not
stored in the shared corpus. Back up that directory along with account data.
Two windows editing the same project cannot silently overwrite each other:
a revision conflict pauses saving. Export the draft before reloading and reconcile
its text manually. Simultaneous collaborative editing is not implemented.

## Project libraries

In **Library**, create a collection and add papers using **Add to collection**.
The same paper may belong to several collections without duplicating its PDF.
Removing a paper from a collection does not remove it from the corpus or shelf.

Choose that collection in an OriginWrite project and click **Update references.bib**.
This explicitly replaces that file, with confirmation if it is nonempty. Metadata
changes are not silently applied to manuscripts. Citation keys are stable hashes
of corpus paper IDs. Check imported author names, publication types and other
bibliographic fields before submission; catalogue metadata may be incomplete.

## Journal templates

The template gallery links to the official author resources for
[G3](https://academic.oup.com/G3JOURNAL/pages/author-guidelines),
[Bioinformatics](https://academic.oup.com/bioinformatics/pages/author-guidelines),
[Bioinformatics Advances](https://academic.oup.com/bioinformaticsadvances/pages/author-guidelines),
[Briefings in Bioinformatics](https://academic.oup.com/bib/pages/msprep_submission), and
[Springer Nature](https://www.springernature.com/gp/authors/campaigns/latex-author-support).
These links were checked on 16 September 2026. Download the publisher's ZIP and
import the source files. No publisher template is redistributed or presented as
an OriginWrite-authored template. Consult the original licence and journal's
instructions. The bundled generic article starter is not a journal template.

The prototype accepts UTF-8 `.tex`, `.bib`, `.sty`, `.cls`, `.bst`, `.bbl`, `.txt`,
`.md` and `.csv` files, plus `.pdf`, `.png`, `.jpg` and `.jpeg` assets. ZIPs with
other files, symlinks, unsafe paths or duplicate paths are rejected, not partially
imported. Remove unsupported packaging files from a copy if needed. Limits:
12 MB and 200 files per project, 100 MB of serialised source data per account,
150 projects/collections per account, 500 papers per collection. The browser
preview renders the first 40 PDF pages; the PDF download contains all pages.

## Configure PDF compilation

Editing and ZIP export work without TeX. Compilation requires isolation and
never falls back to unrestricted execution on the host. Compiler status describes
configuration, not a guarantee that the engine and all required packages are ready.

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
This is a private-server option, not a hardened public compilation service.

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
account and private deployment; this prototype is not a public multi-tenant
compilation service.

One compilation runs at a time per web-server process. Additional requests get a
visible busy response, not a hidden queue. Each build is limited to 45 seconds,
768 MiB memory (container quota, per-process address-space limit with Bubblewrap,
watchdog on macOS), and bounded
output size. Temporary files are discarded after the response. Build logs are
returned only to the requesting account. LaTeX engine/package differences mean
that not every existing Overleaf project will compile unchanged.

## Local writing and remote retrieval

You can run an installation locally and keep a separate RAG installation on your
server. This prototype does **not** synchronise accounts, collections or manuscript
edits between them. Transfer projects explicitly with ZIP export/import. No
background synchronisation or access to arbitrary files on your computer is enabled.
Local file access happens only through the browser's explicit file picker.
