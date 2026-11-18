import json

import pytest

from originrag import writing_chat as chat
from test_writing_chat import manuscript as manuscript, request


def prepare(p, message, **fields):
    return chat.prepare_request(p, request(p, message=message, auto_context=True, mode='edit') | fields)


def reply(text):
    return json.dumps({'answer': 'Done!', 'insertion': text, 'reason': 'Requested addition'})


@pytest.mark.parametrize('anchor', [r'\end{titlepage}', r'\maketitle', r'\makethesistitle'])
def test_insertion_uses_actual_main_file_for_preview_and_final(manuscript, anchor):
    p = manuscript
    p['files']['main.tex'] = '🪶 Thesis\r\n  ' + anchor + '\r\nAbstract'
    p['files']['style.sty'] = r'\end{titlepage}'
    text = r'\section{Hello World}'
    r = prepare(p, rf'Füge in main.tex {text} direkt nach dem Titelblatt ein.')
    assert chat.reply_schema(r)['properties'] == {'insertion': {'type': 'string', 'enum': [text]}}
    raw = json.dumps({'insertion': text})
    preview = chat.partial_edit(raw[:-1], r)
    result = chat.parse_reply(raw, r)
    final, = result['edits']
    assert preview == {k: final[k] for k in preview}
    assert final['file'] == 'main.tex' and final['from'] == len('🪶 Thesis\r\n'.encode('utf-16-le')) // 2
    assert final['original'] == '  ' + anchor
    assert final['replacement'] == '  ' + anchor + '\r\n' + text
    assert not result['filtered'] and 'Done!' not in result['answer']
    with pytest.raises(chat.LanguageUnavailable):
        chat.parse_reply(reply(None), r)
    with pytest.raises(chat.LanguageUnavailable):
        chat.parse_reply(reply(text + '\nPlaceholder.'), r)
    with pytest.raises(chat.LanguageUnavailable):
        chat.parse_reply('{"answer":"Done!","edits":[]}', r)


def test_before_command_is_line_bound_and_idempotent(manuscript):
    message = r'Add a heading before \chapter{Methods} in main.tex.'
    heading = r'\section{Readers}'
    anchor = r'\chapter{Methods}'
    manuscript['files']['main.tex'] = anchor + '\nBody.'
    r = prepare(manuscript, message)
    assert chat.parse_reply(reply(heading), r)['edits'][0]['replacement'] == heading + '\n' + anchor
    manuscript['files']['main.tex'] = heading + '\n' + anchor + '\nBody.'
    result = chat.parse_reply(reply(heading), prepare(manuscript, message))
    assert result['edits'] == [] and not result['filtered'] and 'already' in result['answer']


def test_no_implicit_binding_to_ambiguous_hidden_or_cross_file_anchor(manuscript):
    message = r'Insert a heading after \end{titlepage} in main.tex.'
    anchor = r'\end{titlepage}'
    manuscript['files']['chapters/intro.tex'] = anchor
    for source in [anchor + '\n' + anchor, '% ' + anchor, r'\makethesistitle',
                   '\\begin{verbatim}\n' + anchor + '\n\\end{verbatim}']:
        manuscript['files']['main.tex'] = source
        with pytest.raises(ValueError, match='Select the intended line'):
            prepare(manuscript, message)
    manuscript['files']['main.tex'] = anchor
    assert not prepare(manuscript, message, mode='discuss')['insertion_point']
    with pytest.raises(ValueError, match='not in this chat'):
        prepare(manuscript, message, files=['chapters/intro.tex'])
    assert not prepare(manuscript, message, selection={'file': 'main.tex', 'text': anchor,
        'from': 0, 'to': len(anchor)})['insertion_point']
    assert not prepare(manuscript, 'Explain what happens after the title page.')['insertion_point']


def test_natural_heading_needs_only_title_and_keeps_source_comment(manuscript):
    anchor = r'\makethesistitle % keep this'
    manuscript['files']['main.tex'] = anchor + '\nAbstract'
    r = prepare(manuscript, 'Füge nach dem Titelblatt eine Section mit dem Titel Zahlen & Daten ein.')
    raw = json.dumps({'heading_title': 'Zahlen & Daten'})
    expected = anchor + '\n' + r'\section{Zahlen \& Daten}'
    assert chat.partial_edit(raw, r)['replacement'] == expected
    assert chat.parse_reply(raw, r)['edits'][0]['replacement'] == expected


def test_parenthesized_titlepage_command_does_not_become_part_of_the_edit(manuscript):
    anchor, heading = r'\end{titlepage}', r'\section{Hello World}'
    manuscript['files']['main.tex'] = anchor + '\nAbstract.'
    message = f'Füge den Befehl `{heading}` direkt nach dem Titelblatt (`{anchor}`) in main.tex ein.'
    r = prepare(manuscript, message)
    assert chat.reply_schema(r)['properties'] == {'insertion': {'type': 'string', 'enum': [heading]}}
    assert chat.parse_reply(json.dumps({'insertion': heading}), r)['edits'][0]['replacement'] == anchor + '\n' + heading


def test_conditional_edit_does_not_force_a_heading(manuscript):
    manuscript['files']['main.tex'] = r'\maketitle'
    r = prepare(manuscript, 'Add a section titled Results after the title page only if data are available.')
    assert not r['insertion_point']
    assert chat.parse_reply('{"answer":"No data found.","edits":[]}', r)['edits'] == []
