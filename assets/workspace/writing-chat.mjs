import {streamWritingRequest} from './writing-language.mjs';
import {projectChatWindow} from './project-chat-window.mjs';
import {projectChatList} from './project-chat-list.mjs';

let activeChat = null;

export function setupProjectChat({api, save, getProject, getEditor, enabled, openModels, reveal, notify}) {
  activeChat?.destroy();
  document.getElementById('projectChatPanel')?.remove();
  const panel = document.createElement('aside');
  panel.id = 'projectChatPanel'; panel.className = 'project-chat'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Quill project chat');
  panel.innerHTML = [
    '<button type="button" id="projectChatClose" class="dialog-close" aria-label="Close project chat" title="Close">×</button>',
    '<div id="projectChatHandle" class="project-chat-heading" role="button" tabindex="0" aria-label="Move project chat" title="Drag to move"><div><button type="button" id="projectChatListToggle" aria-label="Chats" aria-expanded="false" aria-controls="projectChatList">Chat 1 ▾</button><small id="projectChatName"></small></div><div class="project-chat-tools"><button type="button" id="projectChatNew" aria-label="New chat" title="New chat">+</button><button type="button" id="projectChatWindow" aria-label="Open project chat in a separate window" title="Open on another screen">↗</button></div></div>',
    '<div id="projectChatList" class="project-chat-list" aria-label="Project chats" hidden></div>',
    '<div id="projectChatSelectionContext" class="project-chat-selection" hidden><details><summary id="projectChatSelectionSummary"></summary><pre id="projectChatSelectionText"></pre></details><button type="button" id="projectChatRemoveSelection" aria-label="Remove selected passage" title="Remove selection">×</button></div>',
    '<div id="projectChatMessages" class="project-chat-messages" aria-live="polite"></div><p id="projectChatStatus" role="status"></p>',
    '<form id="projectChatForm"><label class="sr-only" for="projectChatInput">Message Quill</label><textarea id="projectChatInput" maxlength="4000" rows="2" placeholder="Ask Quill…" spellcheck="true"></textarea><button type="submit" id="projectChatSend" aria-label="Send" title="Send">↑</button><button type="button" id="projectChatStop" aria-label="Stop" title="Stop" hidden>■</button></form>',
    '<button type="button" id="projectChatResize" class="project-chat-resize" aria-label="Resize project chat" title="Resize"></button><button type="button" id="projectChatResizeLeft" class="project-chat-resize project-chat-resize-left" aria-label="Resize project chat from left" title="Resize"></button>',
  ].join('');
  document.body.append(panel);
  const $ = id => panel.querySelector('#' + id);
  const element = (tag, text, cls) => { const node = document.createElement(tag); node.textContent = text; if (cls) node.className = cls; return node; };
  const configuration = {api, save, getProject, getEditor, enabled, openModels, reveal, notify};
  let alive = true, reviewBusy = false, shownReview = null, serial = 0;
  const conversations = [];
  function createChat() {
    const thread = {id: ++serial, name: 'Chat ' + serial, renamed: false, history: [], busy: false, controller: null,
      generation: 0, selection: null, review: null, draft: '', status: '', unread: false, deleted: false,
      log: element('div', '', 'project-chat-transcript'), scroll: 0};
    conversations.push(thread); return thread;
  }
  let active = createChat();
  const list = projectChatList(panel, {threads: () => conversations, current: () => active,
    choose: chooseChat, remove: deleteChat, blocked: () => !alive || reviewBusy});
  const windowControl = projectChatWindow(panel, {name: () => getProject().name,
    status: text => status(active, text),
    disconnected: () => {
      alive = false; shownReview?.editor.clearAssistantReview?.(); shownReview = null;
      for (const thread of conversations) {
        thread.deleted = true; thread.generation++; thread.controller?.abort();
        thread.history = []; thread.review = null; thread.selection = null; thread.log.replaceChildren();
      }
    },
  });
  function status(thread, text) {
    thread.status = text;
    if (alive && thread === active) $('projectChatStatus').textContent = text;
  }
  function controls() {
    $('projectChatSend').disabled = !alive || active.busy || reviewBusy || !enabled || !$('projectChatInput').value.trim();
    $('projectChatSend').hidden = active.busy;
    $('projectChatStop').hidden = !active.busy;
    $('projectChatInput').disabled = !alive || !enabled;
    $('projectChatRemoveSelection').disabled = active.busy;
    $('projectChatNew').disabled = !alive || reviewBusy;
    const input = $('projectChatInput'); input.style.height = 'auto';
    input.style.height = Math.max(64, Math.min(140, input.scrollHeight)) + 'px';
    list.render();
  }
  const current = base => {
    const project = getProject();
    return project.id === base.id && project.revision === base.revision && project.main === base.main
      && Object.keys(project.files).length === Object.keys(base.files).length
      && Object.entries(base.files).every(([name, text]) => project.files[name] === text);
  };
  function recentHistory(thread) {
    const recent = []; let length = 0;
    for (const item of [...thread.history].reverse()) {
      if (recent.length === 8 || item.content.length > 4000 || length + item.content.length > 12000) break;
      recent.unshift(item); length += item.content.length;
    }
    return recent;
  }
  function message(thread, role, text) {
    const item = element('article', '', 'project-chat-message ' + role);
    item.append(element('strong', role === 'user' ? 'You' : 'Quill'));
    const body = element('div', text, 'project-chat-text'); item.append(body);
    thread.log.append(item); if (thread === active) item.scrollIntoView({block: 'nearest'});
    return {item, body};
  }
  function clearReview(thread = active) {
    if (shownReview === thread.review) { thread.review?.editor.clearAssistantReview?.(); shownReview = null; }
    thread.review = null;
  }
  function newChat() {
    if (!alive || reviewBusy) return;
    chooseChat(createChat()); list.close();
  }
  function chooseChat(thread) {
    if (!alive || reviewBusy || thread.deleted) return;
    active.draft = $('projectChatInput').value; active.scroll = $('projectChatMessages').scrollTop;
    if (shownReview) { shownReview.editor.clearAssistantReview?.(); shownReview = null; }
    active = thread; thread.unread = false;
    $('projectChatMessages').replaceChildren(thread.log); $('projectChatMessages').scrollTop = thread.scroll;
    $('projectChatInput').value = thread.draft; $('projectChatStatus').textContent = thread.status;
    selectionDisplay(); controls();
    if (thread.review) {
      if (!current(thread.review.base)) { clearReview(thread); status(thread, 'The draft changed. Ask again for updated suggestions.'); }
      else {
        try { revealReview(thread.review.edits[0], thread.review.base, true); paintReview(thread.review); }
        catch (error) { clearReview(thread); status(thread, error.message); }
      }
    }
    $('projectChatInput').focus(); windowControl.focus();
  }
  function deleteChat(thread) {
    if (!alive || reviewBusy || thread.deleted) return;
    thread.deleted = true; thread.generation++; thread.controller?.abort(); clearReview(thread);
    const index = conversations.indexOf(thread); conversations.splice(index, 1);
    thread.history = []; thread.selection = null; thread.log.replaceChildren();
    if (thread === active) chooseChat(conversations[Math.min(index, conversations.length - 1)] || createChat());
    controls();
  }
  function normalizeEdits(edits, base, epoch) {
    if (!Array.isArray(edits) || edits.length > 4) throw new Error('The model returned unusable changes.');
    const result = [];
    for (const edit of edits) {
      const source = base.files[edit.file];
      if (typeof source !== 'string' || !edit.original || typeof edit.original !== 'string' || typeof edit.replacement !== 'string') throw new Error('The proposed changes do not belong to this request.');
      const anchored = Number.isInteger(edit.from) && Number.isInteger(edit.to);
      const from = anchored ? edit.from : source.indexOf(edit.original), to = anchored ? edit.to : from + edit.original.length;
      if (from < 0 || to <= from || to > source.length || source.slice(from, to) !== edit.original
          || !anchored && source.indexOf(edit.original, from + 1) !== -1
          || result.some(other => other.file === edit.file && from < other.to && to > other.from)) throw new Error('The suggested changes did not match your draft.');
      result.push({...edit, from, to, id: `${epoch}:${edit.file}:${from}:${to}`});
    }
    return result;
  }
  function revealReview(item, base, preserveFocus = false) {
    const focus = panel.ownerDocument.activeElement === $('projectChatInput');
    const line = base.files[item.file].slice(0, Math.max(item.from, item.to - 1)).split('\n').length;
    reveal(item.file, line);
    if (preserveFocus && focus) { $('projectChatInput').focus(); windowControl.focus(); }
  }
  function paintReview(value) {
    const thread = value.thread;
    if (!alive || thread !== active || thread.review !== value || thread.deleted) return;
    shownReview = value;
    const options = {pending: value.pending, busy: reviewBusy,
      accept: id => decide(value, id, true),
      reject: id => { if (value.pending) { thread.controller?.abort(); clearReview(thread); } else decide(value, id, false); },
      navigate: id => {
        if (thread.review !== value || thread !== active || reviewBusy) return;
        const next = value.edits[(value.edits.findIndex(item => item.id === id) + 1) % value.edits.length];
        revealReview(next, value.base);
      },
      invalidated: text => {
        if (thread.review !== value) return;
        thread.review = null; shownReview = null; if (value.pending) thread.controller?.abort();
        if (alive) status(thread, text);
      }};
    if (!value.editor.showAssistantReview(value.edits, value.base.files, options)) {
      clearReview(thread); throw new Error('The draft changed. Ask again using the latest version.');
    }
  }
  function stageReview(thread, edits, base, epoch, pending) {
    const normalized = normalizeEdits(edits, base, epoch);
    if (!normalized.length) { clearReview(thread); return; }
    if (!current(base)) throw new Error('The manuscript changed. Ask again using the latest draft.');
    if (thread === active && getEditor().isComposing()) throw new Error('Text input is in progress. Finish typing before asking again.');
    if (typeof getEditor().showAssistantReview !== 'function') throw new Error('Reload the Quill page to enable inline suggestions.');
    const previous = thread.review;
    if (previous?.pending && pending && previous.epoch === epoch) previous.edits = normalized;
    else {
      clearReview(thread); thread.review = {thread, edits: normalized, base, epoch, pending, editor: getEditor()};
    }
    if (thread === active && !panel.hidden && (!previous || !pending && previous.pending)) revealReview(normalized[0], base, true);
    paintReview(thread.review);
  }
  async function decide(value, id, accept) {
    const thread = value.thread;
    if (thread !== active || thread.review !== value || value.pending || reviewBusy || value.epoch !== thread.generation) return;
    const edit = value.edits.find(item => item.id === id);
    if (!edit) return;
    if (!accept) {
      value.edits = value.edits.filter(item => item !== edit);
      if (!value.edits.length) { clearReview(thread); value.editor.focus(); }
      else { revealReview(value.edits[0], value.base); paintReview(value); }
      return;
    }
    reviewBusy = true; controls(); paintReview(value);
    let applied = false;
    try {
      if (!current(value.base)) throw new Error('The manuscript changed. Ask again before accepting these suggestions.');
      const remote = await api('/api/writing?revision=' + encodeURIComponent(value.base.id));
      if (!alive || thread.review !== value || value.epoch !== thread.generation || getEditor() !== value.editor) return;
      if (remote.revision !== value.base.revision || !current(value.base)) throw new Error('The saved manuscript changed. Nothing was replaced; ask again using the latest version.');
      if (!value.editor.applyProjectEdits([edit], value.base.files)) throw new Error('The change could not be applied safely. Your draft has been kept.');
      applied = true;
      const shift = edit.replacement.length - edit.original.length;
      const remaining = value.edits.filter(item => item !== edit).map(item => item.file === edit.file && item.from >= edit.to
        ? {...item, from: item.from + shift, to: item.to + shift} : item);
      setSelection(null); await save();
      if (!alive || thread.review !== value) return;
      const project = getProject();
      if (project.id !== value.base.id) { clearReview(thread); return; }
      value.base = {...project, files: {...project.files}};
      if (remaining.some(item => value.base.files[item.file]?.slice(item.from, item.to) !== item.original)) throw new Error('The draft changed while saving. Remaining suggestions were cleared.');
      value.edits = remaining;
      if (!remaining.length) { clearReview(thread); value.editor.focus(); }
      else revealReview(remaining[0], value.base);
      status(thread, '');
    } catch (error) {
      clearReview(thread);
      const text = applied ? 'The change is in your draft, but saving or remaining suggestions need attention.' : error.message;
      status(thread, text); notify?.(text, true);
    } finally { reviewBusy = false; if (thread.review === value) paintReview(value); controls(); }
  }
  $('projectChatForm').onsubmit = async event => {
    event.preventDefault();
    if (!alive || active.busy || !enabled || $('projectChatSend').disabled) return;
    const question = $('projectChatInput').value.trim();
    if (!question) return;
    const thread = active;
    clearReview(thread); thread.busy = true;
    const epoch = ++thread.generation, requestSelection = thread.selection ? {...thread.selection} : null;
    const focusFile = getEditor().selection().file;
    const conversation = recentHistory(thread), historyStart = thread.history.length;
    if (!thread.renamed && thread.history.length === 0) thread.name = question.replace(/\s+/g, ' ').slice(0, 48);
    message(thread, 'user', question);
    const reply = message(thread, 'assistant', 'Thinking…');
    let answerStarted = false;
    reply.item.setAttribute('aria-busy', 'true');
    const liveEdits = new Map();
    thread.draft = ''; $('projectChatInput').value = ''; status(thread, ''); controls();
    const requestController = new AbortController(); thread.controller = requestController;
    const timeout = setTimeout(() => requestController.abort(), 135000);
    const valid = () => alive && !thread.deleted && epoch === thread.generation;
    try {
      await save();
      if (!valid()) return;
      if (requestController.signal.aborted) throw new DOMException('Request stopped.', 'AbortError');
      const project = getProject(), base = {...project, files: {...project.files}};
      if (requestSelection && base.files[requestSelection.file]?.slice(requestSelection.from, requestSelection.to) !== requestSelection.text) throw new Error('The selected text changed. Select it again before sending.');
      thread.history.push({role: 'user', content: question});
      const result = await streamWritingRequest({action: 'project_chat', id: base.id, revision: base.revision,
        message: question, history: conversation, auto_context: true, focus_file: focusFile,
        mode: 'edit', selection: requestSelection}, requestController.signal, event => {
        if (!valid() || requestController.signal.aborted) return;
        const messages = $('projectChatMessages');
        const follow = thread === active && messages.scrollHeight - messages.clientHeight - messages.scrollTop < 48;
        if (event.type === 'answer_delta' && event.text) {
          if (!answerStarted) { reply.body.textContent = ''; answerStarted = true; }
          reply.body.textContent += event.text;
        }
        if (event.type === 'edit_preview') {
          if (!Number.isInteger(event.from) || !Number.isInteger(event.to) || typeof event.original !== 'string') return;
          liveEdits.set(`${event.file}:${event.from}:${event.to}`, event);
          stageReview(thread, [...liveEdits.values()], base, epoch, true);
        }
        if (event.type === 'edit_delta' && typeof event.text === 'string') {
          const key = `${event.file}:${event.from}:${event.to}`, previous = liveEdits.get(key);
          if (previous) {
            liveEdits.set(key, {...previous, replacement: previous.replacement + event.text});
            stageReview(thread, [...liveEdits.values()], base, epoch, true);
          }
        }
        if (follow) messages.scrollTop = messages.scrollHeight;
      }, 2000000);
      if (requestController.signal.aborted) throw new DOMException('Request stopped.', 'AbortError');
      if (!valid()) return;
      if (result.revision !== base.revision || !current(base)) throw new Error('The manuscript changed during the reply. Ask again using the latest draft.');
      if (typeof result.answer !== 'string' || !result.answer.trim()) throw new Error('No usable answer was returned.');
      reply.body.textContent = result.answer;
      if (!Array.isArray(result.files) || !Array.isArray(result.edits) || result.edits.some(edit => !result.files.includes(edit.file))) throw new Error('The model returned unusable changes.');
      stageReview(thread, result.edits, base, epoch, false);
      if (result.filtered) reply.item.append(element('p', 'The proposed changes did not match your draft and were discarded.', 'project-chat-warning'));
      thread.history.push({role: 'assistant', content: result.answer});
      status(thread, '');
    } catch (error) {
      if (!valid()) return;
      if (thread.review?.epoch === epoch) clearReview(thread); thread.history.splice(historyStart);
      if (!answerStarted) reply.body.textContent = '';
      reply.item.append(element('p', error.name === 'AbortError' ? 'Request stopped. No changes applied.' : error.message, 'project-chat-warning'));
      if (valid()) {
        const retry = element('button', 'Retry'); retry.type = 'button';
        retry.onclick = () => {
          if (thread !== active || thread.deleted || thread.busy) return;
          thread.draft = question; $('projectChatInput').value = question; setSelection(requestSelection); $('projectChatInput').focus(); controls();
        };
        reply.item.append(retry);
        if (/model/i.test(error.message) && openModels) {
          const models = element('button', 'Models'); models.type = 'button';
          models.onclick = () => { window.focus(); openModels(); }; reply.item.append(models);
        }
      }
    } finally {
      clearTimeout(timeout); reply.item.removeAttribute('aria-busy');
      if (valid()) { thread.controller = null; thread.busy = false; thread.unread = thread !== active; controls(); }
    }
  };
  function setSelection(selected) {
    if (active.busy) return;
    if (selected && (!selected.text?.trim() || selected.text.length > 8000)) {
      status(active, 'Select a shorter passage first.'); return;
    }
    active.selection = selected ? {...selected} : null;
    selectionDisplay(); controls();
  }
  function selectionDisplay() {
    const selection = active.selection;
    $('projectChatSelectionContext').hidden = !selection;
    $('projectChatSelectionSummary').textContent = selection ? 'Selection · ' + selection.file : '';
    $('projectChatSelectionText').textContent = selection?.text || '';
  }
  $('projectChatInput').oninput = () => { active.draft = $('projectChatInput').value; controls(); };
  $('projectChatInput').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!$('projectChatSend').disabled) $('projectChatForm').requestSubmit();
    }
  };
  $('projectChatStop').onclick = () => active.controller?.abort();
  $('projectChatNew').onclick = newChat;
  $('projectChatRemoveSelection').onclick = () => setSelection(null);
  $('projectChatClose').onclick = () => {
    const restoreFocus = panel.contains(panel.ownerDocument.activeElement);
    list.close(); if (windowControl.detached()) windowControl.dock(false);
    panel.hidden = true; document.getElementById('projectChat')?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) document.getElementById('projectChat')?.focus();
  };
  panel.addEventListener('keydown', event => { if (event.key === 'Escape' && !event.isComposing && !event.defaultPrevented && !event.cancelBubble) $('projectChatClose').click(); });
  const instance = {async open(options = {}) {
    if (!alive) {
      const replacement = setupProjectChat(configuration);
      const trigger = document.getElementById('projectChat');
      if (trigger) trigger.onclick = replacement.open;
      return replacement.open(options);
    }
    panel.hidden = false; document.getElementById('projectChat')?.setAttribute('aria-expanded', 'true');
    $('projectChatName').textContent = getProject().name; $('projectChatName').title = getProject().name;
    if (!enabled) status(active, 'The assistant is disabled on this host.');
    if (options.selection?.text?.trim()) setSelection(options.selection);
    controls(); $('projectChatInput').focus(); windowControl.focus();
  }, destroy() { windowControl.destroy(); }};
  $('projectChatMessages').append(active.log);
  activeChat = instance; controls();
  return instance;
}
