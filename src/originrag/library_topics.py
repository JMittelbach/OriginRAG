import re
from collections.abc import Mapping


LIBRARY_TOPICS = (
    ("var_pfemp1", "Multigene families & antigenic variation", {"var_pfemp1", "rifin", "stevor", "multigene_families"}),
    ("malaria_biology", "Malaria biology", {"general_malaria", "parasite_biology", "malaria"}),
    ("host_disease", "Host & disease biology", {"host_immunity", "host_immunity_general", "malaria_host_genetics", "sex_diff", "covid_host_response", "infectious_disease_host_response", "noninfectious_disease_biology", "host_tissue_atlas"}),
    ("malaria_omics_resistance", "Malaria omics & resistance", {"resistance_dx", "malaria_omics", "malaria_host_omics", "malaria_tools"}),
    ("single_cell_methods", "Single-cell methods", {"scrna_annotation", "sc_tools", "sc_harmonization", "latent_variable_methods"}),
    ("spatial_transcriptomics", "Spatial transcriptomics", {"spatial_transcriptomics", "spatial_omics"}),
    ("genomics_rnaseq", "Genomics, evolution & RNA-seq", {"rna_seq_methods", "genomics", "phylogenetics", "orthology"}),
    ("ai_computational", "AI & computational methods", {"tools_ml", "foundation_models", "bioinformatics"}),
    ("research_methods", "Evidence synthesis & general methods", {"evidence_synthesis", "general_methods"}),
    ("other", "Other", {"other", "uncategorized", "unknown"}),
)


def library_topic(record: Mapping[str, object]) -> tuple[str, str]:
    title = str(record.get("title") or "").casefold()
    title = re.sub(r"[‐‑–—]", "-", title)
    category = str(record.get("category") or record.get("collection") or "").casefold()
    group = ""
    if re.search(r"spatial(?:ly[- ]resolved)?[- ]+(?:single[- ]cell[- ]+)?(?:transcriptom|gene expression|omics|multiomics)|\b(?:merfish|seqfish|slide-seq|slide-tags|stereo-seq|seq-scope|dbit-seq|squidpy|spatialdata|giotto|banksy|cell2location|spatialexperiment)\b", title):
        group = "spatial_transcriptomics"
    elif re.search(r"\b(?:pfemp1|rifins?|stevors?|multigene|multi-gene)\b|\bvar genes?\b|antigenic variation", title):
        group = "var_pfemp1"
    elif re.search(r"\b(?:foundation models?|large language models?|geneformer|scgpt|scfoundation)\b", title):
        group = "ai_computational"
    elif re.search(r"\b(?:deseq2?|edger|limma|voom|kallisto|featurecounts|rsubread|mafft|muscle|iq-tree|raxml|fasttree|orthofinder|orthomcl|eggnog|hmmer|mmseqs2|interpro|pfam)\b|\bsalmon\b.*quantification|\b(?:orthology|phylogenetic|phylogenomic|sequence alignment)\b", title):
        group = "genomics_rnaseq"
    elif re.search(r"single[- ]cell", title) and re.search(r"\b(?:methods?|benchmark\w*|integration|deconvolution|normalization|clustering|analysis framework|best practices|experimental design|pseudoreplication)\b", title):
        group = "single_cell_methods"
    elif re.search(r"rna[- ]seq|rna sequencing", title) and re.search(r"\b(?:analysis|methods?|normalization|quantification|differential|workflow|alignment)\b", title):
        group = "genomics_rnaseq"
    elif re.search(r"\b(?:nextflow|snakemake|nf-core|multiqc|fastp|cutadapt|umi-tools|gene set enrichment|gsea|gsva|multiple testing|false discovery|batch effects|surrogate variable|power analysis|sample size|meta-analysis|systematic review)\b", title):
        group = "research_methods"
    for key, label, categories in LIBRARY_TOPICS:
        if group == key or (not group and category in categories):
            return key, label
    return "other", "Other"
