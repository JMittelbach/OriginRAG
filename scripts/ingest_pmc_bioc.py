from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BIOC_URL = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"
SECTION_LABELS = {
    "ABSTRACT": "Abstract",
    "INTRO": "Introduction",
    "METHODS": "Methods",
    "RESULTS": "Results",
    "DISCUSS": "Discussion",
    "CONCL": "Conclusion",
    "SUPPL": "Supplementary information",
    "REF": "References",
}


def main() -> int:
    args = parse_args()
    payload = fetch_bioc(args.pmcid, timeout=args.timeout)
    pages = passages_to_pages(payload, max_chars=args.max_chars_per_page)
    if not pages:
        raise RuntimeError(f"NCBI BioC returned no article text for {args.pmcid}")
    output = resolve(args.out or f"data/extracted_text/{args.paper_id}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "paper_id": args.paper_id,
                "source_pmcid": args.pmcid.upper(),
                "source_url": BIOC_URL.format(pmcid=args.pmcid.upper()),
                "source_format": "NCBI BioC full text",
                "pages": pages,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"ok: {args.paper_id}: {len(pages)} text segments -> {display(output)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest an open PMC article through the official NCBI BioC API.")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--pmcid", required=True)
    parser.add_argument("--out")
    parser.add_argument("--max-chars-per-page", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def fetch_bioc(pmcid: str, *, timeout: int) -> object:
    url = BIOC_URL.format(pmcid=pmcid.upper())
    request = urllib.request.Request(url, headers={"User-Agent": "OriginRAG/0.1 local full-text ingestion"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def passages_to_pages(payload: object, *, max_chars: int) -> list[dict[str, object]]:
    passages = find_passages(payload)
    blocks: list[str] = []
    for passage in passages:
        text = " ".join(str(passage.get("text") or "").split())
        if not text:
            continue
        section = str((passage.get("infons") or {}).get("section_type") or "").strip().upper()
        label = SECTION_LABELS.get(section, section.replace("_", " ").title())
        block = f"{label}\n{text}" if label and section != "TITLE" else text
        blocks.append(block)

    pages: list[dict[str, object]] = []
    current: list[str] = []
    current_length = 0
    for block in blocks:
        if current and current_length + len(block) + 2 > max_chars:
            pages.append({"page": len(pages) + 1, "text": "\n\n".join(current)})
            current = []
            current_length = 0
        current.append(block)
        current_length += len(block) + 2
    if current:
        pages.append({"page": len(pages) + 1, "text": "\n\n".join(current)})
    return pages


def find_passages(payload: object) -> list[dict]:
    collections = payload if isinstance(payload, list) else [payload]
    passages: list[dict] = []
    for collection in collections:
        if not isinstance(collection, dict):
            continue
        for document in collection.get("documents", []):
            if isinstance(document, dict):
                passages.extend(item for item in document.get("passages", []) if isinstance(item, dict))
    return passages


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else str(path)


if __name__ == "__main__":
    raise SystemExit(main())
