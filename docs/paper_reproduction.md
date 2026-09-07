# Paper reproduction

The OriginRAG repository installs the application. A separate paper archive
contains the selected research materials needed to inspect and rerun the
manuscript analyses. Internal working directories are not part of the software
checkout.

## Archive contents

The first archive is named `originrag-paper-reproduction-v1`. It records the
software commit and SHA-256 hashes for every included file, including local
research scripts that are not in the application repository. It contains:

- the software source used for recomputation and its MIT licence;
- synthetic generators, independent reference calculations, controlled
  experiments and the retained historical results;
- author-curated report/origin tables, provisional overlap labels and their
  sampling key, and the frozen bibliographic corpus manifest;
- manuscript sources, figure inputs, figure-generation code and compiled PDFs;
- a standalone `reproduce.py` runner, execution instructions and a table mapping
  manuscript results to experiments, inputs, parameters and access conditions.

## What can be reproduced without the PDF corpus?

| Material | Included operation | Additional input needed |
| --- | --- | --- |
| E025 / E033 / E055 reference agreement | Seeded reference comparisons | None; full campaigns can take substantial time |
| E034 / E050 / E051 / E053 / E066 controlled analyses | Generate scenarios, compare decisions and endpoints, recompute summaries | None |
| E044 qualitative origin map | Validate curated assignments and recompute counts | Full texts for an independent reassessment of the curation |
| E045 / E047 overlap audit | Recompute descriptive counts from provisional labels | Full texts and new reviewers for independent adjudication |
| E065 30-pool runtime | Inspect retained results and regenerate the plot | Exact frozen candidate passages and configuration for a new runtime run |
| Publication-family model controls | Inspect retained result summaries | Original candidate passages, prompts and model environment to rerun generation |
| Manuscript and figures | Rebuild from included sources and derived data | LaTeX or Tectonic for PDFs |

No publisher PDFs, extracted passages, model prompts, live indexes or user
sessions are included. Input hashes and bibliographic identifiers document
restricted dependencies; reconstructing a different corpus is not a rerun of
the frozen one. A new GPU or model run is not required for the self-contained
analyses.

## Reproduction versus software CI

Software CI exercises installation, unit tests, command-line smoke checks and
package builds. It does not run the full manuscript campaign or establish
answer quality. The archive's runner reports the software revision, its own
parameters, comparison outcomes and elapsed time in a new output directory.

Historical result files are retained unchanged. New solver runs may change
work counters, exact-completion diagnostics and witness choices. Scientific
decision and endpoint comparisons are reported separately from those changes;
old timings are not relabelled as current measurements.
