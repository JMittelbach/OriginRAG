# Live paper-import controls

The normal test suite does not download journal papers. To check the complete
web import against real services, install Playwright in the development
environment and run the opt-in controls:

```bash
python -m pip install -e '.[dev]' playwright
python -m playwright install chromium
ORIGINRAG_LIVE_IMPORT_TESTS=1 python -m pytest -q -s tests/test_live_paper_import.py
```

The four controls use fastp and MultiQC from *Bioinformatics*, and AlphaFold 2
and AlphaFold 3 from *Nature*. Each opens the real web application, selects the
journal, searches Crossref, and clicks **Add to library**. Search responses,
PDF downloads, DOI checks, import processing and indexing are not mocked.

A control passes only when the UI shows **In library**, a PDF is stored under
its organised filename, its metadata and text chunks exist, and a full-text
index query finds the imported paper. No upload action or language-model call
is used. Each case starts with an empty temporary library, not your corpus.
Paths and library-cache refresh callbacks are redirected to that test library.

Each test saves `control-result.json` and `import-result.png` under pytest's
temporary directory. The JSON records the outcome, import time, PDF size,
chunk count and search-hit count. PDFs remain test data, not Git assets.

These are positive integration controls, not a guarantee that every paper
from either publisher can be downloaded. Publisher restrictions, metadata
changes and service outages can make a live control fail. A failed download
must never be counted as a successful import.
