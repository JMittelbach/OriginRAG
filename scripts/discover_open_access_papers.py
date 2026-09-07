from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from rapidfuzz import fuzz


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pfempire.curation.relevance_policy import relevance_decision  # noqa: E402
from pfempire.discovery.europepmc import EuropePMCHit, best_pdf_url, download_pdf, search  # noqa: E402
from pfempire.discovery.openalex import search_openalex  # noqa: E402
from pfempire.discovery.preprints import search_preprint_server  # noqa: E402
from pfempire.discovery.types import DiscoveryHit  # noqa: E402
from pfempire.paths import OriginRAGConfigError, get_bib_dir, load_project_env  # noqa: E402


DEFAULT_SOURCES = ["europepmc"]
ALL_SOURCES = ["europepmc", "openalex", "biorxiv", "medrxiv"]
SOURCE_ORDER = {source: index for index, source in enumerate(ALL_SOURCES)}
QUALITY_TIER_ORDER = {"low": 0, "review": 1, "candidate": 2, "high": 3}
CATEGORY_DOWNLOAD_PRIORITY = [
    "var_pfemp1",
    "malaria_host_genetics",
    "malaria_host_omics",
    "sex_diff",
    "host_immunity",
    "malaria_omics",
    "parasite_biology",
    "resistance_dx",
    "malaria_tools",
    "scrna_annotation",
    "sc_tools",
    "host_tissue_atlas",
    "covid_host_response",
    "infectious_disease_host_response",
    "noninfectious_disease_biology",
    "general_methods",
    "general_malaria",
]

COLLECTION_BY_CATEGORY = {
    "var_pfemp1": "malaria",
    "malaria_host_genetics": "malaria",
    "malaria_host_omics": "malaria",
    "host_immunity": "malaria",
    "sex_diff": "malaria",
    "resistance_dx": "malaria",
    "parasite_biology": "malaria",
    "general_malaria": "malaria",
    "malaria_omics": "malaria",
    "malaria_tools": "malaria",
    "tools_ml": "bioinformatics_tools",
    "sc_tools": "bioinformatics_tools",
    "sc_harmonization": "bioinformatics_tools",
    "scrna_annotation": "scrna_annotation",
    "host_tissue_atlas": "host_biology",
    "covid_host_response": "host_biology",
    "infectious_disease_host_response": "host_biology",
    "noninfectious_disease_biology": "host_biology",
    "host_immunity_general": "host_biology",
    "general_methods": "methods",
    "foundation_models": "bioinformatics_tools",
    "unknown": "unknown",
}

NON_MALARIA_DISCOVERY_CATEGORIES = {
    "covid_host_response",
    "foundation_models",
    "general_methods",
    "host_immunity_general",
    "host_tissue_atlas",
    "infectious_disease_host_response",
    "noninfectious_disease_biology",
    "sc_harmonization",
    "sc_tools",
    "scrna_annotation",
    "tools_ml",
}

HIGH_SIGNAL_JOURNAL_TERMS = {
    "american journal of human genetics",
    "cell",
    "clinical infectious diseases",
    "elife",
    "genome biology",
    "genome medicine",
    "jci insight",
    "lancet",
    "malaria journal",
    "nature",
    "nature communications",
    "nature medicine",
    "nature methods",
    "plos biology",
    "plos computational biology",
    "plos genetics",
    "plos medicine",
    "plos neglected tropical diseases",
    "plos pathogens",
    "pnas",
    "science",
    "science translational medicine",
    "the journal of infectious diseases",
}

MALARIA_SIGNAL_TERMS = {
    "malaria",
    "malarial",
    "antimalarial",
    "plasmodium",
    "falciparum",
    "vivax",
    "ovale",
    "knowlesi",
    "pfemp1",
    "pfemp",
    "var gene",
    "var genes",
    "var2csa",
    "dblalpha",
    "dbl alpha",
    "cidralpha",
    "cidr alpha",
    "rosetting",
    "cytoadherence",
    "sequestration",
    "merozoite",
    "sporozoite",
    "gametocyte",
    "pfhrp2",
    "pfhrp3",
    "hrp2",
    "hrp3",
    "kelch13",
    "kelch 13",
    "k13",
    "pfmdr1",
    "pfcrt",
    "pfdhfr",
    "pfdhps",
    "anopheles",
    "mosquito",
    "rdt",
}

MALARIA_CONTEXT_REQUIRED_CATEGORIES = {
    "general_malaria",
    "host_immunity",
    "malaria_host_genetics",
    "malaria_host_omics",
    "malaria_omics",
    "malaria_tools",
    "parasite_biology",
    "resistance_dx",
    "sex_diff",
    "var_pfemp1",
}

OFF_TOPIC_TITLE_TERMS = {
    "covid-19",
    "sars-cov-2",
    "rheumatoid arthritis",
    "cancer",
    "tumor",
    "tumour",
    "rothko",
}

SCRNA_SIGNAL_TERMS = {
    "single-cell",
    "single cell",
    "scrna",
    "scrna-seq",
    "cell type annotation",
    "cell-type annotation",
    "reference mapping",
    "cell atlas",
    "celltypist",
    "singler",
    "azimuth",
    "scanvi",
    "scvi",
    "scarches",
    "geneformer",
    "scgpt",
}

HOST_BIOLOGY_SIGNAL_TERMS = {
    "cell atlas",
    "gene expression",
    "gene signature",
    "immune atlas",
    "immune response",
    "multi-omic",
    "rna-seq",
    "single cell",
    "single-cell",
    "spatial transcript",
    "transcriptome",
    "transcriptomic",
}

PERTURBATION_SIGNAL_TERMS = {
    "causal",
    "chemical perturb",
    "crispr screen",
    "crop-seq",
    "drug perturb",
    "gene perturb",
    "perturb-seq",
    "perturbation",
    "response prediction",
    "single-cell crispr",
}

BIOLOGICAL_METHOD_CONTEXT_TERMS = {
    "biological",
    "cell",
    "chemical",
    "crispr",
    "drug",
    "gene",
    "genetic",
    "genome",
    "genomic",
    "molecular",
    "multiomic",
    "perturb",
    "protein",
    "rna",
    "single cell",
    "single-cell",
    "transcript",
}

LOW_SIGNAL_TITLE_TERMS = {
    "case report": -18,
    "commentary": -18,
    "editorial": -22,
    "knowledge attitudes and practices": -12,
    "letter to the editor": -22,
    "protocol": -12,
    "traditional treatment": -15,
    "trial protocol": -12,
}

FIELDS = [
    "discovered_at",
    "sources",
    "query_id",
    "query",
    "collection",
    "category",
    "matched_categories",
    "author",
    "year",
    "title",
    "journal",
    "doi",
    "pmid",
    "pmcid",
    "first_publication_date",
    "is_open_access",
    "landing_url",
    "pdf_url",
    "pdf_status",
    "quality_score",
    "quality_tier",
    "quality_flags",
    "local_pdf_relpath",
    "dedupe_key",
    "message",
]


def main() -> int:
    args = parse_args()
    load_project_env(REPO_ROOT / ".env")
    config = load_config(resolve_repo_path(args.config))
    queries = config["queries"]
    sources = selected_sources(args.sources, config["sources"])
    out_path = resolve_repo_path(args.out)
    existing_titles = load_existing_titles(resolve_repo_path(args.inventory))
    existing_identifiers = load_existing_identifiers(resolve_repo_path(args.registry))
    bib_dir = get_configured_bib_dir(required=args.download)

    discovered_rows: list[dict[str, str]] = []
    from_date, to_date = date_range(args.since_days)
    for query_def in queries:
        query_id = str(query_def.get("query_id") or slugify(str(query_def.get("query") or "query")))
        category = str(query_def.get("category") or args.category or "general_malaria")
        collection = str(query_def.get("collection") or collection_for_category(category))
        base_query = str(query_def.get("query") or "").strip()
        if not base_query:
            continue
        query_sources = selected_sources("", query_def.get("sources") or sources)
        for source_name in query_sources:
            print(f"Searching {query_id}/{source_name}: {base_query}")
            try:
                hits = search_source(
                    source_name,
                    base_query,
                    from_date=from_date,
                    to_date=to_date,
                    max_per_query=args.max_per_query,
                    preprint_max_records=args.preprint_max_records,
                )
            except Exception as exc:
                discovered_rows.append(
                    error_row(
                        query_id,
                        base_query,
                        collection,
                        category,
                        source_name,
                        f"search_failed: {exc}",
                    )
                )
                continue
            for hit in hits:
                if not is_relevant_hit(hit, base_query, category=category, collection=collection):
                    continue
                discovered_rows.append(
                    hit_to_row(hit, query_id=query_id, collection=collection, category=category)
                )

    rows = dedupe_rows(discovered_rows)
    for row in rows:
        apply_status(
            row,
            existing_titles=existing_titles,
            existing_identifiers=existing_identifiers,
            download=False,
        )

    if args.download:
        download_ranked_rows(
            rows,
            bib_dir=bib_dir,
            dry_run=args.dry_run,
            download_min_tier=args.download_min_tier,
            download_limit=args.download_limit,
            require_doi=not args.allow_missing_doi,
            download_strategy=args.download_strategy,
        )

    write_tsv(out_path, rows)
    print(f"Wrote {len(rows)} deduplicated discovery rows to {display_path(out_path)}")
    print(f"Raw source hits before dedupe: {len(discovered_rows)}")
    if args.download:
        print("Refresh inventory after downloads:")
        print("  python scripts/inventory_bib_pdfs.py --out data/metadata/local_pdf_inventory.tsv")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover recent open-access malaria papers across multiple sources."
    )
    parser.add_argument("--config", default="config/literature_watch.yaml")
    parser.add_argument("--out", default="data/metadata/open_access_discovery.tsv")
    parser.add_argument("--inventory", default="data/metadata/local_pdf_inventory.tsv")
    parser.add_argument("--registry", default="data/metadata/paper_registry.tsv")
    parser.add_argument("--max-per-query", type=int, default=25)
    parser.add_argument("--since-days", type=int, default=60)
    parser.add_argument("--category", default="")
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated sources: europepmc,openalex,biorxiv,medrxiv,all. Defaults to config sources.",
    )
    parser.add_argument(
        "--preprint-max-records",
        type=int,
        default=500,
        help="Maximum recent bioRxiv/medRxiv records to scan per query when using preprint sources.",
    )
    parser.add_argument(
        "--download", action="store_true", help="Download open-access PDFs into PFEMPIRE_BIB_DIR."
    )
    parser.add_argument(
        "--download-min-tier",
        choices=["low", "review", "candidate", "high"],
        default="candidate",
        help="Minimum quality tier for automatic downloads. Lower-tier hits stay in the TSV for review.",
    )
    parser.add_argument(
        "--download-limit",
        type=int,
        default=0,
        help="Maximum number of ranked eligible PDFs to download. 0 means no limit.",
    )
    parser.add_argument(
        "--download-strategy",
        choices=["ranked", "diverse"],
        default="ranked",
        help="ranked uses global score order; diverse round-robins categories before applying the limit.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show target paths without downloading PDFs."
    )
    parser.add_argument(
        "--allow-missing-doi",
        action="store_true",
        help="Allow automatic PDF downloads without a DOI. By default, DOI is required for new downloads.",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {
            "sources": DEFAULT_SOURCES,
            "queries": [
                {"query_id": "malaria_recent", "category": "general_malaria", "query": "malaria"},
                {
                    "query_id": "var_pfemp1_recent",
                    "category": "var_pfemp1",
                    "query": "PfEMP1 OR var gene",
                },
            ],
        }
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    queries = payload.get("queries") or []
    sources = payload.get("sources") or DEFAULT_SOURCES
    return {
        "sources": sources,
        "queries": [item for item in queries if isinstance(item, dict)],
    }


def selected_sources(cli_sources: str, configured_sources: object) -> list[str]:
    raw_sources: list[str]
    if cli_sources:
        raw_sources = [item.strip() for item in cli_sources.split(",")]
    elif isinstance(configured_sources, str):
        raw_sources = [item.strip() for item in configured_sources.split(",")]
    elif isinstance(configured_sources, list):
        raw_sources = [str(item).strip() for item in configured_sources]
    else:
        raw_sources = DEFAULT_SOURCES

    normalized: list[str] = []
    for source in raw_sources:
        value = source.lower()
        if not value:
            continue
        if value == "all":
            normalized.extend(ALL_SOURCES)
            continue
        if value not in ALL_SOURCES:
            raise ValueError(f"Unsupported discovery source: {source}")
        normalized.append(value)
    return sort_sources(dedupe_tokens(normalized)) or DEFAULT_SOURCES


def date_range(since_days: int) -> tuple[str, str]:
    start_date = (date.today() - timedelta(days=max(0, since_days))).isoformat()
    return start_date, date.today().isoformat()


def search_source(
    source_name: str,
    base_query: str,
    *,
    from_date: str,
    to_date: str,
    max_per_query: int,
    preprint_max_records: int,
) -> list[DiscoveryHit]:
    if source_name == "europepmc":
        europepmc_query = build_europepmc_query(base_query, from_date=from_date, to_date=to_date)
        return [
            europepmc_to_hit(hit, query=europepmc_query)
            for hit in search(europepmc_query, page_size=max_per_query)
        ]
    if source_name == "openalex":
        return search_openalex(
            base_query, from_date=from_date, to_date=to_date, page_size=max_per_query
        )
    if source_name in {"biorxiv", "medrxiv"}:
        return search_preprint_server(
            source_name,
            base_query,
            from_date=from_date,
            to_date=to_date,
            page_size=max_per_query,
            max_records=preprint_max_records,
        )
    raise ValueError(f"Unsupported discovery source: {source_name}")


def is_relevant_hit(hit: DiscoveryHit, base_query: str, *, category: str, collection: str) -> bool:
    clauses = query_clauses(base_query)
    searchable_text = " ".join(
        [
            hit.title,
            hit.journal,
            hit.author,
            hit.doi,
        ]
    )
    normalized = normalize_search_text(searchable_text)
    broad_scope = category in NON_MALARIA_DISCOVERY_CATEGORIES
    if not broad_scope:
        decision = relevance_decision(
            title=hit.title,
            journal=hit.journal,
            category=category,
            query=base_query,
        )
        if not decision.keep:
            return False
    if requires_malaria_context(category, collection) and is_off_topic_hit(normalized):
        return False
    if requires_malaria_context(category, collection) and not has_malaria_context(normalized):
        return False
    if category in {"general_methods", "sc_tools"} and not any(
        term in normalized for term in BIOLOGICAL_METHOD_CONTEXT_TERMS
    ):
        return False
    if collection_contains(collection, "scrna_annotation") and not any(
        term in normalized for term in SCRNA_SIGNAL_TERMS
    ):
        return False
    if not clauses:
        return True
    return any(all(term in normalized for term in clause) for clause in clauses)


def requires_malaria_context(category: str, collection: str) -> bool:
    category_value = (category or "").strip()
    if category_value in MALARIA_CONTEXT_REQUIRED_CATEGORIES:
        return True
    return collection_contains(collection, "malaria")


def has_malaria_context(normalized_text: str) -> bool:
    return any(term in normalized_text for term in MALARIA_SIGNAL_TERMS)


def is_off_topic_hit(normalized_text: str) -> bool:
    return any(
        term in normalized_text for term in OFF_TOPIC_TITLE_TERMS
    ) and not has_malaria_context(normalized_text)


def query_clauses(query: str) -> list[list[str]]:
    clauses: list[list[str]] = []
    for raw_clause in re.split(r"\bOR\b", query, flags=re.IGNORECASE):
        quoted = re.findall(r'"([^"]+)"', raw_clause)
        without_quotes = re.sub(r'"[^"]+"', " ", raw_clause)
        words = re.split(r"\bAND\b|[()]", without_quotes, flags=re.IGNORECASE)
        terms = [normalize_search_text(term) for term in quoted if meaningful_query_term(term)]
        for word_group in words:
            terms.extend(unquoted_query_terms(word_group))
        if terms:
            clauses.append(dedupe_tokens(terms))
    return clauses


def unquoted_query_terms(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    if not meaningful_query_term(normalized):
        return []
    return [
        term
        for term in re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)?", normalized)
        if meaningful_query_term(term)
    ]


def meaningful_query_term(term: str) -> bool:
    value = normalize_search_text(term)
    return bool(value and value not in {"or", "and", "the", "a", "an"})


def normalize_search_text(value: str) -> str:
    return " ".join(value.lower().split())


def build_europepmc_query(base_query: str, *, from_date: str, to_date: str) -> str:
    return f"({base_query}) AND OPEN_ACCESS:y AND FIRST_PDATE:[{from_date} TO {to_date}]"


def europepmc_to_hit(hit: EuropePMCHit, *, query: str) -> DiscoveryHit:
    return DiscoveryHit(
        source="europepmc",
        query=query,
        author=hit.author,
        year=hit.year,
        title=hit.title,
        journal=hit.journal,
        doi=normalize_doi(hit.doi),
        pmid=hit.pmid,
        pmcid=normalize_pmcid(hit.pmcid),
        first_publication_date=hit.first_publication_date,
        is_open_access=hit.is_open_access,
        pdf_url=best_pdf_url(hit),
        landing_url=landing_url_for_europepmc_hit(hit),
    )


def landing_url_for_europepmc_hit(hit: EuropePMCHit) -> str:
    if hit.pmcid:
        return f"https://europepmc.org/articles/{hit.pmcid}"
    if hit.pmid:
        return f"https://europepmc.org/article/MED/{hit.pmid}"
    if hit.doi:
        return f"https://doi.org/{hit.doi}"
    return ""


def hit_to_row(
    hit: DiscoveryHit, *, query_id: str, collection: str, category: str
) -> dict[str, str]:
    pdf_url = hit.pdf_url
    row = {
        "discovered_at": datetime.now().isoformat(timespec="seconds"),
        "sources": hit.source,
        "query_id": query_id,
        "query": hit.query,
        "collection": collection,
        "category": category,
        "matched_categories": category,
        "author": hit.author,
        "year": hit.year,
        "title": hit.title,
        "journal": hit.journal,
        "doi": normalize_doi(hit.doi),
        "pmid": hit.pmid,
        "pmcid": normalize_pmcid(hit.pmcid),
        "first_publication_date": hit.first_publication_date,
        "is_open_access": "yes" if hit.is_open_access else "no",
        "landing_url": hit.landing_url,
        "pdf_url": pdf_url,
        "pdf_status": "pdf_available" if pdf_url else "no_pdf_url",
        "quality_score": "",
        "quality_tier": "",
        "quality_flags": "",
        "local_pdf_relpath": "",
        "dedupe_key": "",
        "message": hit.message,
    }
    set_quality(row)
    return row


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    by_identifier: dict[str, dict[str, str]] = {}

    for row in rows:
        key = stable_identifier(row)
        existing = by_identifier.get(key) if key else find_title_duplicate(row, deduped)
        if existing is None:
            row["dedupe_key"] = key or title_year_key(row)
            deduped.append(row)
            for identifier in all_identifiers(row):
                by_identifier[identifier] = row
            continue
        merge_row(existing, row)
        for identifier in all_identifiers(existing):
            by_identifier[identifier] = existing
    return deduped


def stable_identifier(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    pmcid = normalize_pmcid(row.get("pmcid", ""))
    if pmcid:
        return f"pmcid:{pmcid}"
    pmid = normalize_numeric_id(row.get("pmid", ""))
    if pmid:
        return f"pmid:{pmid}"
    return ""


def all_identifiers(row: dict[str, str]) -> list[str]:
    identifiers = [stable_identifier(row)]
    title_key = title_year_key(row)
    if title_key:
        identifiers.append(title_key)
    return [identifier for identifier in identifiers if identifier]


def title_year_key(row: dict[str, str]) -> str:
    title = normalize_title(row.get("title", ""))
    year = row.get("year", "").strip()
    if not title:
        return ""
    return f"title_year:{year}:{title}"


def find_title_duplicate(
    row: dict[str, str], existing_rows: list[dict[str, str]]
) -> dict[str, str] | None:
    title = row.get("title", "")
    year = row.get("year", "").strip()
    if not title:
        return None
    for existing in existing_rows:
        if year and existing.get("year", "").strip() != year:
            continue
        score = fuzz.token_set_ratio(title, existing.get("title", ""))
        if score >= 96:
            return existing
    return None


def merge_row(existing: dict[str, str], incoming: dict[str, str]) -> None:
    for field in [
        "doi",
        "pmid",
        "pmcid",
        "author",
        "year",
        "title",
        "journal",
        "first_publication_date",
        "landing_url",
    ]:
        if not existing.get(field) and incoming.get(field):
            existing[field] = incoming[field]
    if better_pdf_url(incoming.get("pdf_url", ""), existing.get("pdf_url", "")):
        existing["pdf_url"] = incoming["pdf_url"]
        existing["pdf_status"] = "pdf_available"
    existing["sources"] = merge_token_field(
        existing.get("sources", ""), incoming.get("sources", ""), sort_by_source=True
    )
    existing["query_id"] = merge_token_field(
        existing.get("query_id", ""), incoming.get("query_id", "")
    )
    existing["collection"] = merge_token_field(
        existing.get("collection", ""), incoming.get("collection", "")
    )
    existing["matched_categories"] = merge_token_field(
        existing.get("matched_categories", ""), incoming.get("matched_categories", "")
    )
    existing["is_open_access"] = (
        "yes" if "yes" in {existing.get("is_open_access"), incoming.get("is_open_access")} else "no"
    )
    existing["message"] = merge_messages(
        existing.get("message", ""), incoming.get("message", ""), "merged_duplicate_source"
    )
    set_quality(existing)


def better_pdf_url(candidate: str, current: str) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    candidate_lower = candidate.lower().split("?", 1)[0]
    current_lower = current.lower().split("?", 1)[0]
    if candidate_lower.endswith(".pdf") and not current_lower.endswith(".pdf"):
        return True
    if "europepmc.org" in candidate_lower and "europepmc.org" not in current_lower:
        return True
    return False


def apply_status(
    row: dict[str, str],
    *,
    existing_titles: list[dict[str, str]],
    existing_identifiers: dict[str, str],
    download: bool,
) -> None:
    set_quality(row)
    if is_excluded_publication_title(row.get("title", "")):
        row["pdf_status"] = "excluded"
        row["quality_score"] = "0"
        row["quality_tier"] = "low"
        row["quality_flags"] = merge_token_field(
            row.get("quality_flags", ""), "excluded_publication_type"
        )
        row["message"] = merge_messages(
            row.get("message", ""), "", "excluded publication type by title"
        )
        return

    identifier = stable_identifier(row)
    if identifier and identifier in existing_identifiers:
        row["pdf_status"] = "already_present"
        row["local_pdf_relpath"] = existing_identifiers[identifier]
        row["message"] = merge_messages(
            row.get("message", ""), "", f"same identifier already in local bib: {identifier}"
        )
        return

    duplicate = best_existing_match(row, existing_titles)
    if duplicate:
        row["pdf_status"] = "already_present"
        row["local_pdf_relpath"] = duplicate["relpath"]
        row["message"] = merge_messages(
            row.get("message", ""), "", f"similar local title score={duplicate['score']}"
        )
        return

    if download:
        row["message"] = merge_messages(
            row.get("message", ""), "", "download requested but handled by ranked download stage"
        )


def is_excluded_publication_title(title: str) -> bool:
    normalized = " ".join((title or "").lower().split())
    return normalized.startswith(
        (
            "author correction:",
            "correction:",
            "correction for",
            "correction to:",
            "corrigendum:",
            "erratum:",
            "expression of concern for",
            "expression of concern:",
            "publisher correction:",
            "retraction:",
            "review 1:",
            "review 2:",
        )
    )


def set_quality(row: dict[str, str]) -> None:
    score, flags = score_discovery_row(row)
    row["quality_score"] = str(score)
    row["quality_tier"] = quality_tier(score)
    row["quality_flags"] = ";".join(flags)


def score_discovery_row(row: dict[str, str]) -> tuple[int, list[str]]:
    score = 0
    flags: list[str] = []
    title = row.get("title", "")
    journal = row.get("journal", "")
    sources = split_tokens(row.get("sources", ""))
    collection = row.get("collection", "")
    text = normalize_search_text(
        " ".join([title, journal, row.get("query", ""), row.get("matched_categories", "")])
    )
    category = row.get("category", "")
    broad_scope = category in NON_MALARIA_DISCOVERY_CATEGORIES
    relevance = (
        None
        if broad_scope
        else relevance_decision(
            title=title,
            journal=journal,
            category=category,
            query=row.get("query", ""),
        )
    )

    if is_excluded_publication_title(title):
        return 0, ["excluded_publication_type"]
    if relevance is not None and not relevance.keep:
        return 0, ["scope_excluded", *relevance.reasons]

    if row.get("doi") or row.get("pmid") or row.get("pmcid"):
        score += 18
        flags.append("has_stable_identifier")
    else:
        score -= 8
        flags.append("missing_stable_identifier")

    if row.get("pdf_url"):
        score += 18
        flags.append("oa_pdf_available")
    elif row.get("is_open_access") == "yes":
        score += 6
        flags.append("oa_metadata_only")
    else:
        score -= 10
        flags.append("no_oa_pdf_url")

    if any(source in {"europepmc", "openalex"} for source in sources):
        score += 10
        flags.append("indexed_metadata_source")
    if any(source in {"biorxiv", "medrxiv"} for source in sources):
        score -= 8
        flags.append("preprint_review_required")

    if is_high_signal_journal(journal):
        score += 16
        flags.append("high_signal_journal")

    if collection_contains(collection, "malaria") and any(
        term in text for term in MALARIA_SIGNAL_TERMS
    ):
        score += 18
        flags.append("malaria_topic_signal")
    if collection_contains(collection, "scrna_annotation") and any(
        term in text for term in SCRNA_SIGNAL_TERMS
    ):
        score += 18
        flags.append("scrna_annotation_signal")
    if collection_contains(collection, "host_biology") and any(
        term in text for term in HOST_BIOLOGY_SIGNAL_TERMS
    ):
        score += 18
        flags.append("host_biology_signal")
    if collection in {"bioinformatics_tools", "methods"} and any(
        term in text for term in PERTURBATION_SIGNAL_TERMS
    ):
        score += 18
        flags.append("perturbation_method_signal")

    lower_title = normalize_search_text(title)
    if "systematic review" in lower_title or "meta-analysis" in lower_title:
        score += 8
        flags.append("evidence_synthesis")
    elif "review" in lower_title:
        score += 4
        flags.append("review_article")

    for term, penalty in LOW_SIGNAL_TITLE_TERMS.items():
        if term in lower_title:
            score += penalty
            flags.append(term.replace(" ", "_"))

    if row.get("year", "").isdigit() and int(row["year"]) >= date.today().year - 2:
        score += 4
        flags.append("recent")

    return max(0, min(100, score)), dedupe_tokens(flags)


def quality_tier(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "candidate"
    if score >= 35:
        return "review"
    return "low"


def is_high_signal_journal(journal: str) -> bool:
    normalized = normalize_search_text(journal)
    if not normalized:
        return False
    for term in HIGH_SIGNAL_JOURNAL_TERMS:
        if normalized == term:
            return True
        if term != "nature" and normalized.startswith(f"{term} "):
            return True
    return False


def quality_at_least(tier: str, minimum: str) -> bool:
    return QUALITY_TIER_ORDER.get(tier, 0) >= QUALITY_TIER_ORDER.get(minimum, 0)


def collection_contains(collection: str, value: str) -> bool:
    return value in {item.strip() for item in collection.split(";") if item.strip()}


def download_ranked_rows(
    rows: list[dict[str, str]],
    *,
    bib_dir: Path | None,
    dry_run: bool,
    download_min_tier: str,
    download_limit: int,
    require_doi: bool,
    download_strategy: str,
) -> None:
    eligible: list[dict[str, str]] = []
    for row in rows:
        if row.get("pdf_status") in {"already_present", "excluded", "search_failed"}:
            continue
        if require_doi and not normalize_doi(row.get("doi", "")):
            row["pdf_status"] = "missing_doi"
            row["message"] = merge_messages(
                row.get("message", ""),
                "",
                "not downloaded: DOI missing; pass --allow-missing-doi for manual exceptions",
            )
            continue
        if not row.get("pdf_url"):
            row["pdf_status"] = "no_pdf_url"
            continue
        if not quality_at_least(row.get("quality_tier", "low"), download_min_tier):
            row["pdf_status"] = "below_quality_threshold"
            row["message"] = merge_messages(
                row.get("message", ""),
                "",
                f"not downloaded: quality_tier={row.get('quality_tier', 'low')} below threshold={download_min_tier}",
            )
            continue
        eligible.append(row)

    ranked = rank_download_rows(eligible, strategy=download_strategy, limit=download_limit)
    successful_downloads = 0
    for row in ranked:
        if download_limit > 0 and successful_downloads >= download_limit:
            row["pdf_status"] = "not_downloaded_limit"
            row["message"] = merge_messages(
                row.get("message", ""),
                "",
                f"not downloaded: outside top {download_limit} ranked eligible hits",
            )
            continue
        row.update(download_row(row, bib_dir=bib_dir, dry_run=dry_run))
        if row.get("pdf_status") in {"downloaded", "dry_run"}:
            successful_downloads += 1


def download_rank_key(row: dict[str, str]) -> tuple[int, int, int, int]:
    score = int(row.get("quality_score") or 0)
    source_count = len(split_tokens(row.get("sources", "")))
    year = int(row["year"]) if row.get("year", "").isdigit() else 0
    pdf_directness = 1 if row.get("pdf_url", "").lower().split("?", 1)[0].endswith(".pdf") else 0
    return score, source_count, year, pdf_directness


def rank_download_rows(
    rows: list[dict[str, str]], *, strategy: str, limit: int
) -> list[dict[str, str]]:
    ranked = sorted(rows, key=download_rank_key, reverse=True)
    if strategy != "diverse" or limit <= 0:
        return ranked
    return diverse_ranked_rows(ranked)


def diverse_ranked_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        category = first_category(row.get("category", "")) or "unknown"
        groups.setdefault(category, []).append(row)
    for group in groups.values():
        group.sort(key=download_rank_key, reverse=True)

    known_order = [category for category in CATEGORY_DOWNLOAD_PRIORITY if category in groups]
    remaining_order = sorted(
        (category for category in groups if category not in known_order),
        key=lambda category: download_rank_key(groups[category][0]),
        reverse=True,
    )
    category_order = known_order + remaining_order
    ranked: list[dict[str, str]] = []
    while len(ranked) < len(rows):
        moved = False
        for category in category_order:
            group = groups.get(category) or []
            if not group:
                continue
            ranked.append(group.pop(0))
            moved = True
        if not moved:
            break
    return ranked


def first_category(value: str) -> str:
    return (value or "").split(";", 1)[0].strip()


def download_row(row: dict[str, str], *, bib_dir: Path | None, dry_run: bool) -> dict[str, str]:
    pdf_url = row.get("pdf_url", "").strip()
    if not pdf_url:
        return {
            "pdf_status": "no_pdf_url",
            "local_pdf_relpath": "",
            "message": merge_messages(row.get("message", ""), "", "no OA PDF URL found"),
        }
    if bib_dir is None:
        return {
            "pdf_status": "not_downloaded",
            "local_pdf_relpath": "",
            "message": merge_messages(row.get("message", ""), "", "PFEMPIRE_BIB_DIR missing"),
        }

    relpath = unique_pdf_relpath(
        bib_dir, row.get("category", ""), row.get("author", ""), row.get("year", "")
    )
    target_path = bib_dir / relpath
    if dry_run:
        return {
            "pdf_status": "dry_run",
            "local_pdf_relpath": relpath.as_posix(),
            "message": merge_messages(row.get("message", ""), "", pdf_url),
        }
    try:
        download_pdf(pdf_url, target_path)
    except Exception as exc:
        return {
            "pdf_status": "download_failed",
            "local_pdf_relpath": relpath.as_posix(),
            "message": merge_messages(row.get("message", ""), "", str(exc)),
        }
    return {
        "pdf_status": "downloaded",
        "local_pdf_relpath": relpath.as_posix(),
        "message": merge_messages(row.get("message", ""), "", pdf_url),
    }


def load_existing_titles(inventory_path: Path) -> list[dict[str, str]]:
    if not inventory_path.exists():
        return []
    with inventory_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = []
        for row in reader:
            title = " ".join(
                value
                for value in [
                    row.get("pdf_metadata_title", ""),
                    row.get("guessed_title", ""),
                    row.get("stem", ""),
                    row.get("first_page_text_sample", "")[:250],
                ]
                if value
            )
            if title:
                rows.append({"title": title, "relpath": row.get("relpath_from_bib", "")})
        return rows


def load_existing_identifiers(registry_path: Path) -> dict[str, str]:
    if not registry_path.exists():
        return {}
    identifiers: dict[str, str] = {}
    with registry_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            relpath = row.get("local_pdf_relpath", "")
            for identifier in all_identifiers(row):
                if identifier and relpath:
                    identifiers.setdefault(identifier, relpath)
    return identifiers


def best_existing_match(
    row: dict[str, str], existing_titles: list[dict[str, str]]
) -> dict[str, str] | None:
    title = row.get("title", "")
    if not title:
        return None
    best: dict[str, str] | None = None
    for existing in existing_titles:
        score = fuzz.token_set_ratio(title, existing["title"])
        if score >= 93 and (best is None or score > float(best["score"])):
            best = {"score": f"{score:.1f}", "relpath": existing["relpath"]}
    return best


def unique_pdf_relpath(bib_dir: Path, category: str, author: str, year: str) -> Path:
    safe_category = sanitize_path_part((category or "general_malaria").split(";", 1)[0])
    safe_author = sanitize_path_part(author or "Unknown")
    safe_year = year if re.fullmatch(r"\d{4}", year or "") else "unknown"
    base = Path(safe_category) / f"{safe_author}_{safe_year}.pdf"
    candidate = base
    suffix = 2
    while (bib_dir / candidate).exists():
        candidate = Path(safe_category) / f"{safe_author}_{safe_year}_{suffix}.pdf"
        suffix += 1
    return candidate


def sanitize_path_part(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value.strip()).encode("ascii", "ignore").decode("ascii")
    )
    clean = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_")
    return clean or "unknown"


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def normalize_pmcid(value: str) -> str:
    pmcid = value.strip().upper()
    if not pmcid:
        return ""
    return pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"


def normalize_numeric_id(value: str) -> str:
    match = re.search(r"\d+", value or "")
    return match.group(0) if match else ""


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def merge_token_field(left: str, right: str, *, sort_by_source: bool = False) -> str:
    tokens = dedupe_tokens(split_tokens(left) + split_tokens(right))
    if sort_by_source:
        tokens = sort_sources(tokens)
    return ";".join(tokens)


def split_tokens(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;|]", value or "") if item.strip()]


def dedupe_tokens(tokens: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def sort_sources(sources: list[str]) -> list[str]:
    return sorted(sources, key=lambda source: SOURCE_ORDER.get(source, len(SOURCE_ORDER)))


def merge_messages(left: str, right: str, extra: str) -> str:
    messages = [message for message in [left.strip(), right.strip(), extra.strip()] if message]
    return " | ".join(dedupe_tokens(messages))


def get_configured_bib_dir(*, required: bool) -> Path | None:
    try:
        bib_dir = get_bib_dir()
    except OriginRAGConfigError:
        if required:
            raise
        return None
    return bib_dir


def collection_for_category(category: str) -> str:
    return COLLECTION_BY_CATEGORY.get((category or "unknown").strip(), "unknown")


def error_row(
    query_id: str, query: str, collection: str, category: str, source: str, message: str
) -> dict[str, str]:
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "discovered_at": datetime.now().isoformat(timespec="seconds"),
            "sources": source,
            "query_id": query_id,
            "query": query,
            "collection": collection,
            "category": category,
            "matched_categories": category,
            "pdf_status": "search_failed",
            "quality_score": "0",
            "quality_tier": "low",
            "quality_flags": "search_failed",
            "dedupe_key": f"error:{source}:{query_id}",
            "message": message,
        }
    )
    return row


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def slugify(value: str) -> str:
    return sanitize_path_part(value.lower())[:80] or "query"


if __name__ == "__main__":
    raise SystemExit(main())
