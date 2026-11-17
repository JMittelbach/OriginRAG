import os

import pytest

from originrag.writing import WritingStore, bibliography_connection, tex_without_comments
from originrag.writing_templates import template_files


def project(source, extra=None, **metadata):
    return {'main': 'main.tex', 'files': {'main.tex': source, **(extra or {})}, **metadata}


def test_unrelated_bundled_sources_do_not_connect_document():
    main = '\\documentclass{article}\n\\begin{document}Text.\\end{document}'
    p = project(main, {'publisher-example.tex': '\\bibliography{reference}',
                       'publisher-doc.tex': '\\printbibliography',
                       'unrelated.cls': '\\RequirePackage{biblatex}',
                       'unused.sty': '\\newcommand{\\example}{\\bibliography{old}}'})
    needed, edit = bibliography_connection(p, 'references.bib', True)
    assert needed and edit and '\\bibliography{references}' in edit['after']
    assert p['files']['main.tex'] == main


def test_literal_document_input_graph_preserves_bibliography_and_style():
    p = project('\\begin{document}\\input{sections/body}\\end{document}',
                {'sections/body.tex': '\\include{references-section}',
                 'references-section.tex': '\\bibliographystyle{custom}\\bibliography{my-references}'})
    assert bibliography_connection(p, 'references.bib', True) == (False, None)
    p['files']['references-section.tex'] = '\\bibliographystyle{custom}'
    needed, edit = bibliography_connection(p, 'references.bib', True)
    assert needed and edit and '\\bibliographystyle' not in edit['after']
    assert '\\bibliography{references}' in edit['after']


def test_commented_and_post_document_includes_are_not_reachable():
    p = project('% \\input{unused}\n\\begin{document}\\end{document}\n\\input{unused}',
                {'unused.tex': '\\bibliography{not-used}'})
    needed, edit = bibliography_connection(p, 'references.bib', True)
    assert needed and edit['after'].index('\\bibliography{references}') < edit['after'].index('\\end{document}')


@pytest.mark.parametrize('setup', [r'\input{\dynamic}', r'\input\dynamic', r'\input{../outside}', r'\input{missing}', r'\include{https://example.org/x}'])
def test_dynamic_missing_or_unsafe_inputs_are_conservative(setup):
    p = project('\\begin{document}' + setup + '\\end{document}')
    assert bibliography_connection(p, 'references.bib', True) == (False, None)


def test_input_cycles_terminate_without_losing_relevant_sources():
    p = project('\\begin{document}\\input{section}\\end{document}', {'section.tex': '\\input{main}\n\\bibliography{references}'})
    assert bibliography_connection(p, 'references.bib', True) == (False, None)


def test_explicit_custom_biblatex_class_is_preserved():
    p = project('\\documentclass{custom}\n\\begin{document}Text.\\end{document}',
                {'custom.cls': '\\RequirePackage[backend=biber]{biblatex}'})
    assert bibliography_connection(p, 'references.bib', True) == (False, None)
    p['files']['custom.cls'] = '\\RequirePackage{custom-bibliography}'
    p['files']['custom-bibliography.sty'] = '\\RequirePackage{biblatex}'
    assert bibliography_connection(p, 'references.bib', True) == (False, None)


def test_existing_style_is_not_duplicated_and_comment_selects_available_style():
    p = project('\\bibliographystyle{my-style}\n\\begin{document}Text.\\end{document}')
    needed, edit = bibliography_connection(p, 'references.bib', True)
    assert needed and tex_without_comments(edit['after']).count('\\bibliographystyle') == 1
    p = project('% \\bibliographystyle{my-style}\n\\begin{document}Text.\\end{document}', {'my-style.bst': 'Style'})
    assert '\\bibliographystyle{my-style}' in tex_without_comments(bibliography_connection(p, 'refs.bib', True)[1]['after'])
    p['files'].pop('my-style.bst')
    assert '\\bibliographystyle{plain}' in tex_without_comments(bibliography_connection(p, 'refs.bib', True)[1]['after'])


@pytest.mark.parametrize('template_id', ['article', 'thesis', 'bioinformatics', 'g3', 'plos-one', 'plos-compbiol', 'plos-genetics'])
def test_bundled_template_connects_real_main_with_matching_style(template_id):
    files, main = template_files(template_id)
    needed, edit = bibliography_connection({'main': main, 'files': files, 'template': template_id}, 'references.bib', True)
    active = tex_without_comments(edit['after'])
    assert needed and active.count('\\bibliography{references}') == 1
    assert active.count('\\bibliographystyle') == 1
    style = 'plos2025' if template_id.startswith('plos-') else ('plainnat' if template_id == 'thesis' else ('plain' if template_id == 'article' else 'oup-abbrvnat'))
    assert '\\bibliographystyle{' + style + '}' in active


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires prepared isolated TeX compiler')
@pytest.mark.parametrize('template_id', ['article', 'thesis', 'bioinformatics', 'g3', 'plos-one', 'plos-compbiol', 'plos-genetics'])
def test_create_link_automatically_sync_cite_and_compile_template(tmp_path, template_id):
    from originrag.writing_compile import compile_project
    import fitz

    catalogue = [{'paper_id': 'citation-test', 'title': 'Synthetic integration reference', 'authors': 'Doe, Jane', 'year': '2026', 'journal': 'Test journal'}]
    store = WritingStore(tmp_path)
    library = store.change({'action': 'create_collection', 'name': 'My references'}, catalogue)
    library = store.change({'action': 'collection_paper', 'id': library['id'], 'revision': library['revision'], 'paper_id': 'citation-test'}, catalogue)
    p = store.change({'action': 'create_project', 'name': 'Manuscript', 'template': template_id}, catalogue)
    assert p.get('template') == template_id
    p = store.change({**p, 'action': 'save_project', 'collection': library['id']}, catalogue)
    preview = store.bibliography_preview(p['id'], catalogue, connect_document=True)
    assert not preview['added'] and not preview['updated'] and not preview['conflicts']
    assert p['bibliography_status']['state'] == 'synced'
    key = store.collection_references(library['id'], catalogue)['references'][0]['key']
    p['files'][p['main']] = p['files'][p['main']].replace('\\section{Introduction}', '\\section{Introduction}\nCitation \\cite{' + key + '}.')
    if template_id == 'thesis':
        p['files']['chapters/introduction.tex'] += '\nCitation \\cite{' + key + '}.\n'
    p = store.change({**p, 'action': 'save_project'}, catalogue)
    assert '\n\\bibliography{references}' in p['files'][p['main']]
    result = compile_project(p)
    assert result['ok'], result['log']
    with fitz.open(stream=result['pdf'], filetype='pdf') as document:
        assert 'Synthetic integration reference' in ''.join(page.get_text() for page in document)
