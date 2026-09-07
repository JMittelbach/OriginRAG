# Data Policy

OriginRAG separates reproducible project files from local research data.

## Committed

- Source code
- Tests
- Configuration files
- Bibliographic metadata without publisher full text
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

## Separate paper archive

Selected paper-reproduction materials are prepared as a separate versioned
archive, with an explicit file allowlist and SHA-256 inventory. It contains
synthetic inputs, derived results, author-curated mappings and figure/build
instructions. Corpus-dependent experiments retain input identities and access
instructions; publisher passages, PDFs, account data and operational sessions
are not included. See [Paper reproduction](paper_reproduction.md).

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
