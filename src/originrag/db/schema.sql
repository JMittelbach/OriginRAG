CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER,
    doi TEXT,
    pmid TEXT,
    pmcid TEXT,
    domain TEXT,
    topics TEXT,
    methods TEXT,
    systems TEXT,
    local_pdf_relpath TEXT,
    activation_status TEXT,
    fulltext_status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    chunk_index INTEGER,
    section TEXT,
    page_start INTEGER,
    page_end INTEGER,
    text TEXT NOT NULL,
    quality_flags TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers (paper_id)
);

CREATE TABLE IF NOT EXISTS evidence_packs (
    evidence_pack_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    task_mode TEXT NOT NULL,
    n_chunks INTEGER DEFAULT 0,
    n_unique_papers INTEGER DEFAULT 0,
    corpus_version TEXT,
    run_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id TEXT PRIMARY KEY,
    evidence_pack_id TEXT,
    paper_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    source_label TEXT,
    section TEXT,
    page_start INTEGER,
    page_end INTEGER,
    text TEXT NOT NULL,
    retrieval_score REAL,
    keyword_score REAL,
    semantic_score REAL,
    final_score REAL,
    quality_flags TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (evidence_pack_id) REFERENCES evidence_packs (evidence_pack_id),
    FOREIGN KEY (paper_id) REFERENCES papers (paper_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks (chunk_id)
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    claim_hash TEXT,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    evidence_type TEXT,
    evidence_strength TEXT,
    direction TEXT,
    uncertainty TEXT,
    organism TEXT,
    pathogen TEXT,
    host_system TEXT,
    disease_context TEXT,
    phenotype TEXT,
    parasite_feature TEXT,
    host_feature TEXT,
    method TEXT,
    dataset_or_cohort TEXT,
    source_paper_id TEXT NOT NULL,
    source_chunk_id TEXT NOT NULL,
    source_section TEXT,
    source_pages TEXT,
    source_text TEXT,
    entities_json TEXT,
    entity_ids TEXT,
    claim_quality_flags TEXT,
    citation_short TEXT,
    title TEXT,
    year TEXT,
    doi TEXT,
    confidence REAL,
    duplicate_count INTEGER DEFAULT 1,
    claim_status TEXT DEFAULT 'auto_extracted',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claim_entities (
    claim_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    role TEXT,
    matched_text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (claim_id, entity_id),
    FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT,
    source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    object_entity_id TEXT NOT NULL,
    claim_id TEXT,
    paper_id TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subject_entity_id) REFERENCES entities (entity_id),
    FOREIGN KEY (object_entity_id) REFERENCES entities (entity_id),
    FOREIGN KEY (claim_id) REFERENCES claims (claim_id),
    FOREIGN KEY (paper_id) REFERENCES papers (paper_id)
);

CREATE TABLE IF NOT EXISTS paper_summaries (
    paper_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers (paper_id)
);

CREATE TABLE IF NOT EXISTS research_gaps (
    gap_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    missing_link TEXT NOT NULL,
    gap_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    hypothesis_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    retrieval_run_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    task_mode TEXT,
    query_plan_json TEXT,
    config_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_runs (
    model_run_id TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model_name TEXT,
    task_mode TEXT,
    prompt_template TEXT,
    input_ref TEXT,
    output_ref TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS training_examples (
    training_example_id TEXT PRIMARY KEY,
    example_type TEXT NOT NULL,
    input_json TEXT NOT NULL,
    label_json TEXT,
    reviewer TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cited_works (
    work_id TEXT PRIMARY KEY,
    doi TEXT,
    pmid TEXT,
    pmcid TEXT,
    title TEXT,
    author TEXT,
    year TEXT,
    journal TEXT,
    citation_text TEXT NOT NULL,
    registry_paper_id TEXT,
    bibliographic_resolution_status TEXT NOT NULL,
    metadata_source TEXT,
    oa_pdf_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS citation_mentions (
    mention_id TEXT PRIMARY KEY,
    citing_paper_id TEXT NOT NULL,
    source_chunk_id TEXT,
    cited_work_id TEXT NOT NULL,
    reference_index INTEGER,
    marker_text TEXT,
    printed_reference TEXT,
    context_text TEXT NOT NULL,
    source_page INTEGER,
    source_rect_json TEXT,
    citation_link_status TEXT NOT NULL,
    scope_status TEXT NOT NULL,
    bibliographic_resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    parser_version TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cited_work_id) REFERENCES cited_works (work_id)
);

CREATE TABLE IF NOT EXISTS claim_sources (
    claim_id TEXT NOT NULL,
    mention_id TEXT NOT NULL,
    cited_work_id TEXT NOT NULL,
    source_role TEXT NOT NULL,
    support_relation TEXT NOT NULL,
    origin_review_status TEXT NOT NULL,
    confidence REAL,
    reviewer TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (claim_id, mention_id, cited_work_id),
    FOREIGN KEY (claim_id) REFERENCES claims (claim_id),
    FOREIGN KEY (mention_id) REFERENCES citation_mentions (mention_id),
    FOREIGN KEY (cited_work_id) REFERENCES cited_works (work_id)
);

CREATE TABLE IF NOT EXISTS scientific_origins (
    origin_id TEXT PRIMARY KEY,
    origin_type TEXT NOT NULL,
    label TEXT NOT NULL,
    accession TEXT,
    review_status TEXT NOT NULL DEFAULT 'review_required',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_origins (
    work_id TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    evidence_locator TEXT,
    review_status TEXT NOT NULL DEFAULT 'review_required',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (work_id, origin_id, relation_type),
    FOREIGN KEY (work_id) REFERENCES cited_works (work_id),
    FOREIGN KEY (origin_id) REFERENCES scientific_origins (origin_id)
);

CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi);
CREATE INDEX IF NOT EXISTS idx_papers_domain ON papers (domain);
CREATE INDEX IF NOT EXISTS idx_chunks_paper_id ON chunks (paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks (section);
CREATE INDEX IF NOT EXISTS idx_evidence_packs_task_mode ON evidence_packs (task_mode);
CREATE INDEX IF NOT EXISTS idx_evidence_items_pack ON evidence_items (evidence_pack_id);
CREATE INDEX IF NOT EXISTS idx_claims_claim_type ON claims (claim_type);
CREATE INDEX IF NOT EXISTS idx_claims_source_paper ON claims (source_paper_id);
CREATE INDEX IF NOT EXISTS idx_claims_source_chunk ON claims (source_chunk_id);
CREATE INDEX IF NOT EXISTS idx_claims_hash ON claims (claim_hash);
CREATE INDEX IF NOT EXISTS idx_claims_doi ON claims (doi);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims (claim_status);
CREATE INDEX IF NOT EXISTS idx_claim_entities_entity ON claim_entities (entity_id);
CREATE INDEX IF NOT EXISTS idx_claim_entities_type ON claim_entities (entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities (entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_normalized_name ON entities (normalized_name);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations (relation_type);
CREATE INDEX IF NOT EXISTS idx_retrieval_runs_task_mode ON retrieval_runs (task_mode);
CREATE INDEX IF NOT EXISTS idx_model_runs_task_mode ON model_runs (task_mode);
CREATE INDEX IF NOT EXISTS idx_training_examples_type ON training_examples (example_type);
CREATE INDEX IF NOT EXISTS idx_cited_works_doi ON cited_works (doi);
CREATE INDEX IF NOT EXISTS idx_cited_works_pmid ON cited_works (pmid);
CREATE INDEX IF NOT EXISTS idx_cited_works_pmcid ON cited_works (pmcid);
CREATE INDEX IF NOT EXISTS idx_citation_mentions_paper ON citation_mentions (citing_paper_id);
CREATE INDEX IF NOT EXISTS idx_citation_mentions_work ON citation_mentions (cited_work_id);
CREATE INDEX IF NOT EXISTS idx_claim_sources_claim ON claim_sources (claim_id);
CREATE INDEX IF NOT EXISTS idx_work_origins_origin ON work_origins (origin_id);
