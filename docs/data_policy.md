# Data Policy

OriginRAG separates reproducible project files from local research data.

## Committed

- Source code
- Tests
- Configuration files
- User installation, web, and data-policy documentation

A fresh checkout contains no user library or starter bibliography.

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
Setup or the first RAG launch creates header-only tables in `data/metadata/`
if no local catalogue exists. Importing papers updates only the local catalogue.
An update does not empty an existing library.

Older releases copied a starter catalogue into `data/metadata/`. A pull removes
the tracked template, not that local copy. To distinguish the original starter
from a changed table, run `python3 scripts/init_local_data.py --check`.
It compares file fingerprints without printing titles or changing files. If
you want a separate empty workspace, set up a fresh clone in a new directory
and keep your original checkout for its papers and manuscripts. The old
catalogue templates are still present in historical commits.

When updating an older checkout that still tracks `data/metadata/*.tsv`, back
up any personal changes to those tables before pulling. Do not resolve a Git
rename or merge conflict by moving personal entries into a tracked directory.

`./setup.sh` also enables commit and push checks unless existing Git hooks
would be replaced. Existing installations can enable them with
`python3 scripts/install_git_hooks.py`. These checks reject recognised local-data
paths even if someone used `git add -f`. The push check also examines outgoing
commits, including files added and removed before the latest commit. CI provides
an additional check, but CI runs after upload and is not a privacy boundary.
Local data directories are also protected inside nested copies of a checkout.

Keep custom exports inside `outputs/` and custom corpora inside `bib/` or outside
the checkout. Arbitrarily renamed data in source directories cannot reliably be
recognised. Git ignores and hooks are accidental-disclosure safeguards, not
access controls: they can be bypassed, and do not remove data already in Git
history. Review staged changes before committing. Personal content must not be
copied into tests or public documentation.

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

A fresh Git clone includes the code and example configuration. The clone
will not contain:

- your catalogue or paper registry
- the PDF files
- extracted page text
- chunk JSONL files
- future embeddings or vector indexes

To use the same corpus on another machine, copy the ignored `bib/` and
`data/metadata/` directories separately and set `ORIGINRAG_BIB_DIR` to that
machine's local PDF path.
If extracted text, chunks, and the SQLite retrieval index are not restored as
well, rebuild them with:

```bash
./originrag bib
./originrag prepare-corpus
```

Use `./originrag doctor` to check whether local retrieval is ready.
