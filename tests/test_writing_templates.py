import base64
import hashlib
import os
from pathlib import Path
import zipfile

import pytest

from originrag.writing import validate_files
from originrag.writing_templates import ASSETS, ARCHIVE_HASH, list_templates, template_files


def test_gallery_is_local_and_returned_metadata_is_not_mutable():
    gallery = list_templates()
    assert len(gallery) == 3
    assert len({entry['id'] for entry in gallery}) == 3
    assert all(entry['main'] == 'main.tex' and entry['licence'] and entry['source'].startswith('https://') for entry in gallery)
    gallery[1]['journals'].append('changed')
    assert 'changed' not in list_templates()[1]['journals']


@pytest.mark.parametrize('template_id', ['article', 'bioinformatics', 'g3'])
def test_complete_template_projects(template_id):
    files, main = template_files(template_id)
    validate_files(files, main)
    assert 'OriginWrite-LICENSE.txt' in files and 'references.bib' in files
    assert files['references.bib'] == ''
    assert '\\begin{document}' in files[main]
    if template_id != 'article':
        assert 'oup-authoring-template.cls' in files and 'oup-abbrvnat.bst' in files
        assert 'LPPL-1.3c.txt' in files and 'OUP-README.txt' in files
        assert base64.b64decode(files['doc/oup-authoring-template-doc.pdf']).startswith(b'%PDF')


@pytest.mark.parametrize('template_id', ['../g3', '/tmp/main.tex', 'https://example.org/t.zip', 'springer', '', None, []])
def test_only_known_template_identifiers(template_id):
    with pytest.raises(ValueError):
        template_files(template_id)


def test_publisher_distribution_is_unmodified_and_complete():
    archive_path = ASSETS / 'vendor/oup-authoring-template-1.5.zip'
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == ARCHIVE_HASH
    files, _ = template_files('bioinformatics')
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = member.filename.removeprefix('oup-authoring-template/')
            name = 'OUP-README.txt' if name == 'README' else name
            value = base64.b64decode(files[name]) if Path(name).suffix == '.pdf' else files[name].encode('utf-8')
            assert value == archive.read(member)


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires prepared isolated TeX compiler')
@pytest.mark.parametrize('template_id', ['article', 'bioinformatics', 'g3'])
def test_bundled_template_compiles_offline(template_id):
    from originrag.writing_compile import compile_project
    files, main = template_files(template_id)
    result = compile_project({'files': files, 'main': main, 'revision': 1})
    assert result['ok'], result.get('log')
    assert result['pdf'].startswith(b'%PDF')
    assert 'Interpreting PS code failed' not in result['log']
    import fitz
    with fitz.open(stream=result['pdf'], filetype='pdf') as document:
        assert len(document) >= 1
        assert 'Introduction' in ''.join(page.get_text() for page in document)
        if template_id != 'article':
            assert document[0].rect.width == pytest.approx(595, abs=4)
            assert document[0].rect.height == pytest.approx(782, abs=4)


@pytest.mark.skipif(os.environ.get('ORIGINWRITE_TEST_COMPILER') != '1', reason='Requires prepared isolated TeX compiler')
@pytest.mark.parametrize('template_id', ['article', 'bioinformatics', 'g3'])
def test_template_can_cite_synced_bibliography(template_id):
    from originrag.writing_compile import compile_project
    files, main = template_files(template_id)
    files[main] = files[main].replace('% \\bibliographystyle', '\\bibliographystyle').replace('% \\bibliography', '\\bibliography')
    files[main] = files[main].replace('\\section{Introduction}', '\\section{Introduction}\nA test citation \\cite{sample2026}.')
    files['references.bib'] = '@article{sample2026, author={Doe, Jane}, title={Synthetic bibliography test}, journal={Test journal}, year={2026}}\n'
    result = compile_project({'files': files, 'main': main, 'revision': 1})
    assert result['ok'], result.get('log')
    import fitz
    with fitz.open(stream=result['pdf'], filetype='pdf') as document:
        text = ''.join(page.get_text() for page in document)
        assert 'Synthetic bibliography test' in text
    assert 'errors were issued by BibTeX' not in result['log']
