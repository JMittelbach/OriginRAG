# Contributing to OriginRAG

OriginRAG welcomes focused bug reports, reproducibility checks, documentation
improvements, and scientific review. The project is research software, so a
change must preserve both software correctness and the stated evidence limits.

## Before opening a change

1. Search existing issues for related work.
2. Keep the change focused on one measured problem or one documented research
   question.
3. Do not commit PDFs, extracted publisher text, model outputs containing
   licensed excerpts, secrets, account databases, or local indexes.
4. Do not turn model-assisted or fuzzy matches into verified paper identities
   or scientific-origin relations.

For security-sensitive findings, follow [`SECURITY.md`](SECURITY.md) rather
than publishing credentials, private data, or a working exploit in an issue.

## Development setup

```bash
git clone https://github.com/JMittelbach/OriginRAG.git
cd OriginRAG
./setup.sh
conda activate originrag
make check
```

A Python 3.11 virtual environment with `pip install -e '.[dev]'` is also
supported. See the [Getting started guide](docs/getting_started.md).

## Tests and style

Before proposing a change, run:

```bash
python -m ruff check .
python -m pytest -q
git diff --check
```

Add a regression test for a corrected failure. Keep Python compatible with
3.11, repository text in English, and Markdown prose concise.

## Scientific and experiment changes

Claims must not exceed the evidence. In particular, retrieval quality,
scientific-origin accuracy, biological validity, end-to-end utility, and
superiority over another system are different endpoints and require different
validation.

Literature additions need an exact DOI, PMID, PMCID, or stable official URL and
must be obtained lawfully. Full text belongs only in the ignored local
bibliography.

## Pull requests

Describe:

- the problem and why it matters;
- the smallest implemented solution;
- commands and results used for validation;
- affected scientific claims or explicit non-claims;
- any local-only data needed to reproduce the result.

By submitting a contribution, contributors agree that it may be distributed
under the repository's [MIT License](LICENSE).
