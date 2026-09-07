# OriginRAG paper library

PDFs are organized by their primary retrieval topic. The registry in
`data/metadata/paper_registry.tsv` remains the authoritative mapping from paper
IDs and DOI metadata to these files.

## Folders

- `papers/single_cell/annotation/`: cell-type annotation and label transfer
- `papers/single_cell/harmonization_integration/`: label harmonization, batch correction, and dataset integration
- `papers/single_cell/tools_methods/`: other single-cell tools and computational methods
- `papers/foundation_models/`: biological foundation models, transformers, and LLM-related methods
- `papers/malaria/var_genes/`: var genes, PfEMP1, switching, and cytoadhesion
- `papers/malaria/general_biology/`: general malaria and parasite biology
- `papers/malaria/resistance_diagnostics/`: drug resistance, diagnostics, and treatment
- `papers/malaria/omics/`: malaria transcriptomics and other omics studies
- `papers/malaria/tools_methods/`: malaria-specific computational and experimental methods
- `papers/immunology/`: general host and immune biology
- `papers/other/`: papers that do not yet fit a stable primary category

Run `./originrag bib` after adding or correcting papers. The bibliography
workflow preserves this folder structure and updates all metadata paths.

## Partial library mirrors

`data/metadata/paper_registry.tsv` is authoritative even when the current machine contains only a
subset of the PDFs. Registry builds preserve known rows that are absent from the local inventory
and preserve curated metadata for known paths. New local PDFs may add rows; intentional changes to
known bibliographic fields belong in `data/metadata/metadata_corrections.tsv`.

Do not replace `data/metadata/local_pdf_inventory.tsv` with a partial-machine scan and commit it as
if it represented the complete corpus. Refresh the full inventory on the machine that contains the
complete PDF library. PDFs are intentionally not transported by Git, so use SHA-256 manifests to
reconcile files across machines.
