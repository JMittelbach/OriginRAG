# Quill templates

The gallery contains ready-to-edit starter manuscripts and the complete,
unmodified OUP authoring-template distribution, version 1.5 (14 July 2026).
The journal starters are written for Quill using that publisher class.
They are not the publisher's sample manuscript or a guarantee of submission
compliance. Journal requirements must still be checked before submission.

The OUP class, bibliography styles, sample source, and documentation are
Copyright 2020–2026 Oxford University Press, distributed under LPPL 1.3 or
later, not OriginRAG's MIT licence. The upstream archive and its licence are
included in `vendor/`. Its files are preserved byte-for-byte. In projects,
the upstream `README` is named `OUP-README.txt` to support text editing.
Original filenames of all LaTeX sources are unchanged.

The separate MIT-licensed `originwrite-oup-xetex.sty` helper disables the
class's legacy PostScript page-box hook only under XeTeX/Tectonic. The
publisher's crop package still supplies the paper dimensions. The class
and bibliography styles themselves remain unmodified. The helper has no
effect on pdfLaTeX. The publisher class's optional external word-count
command remains disabled by the untrusted compiler.

The small Quill starter documents are MIT-licensed. Their licence is
included in every project. No publisher logos or third-party paper PDFs
are added.

## Sources verified 16 September 2026

- Package and licence declaration: https://ctan.org/pkg/oup-authoring-template
- Official package: https://mirrors.ctan.org/macros/latex/contrib/oup-authoring-template.zip
- LPPL text: https://www.latex-project.org/lppl/lppl-1-3c.txt
- Bioinformatics recommends the OUP template:
  https://academic.oup.com/bioinformatics/pages/author-guidelines
- G3 provides both a GSA-specific Overleaf template and the generic OUP template:
  https://academic.oup.com/g3journal/pages/author-guidelines

The G3 gallery entry uses the generic OUP template, not the separate GSA
class. The OUP gallery entry supports the publisher's large, contemporary
page design. Specific journal requirements can differ.

The Springer Nature archive was inspected but is not redistributed here.
Its class has an additional distribution condition and the archive does
not clearly license every supporting file. A user can still import their
own lawfully obtained project ZIP.

## Integrity and package preparation

`provenance.json` records the upstream hashes. The loader checks the archive
and licence checksums before creating a project. Runtime creation never
fetches a URL. The complete assets directory must be shipped with the app.

The host can pre-download TeX dependencies for these fixed, reviewed
starters with `python scripts/prepare_writing_templates.py --warm-cache`.
Set `TECTONIC_CACHE_DIR` to the compiler's cache, if using a non-default
cache. This command allows networking only for these bundled sources.
Normal user compilation remains network-isolated and uses cached packages.
