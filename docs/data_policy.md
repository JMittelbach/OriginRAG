# Data Policy

OriginRAG separates reproducible project files from local research data.

## Committed

- Source code
- Tests
- Configuration files
- The bundled example catalogue in `assets/catalogue/` (metadata only)
- User installation, web, and data-policy documentation

## Not Committed

- PDFs
- EPUBs
- Extracted full text
- Chunk files
- Embeddings
- Vector indices
- SQLite or local database files
- API keys
- Real `.env` files
- Generated answers and model prompts
- Account databases, session files, and runtime logs
- Internal experiment, review, and manuscript working files
- Personal bibliographic metadata and import/discovery reports

## Personal use and Git

The complete `data/`, `bib/` and `outputs/` working directories are ignored,
apart from shipped placeholder files and the bibliography README. This includes
personal paper lists, downloaded PDFs, extracted text, indexes, chats, uploads,
answers and account data. Local `.env` files and `config/paths.yaml` are ignored.
Setup or the first application launch copies the bundled example catalogue into
`data/metadata/` only if no local catalogue exists. Existing files are not replaced.
Importers update this ignored copy, never the bundled catalogue.

`./setup.sh` also enables commit and push checks unless existing Git hooks
would be replaced. Existing installations can enable them with
`python3 scripts/install_git_hooks.py`. These checks reject recognised local-data
paths even if someone used `git add -f`. The push check also examines outgoing
commits, including files added and removed before the latest commit. CI provides
an additional check, but CI runs after upload and is not a privacy boundary.

Keep custom exports inside `outputs/` and custom corpora inside `bib/` or outside
the checkout. Arbitrarily renamed data in source directories cannot reliably be
recognised. Git ignores and hooks are accidental-disclosure safeguards, not
access controls: they can be bypassed, and do not remove data already in Git
history. Review staged changes before committing. Personal content must not be
copied into the bundled example catalogue, tests or public documentation.

## Local Bibliography

The local PDF library can live inside the repo tree for convenience, but its contents are ignored by Git:

```text
bib/
```

One possible source-checkout layout is:

```text
~/projects/OriginRAG
~/projects/OriginRAG/bib
```

Metadata stores only relative PDF paths from the bibliography root. For example:

```text
var_pfemp1/example-paper.pdf
```

The absolute bibliography root is configured with `ORIGINRAG_BIB_DIR`.

## Cloning On Another Computer

A fresh Git clone is enough for code, configuration, and bibliographic
metadata, but not enough to run full local retrieval over the papers. The clone
will not contain:

- the PDF files
- extracted page text
- chunk JSONL files
- future embeddings or vector indexes

To use the same corpus on another machine, copy or sync the ignored `bib/`
directory separately and set `ORIGINRAG_BIB_DIR` to that machine's local path.
If extracted text, chunks, and the SQLite retrieval index are not restored as
well, rebuild them with:

```bash
./originrag bib
./originrag prepare-corpus
```

Use `./originrag doctor` to distinguish a metadata-only clone from a complete
local retrieval installation.
