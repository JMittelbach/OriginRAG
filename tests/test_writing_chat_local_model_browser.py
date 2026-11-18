import os
import time

import pytest

from originrag import writing_chat
from test_writing import workspace_server as workspace_server
from test_writing_chat_browser import make_project, get_project
from test_writing_language_browser import select_line, ask_selection


@pytest.mark.skipif(not os.environ.get('ORIGINRAG_TEST_LOCAL_MODEL'), reason='Opt-in: requires an already installed local Ollama model')
def test_real_local_model_answers_two_separate_project_chats(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', os.environ['ORIGINRAG_TEST_LOCAL_MODEL'])
    monkeypatch.setenv('ORIGINRAG_OLLAMA_MODEL', os.environ['ORIGINRAG_TEST_LOCAL_MODEL'])
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1050})
        page.set_default_timeout(150000)
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        first = 'Nenne kurz die Dateinamen des Projekts. Keine Änderungen.'
        second = 'Welche LaTeX-Dokumentenklasse hat main.tex? Antworte kurz. Keine Änderungen.'
        page.locator('#projectChatInput').fill(first)
        page.locator('#projectChatSend').click()
        page.locator('#projectChatNew').click()
        page.locator('#projectChatInput').fill(second)
        page.locator('#projectChatSend').click()
        pw.expect(page.locator('#projectChatStop')).to_be_hidden(timeout=150000)
        second_reply = page.locator('.project-chat-message.assistant').inner_text()
        assert 'article' in second_reply.lower(), second_reply
        page.locator('#projectChatListToggle').click()
        page.get_by_role('button', name='Open chat: ' + first[:48], exact=True).click()
        pw.expect(page.locator('#projectChatStop')).to_be_hidden(timeout=150000)
        first_reply = page.locator('.project-chat-message.assistant').inner_text()
        assert 'main.tex' in first_reply and 'chapter.tex' in first_reply, first_reply
        assert page.locator('.project-chat-message').count() == 2
        assert page.locator('.cm-ai-review').count() == 0
        assert get_project(page, key)['files'] == files
        print(f'REAL_QWEN threads_first={first_reply!r} threads_second={second_reply!r}', flush=True)
        browser.close()


@pytest.mark.skipif(not os.environ.get('ORIGINRAG_TEST_LOCAL_MODEL'), reason='Opt-in: requires an already installed local Ollama model')
def test_real_local_model_selection_chat_streams_applies_and_answers(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    model = os.environ['ORIGINRAG_TEST_LOCAL_MODEL']
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', model)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_MODEL', model)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_URL', 'http://127.0.0.1:11434')
    raw_replies = []
    parse_reply = writing_chat.parse_reply
    def inspect_reply(raw, prepared):
        raw_replies.append(raw)
        return parse_reply(raw, prepared)
    monkeypatch.setattr(writing_chat, 'parse_reply', inspect_reply)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1050})
        page.set_default_timeout(150000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        key, _ = make_project(page, base)
        code = page.locator('#source .cm-content')
        original = '\\documentclass{article}\n\\begin{document}\nPrefix remains unchanged.\nWe recieve the samples and perform the analysys.\nSuffix remains unchanged.\n\\end{document}'
        code.fill(original)
        select_line(page, 'We recieve the samples')
        ask_selection(page)
        pw.expect(page.locator('#projectChatInput')).to_have_value('')
        assert page.locator('#projectChatPanel select, #projectChatOptions').count() == 0
        page.evaluate('''() => {
          window.chatFrames = [];
          const observer = new MutationObserver(() => {
            const panel = document.querySelector('#projectChatPanel');
            const text = panel.querySelector('.project-chat-message.assistant .project-chat-text')?.textContent || '';
            const preview = document.querySelector('.cm-ai-proposed')?.textContent || '';
            if (!panel.querySelector('#projectChatStop').hidden) window.chatFrames.push({answer:text, preview});
          });
          observer.observe(document.querySelector('#projectChatPanel'), {subtree:true, childList:true, characterData:true});
          observer.observe(document.querySelector('#source'), {subtree:true, childList:true, characterData:true});
        }''')
        page.locator('#projectChatInput').fill('Bitte korrigiere ausschließlich die beiden englischen Tippfehler recieve und analysys in der markierten Passage. Behalte den restlichen Wortlaut exakt bei.')
        start = time.monotonic()
        page.locator('#projectChatSend').click()
        page.wait_for_function("() => document.querySelector('.project-chat-message.assistant') && document.querySelector('#projectChatStop').hidden", timeout=150000)
        if not page.locator('.cm-ai-accept').count():
            print('REAL_QWEN rejected_reply=' + repr(raw_replies), flush=True)
        assert page.locator('.cm-ai-accept').count() == 1, (page.locator('.project-chat-message.assistant').inner_text(), raw_replies)
        pw.expect(page.locator('.cm-ai-accept')).to_be_enabled()
        elapsed = time.monotonic() - start
        reply = page.locator('.project-chat-message.assistant .project-chat-text').inner_text()
        frames = page.evaluate('window.chatFrames')
        assert len({x['answer'] for x in frames if x['answer'] and 'Saving your draft' not in x['answer']}) > 1
        assert any(x['preview'] for x in frames)
        assert get_project(page, key)['files']['main.tex'] == original
        print(f'REAL_QWEN correction_seconds={elapsed:.2f} live_frames={len(frames)} reply={reply}', flush=True)
        dialogs = []
        page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        page.locator('.cm-ai-accept').click()
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        pw.expect(page.locator('#savedState')).to_have_text('All changes saved')
        expected = original.replace('recieve', 'receive').replace('analysys', 'analysis')
        assert get_project(page, key)['files']['main.tex'] == expected
        page.locator('#projectChatInput').fill('Erkläre mir auf Deutsch, was die Zeile mit documentclass in diesem Projekt bewirkt. Bitte ändere keine Dateien.')
        start = time.monotonic()
        page.locator('#projectChatSend').click()
        pw.expect(page.locator('#projectChatStop')).to_be_hidden(timeout=150000)
        answer = page.locator('.project-chat-message.assistant .project-chat-text').last.inner_text()
        assert 'article' in answer.lower() and len(answer) > 40
        assert page.locator('.cm-ai-review').count() == 0
        assert get_project(page, key)['files']['main.tex'] == expected
        print(f'REAL_QWEN explanation_seconds={time.monotonic() - start:.2f} reply={answer}', flush=True)
        page.locator('#projectChatClose').click()
        select_line(page, 'We receive the samples')
        ask_selection(page)
        page.locator('#projectChatInput').fill('Formuliere die markierte Passage im englischen wissenschaftlichen Stil um, als abgeschlossene Tätigkeit in der Vergangenheit. Füge keine neuen Informationen hinzu.')
        page.locator('#projectChatSend').click()
        page.wait_for_function("() => document.querySelectorAll('.project-chat-message.assistant').length === 3 && document.querySelector('#projectChatStop').hidden", timeout=150000)
        suggestion = page.locator('.project-chat-message.assistant').last
        assert page.locator('.cm-ai-accept').count() == 1, suggestion.inner_text()
        revised = page.locator('.cm-ai-proposed').inner_text()
        assert 'sampl' in revised.lower() and 'analys' in revised.lower()
        assert revised != 'We receive the samples and perform the analysis.'
        assert get_project(page, key)['files']['main.tex'] == expected
        print(f'REAL_QWEN free_rewrite={revised}', flush=True)
        if capture := os.environ.get('QUILL_ASSISTANT_CAPTURE'):
            page.locator('#projectChatPanel').screenshot(path=capture + '-real-model.png')
            page.locator('#source').screenshot(path=capture + '-inline-editor.png')
        page.locator('#projectChatClose').click()
        page.get_by_role('button', name='Undo', exact=True).click()
        pw.expect(code.locator('.cm-line')).to_have_text(original.split('\n'))
        pw.expect(page.locator('.cm-ai-review')).to_have_count(0)
        assert not errors and not dialogs
        browser.close()


@pytest.mark.skipif(not os.environ.get('ORIGINRAG_TEST_LOCAL_MODEL'), reason='Opt-in: requires an already installed local Ollama model')
def test_real_model_reads_main_preamble_automatically_even_when_another_file_is_open(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    model = os.environ['ORIGINRAG_TEST_LOCAL_MODEL']
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', model)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_MODEL', model)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(150000)
        key, _ = make_project(page, base)
        preamble = '\\documentclass[11pt]{article}\n\\usepackage{geometry}\n\\usepackage{booktabs}\n\\begin{document}\n'
        source = preamble + '\n'.join(f'Body paragraph {number}. The manuscript continues.' for number in range(4000)) + '\n\\end{document}'
        page.locator('#source .cm-content').fill(source)
        page.locator('[data-file="chapter.tex"]').click()
        page.locator('#projectChat').click()
        assert page.locator('#projectChatOptions, #projectChatPanel select').count() == 0
        page.locator('#projectChatInput').fill('Was steht in der Präambel vonmain.tex? Nenne die Dokumentenklasse und die geladenen Pakete. Keine Dateien ändern.')
        start = time.monotonic()
        page.locator('#projectChatSend').click()
        page.wait_for_function("() => document.querySelector('.project-chat-message.assistant') && document.querySelector('#projectChatStop').hidden", timeout=150000)
        reply = page.locator('.project-chat-message.assistant').inner_text()
        print(f'REAL_QWEN automatic_preamble_seconds={time.monotonic() - start:.2f} reply={reply}', flush=True)
        assert all(word in reply.lower() for word in ['article', 'geometry', 'booktabs'])
        assert page.locator('.cm-ai-review').count() == 0
        assert get_project(page, key)['files']['main.tex'] == source
        if capture := os.environ.get('QUILL_ASSISTANT_CAPTURE'):
            page.locator('#projectChatPanel').screenshot(path=capture + '-preamble.png')
        page.locator('#projectChatClose').click()
        browser.close()
@pytest.mark.skipif(not os.environ.get('ORIGINRAG_TEST_LOCAL_MODEL'), reason='Opt-in: requires local Ollama')
def test_real_model_proposes_an_unselected_edit_until_rejected(workspace_server, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    monkeypatch.setenv('ORIGINWRITE_LANGUAGE_CHECK', '1')
    model = os.environ['ORIGINRAG_TEST_LOCAL_MODEL']
    monkeypatch.setenv('ORIGINRAG_OLLAMA_ANSWER_MODEL', model)
    monkeypatch.setenv('ORIGINRAG_OLLAMA_MODEL', model)
    with pw.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1050})
        key, files = make_project(page, base)
        page.locator('#projectChat').click()
        page.locator('#projectChatInput').fill('Ersetze in chapter.tex bitte "A second sentence." durch "The second chapter is ready."')
        page.locator('#projectChatSend').click()
        page.wait_for_function("() => document.querySelector('.project-chat-message.assistant') && document.querySelector('#projectChatStop').hidden", timeout=150000)
        assert page.locator('#projectChatReview').is_visible(), page.locator('.project-chat-message.assistant').inner_text()
        page.locator('#projectChatReview').click()
        pw.expect(page.locator('.cm-ai-proposed')).to_have_text('The second chapter is ready.')
        assert get_project(page, key)['files'] == files
        page.locator('.cm-ai-reject').click()
        pw.expect(page.locator('#projectChatReview')).to_be_hidden()
        assert get_project(page, key)['files'] == files
        browser.close()
