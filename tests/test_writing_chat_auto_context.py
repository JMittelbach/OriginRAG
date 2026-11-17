import json

import pytest

from originrag import writing_chat as chat
from test_writing_chat import manuscript as manuscript, request


def data(prepared):
    return json.loads(prepared['prompt'][prepared['prompt'].index('{"project":'):])


def test_small_project_is_automatically_included_without_any_file_picker(manuscript):
    prepared = chat.prepare_request(manuscript, request(manuscript, auto_context=True))
    payload = data(prepared)
    assert prepared['automatic_context'] is True
    assert {row['file'] for row in payload['files']} == {'main.tex', 'chapters/intro.tex', 'references.bib'}
    assert all(row['complete'] for row in payload['files'])
    assert all(row['content'] == manuscript['files'][row['file']] for row in payload['files'])
    assert 'aGVsbG8=' not in prepared['prompt']


def test_large_project_always_has_main_preamble_and_bounded_honest_context(manuscript):
    preamble = '\\documentclass{article}\n\\usepackage{booktabs}\n\\newcommand{\\projectname}{Context test}\n\\begin{document}\n'
    manuscript['files']['main.tex'] = preamble + '\n'.join(f'Body paragraph {index}: text about the draft.' for index in range(3000)) + '\nHIDDEN_TAIL_MARKER'
    manuscript['files']['publisher.cls'] = 'Vendor implementation.\n' * 4000
    prepared = chat.prepare_request(manuscript, request(manuscript, auto_context=True,
        focus_file='chapters/intro.tex', message='Was steht in der Präambel vonmain.tex?'))
    payload = data(prepared)
    main = next(row for row in payload['files'] if row['file'] == 'main.tex')
    assert main['complete'] is False
    assert any(preamble in excerpt['content'] for excerpt in main['excerpts'])
    assert 'HIDDEN_TAIL_MARKER' not in prepared['prompt']
    assert prepared['input_bytes'] <= chat.context_tokens() - chat.OUTPUT_TOKENS - 1024
    assert 'Do not assume omitted lines were read' in prepared['prompt']
    raw = {'answer': 'Review.', 'edits': [{'file': 'main.tex', 'original': 'HIDDEN_TAIL_MARKER',
        'replacement': 'Not allowed', 'reason': 'Unseen text'}]}
    prepared['mode'] = 'edit'
    assert chat.parse_reply(json.dumps(raw), prepared)['edits'] == []


def test_large_project_question_can_locate_relevant_later_source_lines(manuscript):
    manuscript['files']['main.tex'] = ('\\documentclass{article}\n\\begin{document}\n'
        + '\n'.join(f'Unrelated line {index}.' for index in range(3000))
        + '\n\\newcommand{\\specialsetting}{the precise value}\n\\end{document}')
    prepared = chat.prepare_request(manuscript, request(manuscript, auto_context=True,
        message='Explain specialsetting in main.tex.'))
    assert r'\specialsetting' in prepared['prompt'] and 'the precise value' in prepared['prompt']
    assert len(prepared['prompt']) < 30000


def test_many_project_files_do_not_crowd_out_main_document_context(manuscript):
    for number in range(190):
        manuscript['files'][f'attachments/long-descriptive-filename-for-a-local-project-asset-{number:03d}.txt'] = f'Extra project text {number}.'
    prepared = chat.prepare_request(manuscript, request(manuscript, auto_context=True, message='What does main.tex do?'))
    payload = data(prepared)
    assert payload['inventory_omitted'] > 0
    assert any(row['file'] == 'main.tex' for row in payload['files'])
    assert prepared['input_bytes'] <= chat.context_tokens() - chat.OUTPUT_TOKENS - 1024


def test_selected_passage_near_file_end_is_included_without_sending_entire_large_file(manuscript):
    source = '🪶 Header.\n' + 'A long unrelated paragraph.\n' * 4000 + 'Correct this sentense.'
    manuscript['files']['main.tex'] = source
    selection = {'file': 'main.tex', 'text': 'Correct this sentense.',
                 'from': len(source[:-len('Correct this sentense.')].encode('utf-16-le')) // 2,
                 'to': len(source.encode('utf-16-le')) // 2}
    prepared = chat.prepare_request(manuscript, request(manuscript, auto_context=True, selection=selection, mode='edit'))
    assert selection['text'] in prepared['prompt']
    result = chat.parse_reply('{"answer":"Review.","replacement":"Correct this sentence."}', prepared)
    assert len(result['edits']) == 1 and result['edits'][0]['from'] == selection['from']
    assert prepared['input_bytes'] < 28000


@pytest.mark.parametrize('fields', [{'focus_file': '../other-project.tex'}, {'auto_context': 'yes'}, {'focus_file': None}])
def test_automatic_context_cannot_read_a_different_project(manuscript, fields):
    with pytest.raises(ValueError):
        chat.prepare_request(manuscript, request(manuscript, auto_context=True) | fields)
