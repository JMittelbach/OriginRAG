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

Enable the local-data commit and push checks (automatic with `./setup.sh`):

```bash
python3 scripts/install_git_hooks.py
python3 scripts/check_private_files.py
```

Existing custom hooks are preserved. If you use your own hooks, call
`python3 scripts/check_private_files.py` from `pre-commit` and
`python3 scripts/check_private_files.py --push "$1"` from `pre-push`, forwarding
Git's original standard input to the latter. The checks must succeed before
continuing. Do not use `--no-verify` to bypass a local-data warning.

Personal PDFs belong in `bib/`, local metadata and indexes in `data/`, and chats
or exports in `outputs/`. These directories are ignored. The files under
`assets/catalogue/` are shipped examples, not your working bibliography.
See the [data policy](docs/data_policy.md) for scope and limitations.

## Tests and style

Before proposing a change, run:

```bash
python -m ruff check .
python -m pytest -q
git diff --check
```

Add a regression test for a corrected failure. Keep Python compatible with
3.11, repository text in English, and Markdown prose concise.

Browser tests use Playwright and a local test server with temporary data:

```bash
python -m pip install playwright
python -m playwright install chromium
python -m pytest -q tests/test_workspace_recovery_browser.py tests/test_workspace_profiles_browser.py
```

CI runs browser checks separately, including failed imports, interrupted
connections, manuscript saving and collaboration. These checks need no model
or personal corpus.

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
