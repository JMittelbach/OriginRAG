# Structured Paper Summary

Summarize one paper using only the supplied chunks and metadata.
Return JSON only. Do not add facts that are not supported by the supplied chunks.

Each paper summary must include:
- paper_id
- title
- year
- short_summary
- background
- methods
- datasets_or_cohorts
- key_findings
- limitations
- host_angle
- parasite_angle
- immune_angle
- omics_angle
- computational_angle
- disease_context
- relevance_to_pfempire
- source_chunks

Use empty strings or empty lists when evidence is absent.
