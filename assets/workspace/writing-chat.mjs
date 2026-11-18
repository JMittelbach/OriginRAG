import {streamWritingRequest} from './writing-language.mjs';
import {projectChatWindow} from './project-chat-window.mjs';
import {projectChatList} from './project-chat-list.mjs';
import {restoredChats} from './quill-session.mjs';

let activeChat = null;

export function setupProjectChat({api, save, getProject, getEditor, enabled, openModels, reveal, notify, session}) {
  activeChat?.destroy();
  document.getElementById('projectChatPanel')?.remove();
  const panel = document.createElement('aside');
  panel.id = 'projectChatPanel'; panel.className = 'project-chat'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Quill project chat');
  panel.innerHTML = [
    '<button type="button" id="projectChatClose" class="dialog-close" aria-label="Close project chat" title="Close">×</button>',
    '<div id="projectChatHandle" class="project-chat-heading" role="button" tabindex="0" aria-label="Move project chat" title="Drag to move"><div><button type="button" id="projectChatListToggle" aria-label="Chats" aria-expanded="false" aria-controls="projectChatList">Chat 1 ▾</button><small id="projectChatName"></small></div><div class="project-chat-tools"><button type="button" id="projectChatNew" aria-label="New chat" title="New chat">+</button><button type="button" id="projectChatWindow" aria-label="Open project chat in a separate window" title="Open on another screen">↗</button></div></div>',
    '<div id="projectChatList" class="project-chat-list" aria-label="Project chats" hidden></div>',
    '<div id="projectChatTabs" class="project-chat-tabs" role="group" aria-label="Open chats" hidden></div>',
    '<div id="projectChatSelectionContext" class="project-chat-selection" hidden><details><summary id="projectChatSelectionSummary"></summary><pre id="projectChatSelectionText"></pre></details><button type="button" id="projectChatRemoveSelection" aria-label="Remove selected passage" title="Remove selection">×</button></div>',
    '<div id="projectChatMessages" class="project-chat-messages" aria-live="polite"></div><p id="projectChatStatus" role="status"></p>',
    '<button type="button" id="projectChatReview" hidden>Review in editor</button>',
    '<form id="projectChatForm"><label class="sr-only" for="projectChatInput">Message Quill</label><textarea id="projectChatInput" maxlength="4000" rows="2" placeholder="Ask Quill…" spellcheck="true"></textarea><button type="submit" id="projectChatSend" aria-label="Send" title="Send">↑</button><button type="button" id="projectChatStop" aria-label="Stop" title="Stop" hidden>■</button></form>',
    '<button type="button" id="projectChatResize" class="project-chat-resize" aria-label="Resize project chat" title="Resize"></button><button type="button" id="projectChatResizeLeft" class="project-chat-resize project-chat-resize-left" aria-label="Resize project chat from left" title="Resize"></button>',
  ].join('');
  document.body.append(panel);
  const $ = id => panel.querySelector('#' + id);
  const element = (tag, text, cls) => { const node = document.createElement(tag); node.textContent = text; if (cls) node.className = cls; return node; };
  const configuration = {api, save, getProject, getEditor, enabled, openModels, reveal, notify, session};
  const sessionKey = 'chat:' + getProject().id;
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
      remember();
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
  function composer() {
    const input = $('projectChatInput');
    $('projectChatSend').disabled = !alive || active.busy || reviewBusy || !enabled || !input.value.trim();
    input.style.height = 'auto';
    input.style.height = Math.max(64, Math.min(140, input.scrollHeight)) + 'px';
  }
  function controls() {
    composer();
    $('projectChatSend').hidden = active.busy;
    $('projectChatStop').hidden = !active.busy;
    $('projectChatInput').disabled = !alive || !enabled;
    $('projectChatRemoveSelection').disabled = active.busy;
    $('projectChatNew').disabled = !alive || reviewBusy;
    $('projectChatReview').hidden = !active.review || active.review.pending || !active.review.edits.length;
    $('projectChatReview').textContent = active.review?.earlier ? 'Review earlier edit' : 'Review in editor';
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
    for (let i = thread.history.length - 1; i > 0 && recent.length < 8; i -= 2) {
      const user = thread.history[i - 1], assistant = thread.history[i];
      if (user.role !== 'user' || assistant.role !== 'assistant') break;
      const room = Math.min(4000, 12000 - length - user.content.length);
      if (room < 64 && assistant.content.length > room) break;
      const content = assistant.content.length <= room ? assistant.content
        : assistant.content.slice(0, room - 14).replace(/[\uD800-\uDBFF]$/, '') + '… [shortened]';
      recent.unshift(user, {role: 'assistant', content}); length += user.content.length + content.length;
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
    if (thread === active) $('projectChatReview').hidden = true;
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
        if (alive) { status(thread, text); controls(); }
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
      controls();
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
    thread.previousReview = thread.review && !thread.review.pending ? thread.review : null;
    clearReview(thread); thread.busy = true;
    const epoch = ++thread.generation, requestSelection = thread.selection ? {...thread.selection} : null;
    const focusFile = getEditor().selection().file;
    const conversation = recentHistory(thread), historyStart = thread.history.length;
    if (!thread.renamed && thread.history.length === 0) thread.name = question.replace(/\s+/g, ' ').slice(0, 48);
    message(thread, 'user', question);
    const reply = message(thread, 'assistant', 'Thinking…');
    let answerText = null;
    reply.item.setAttribute('aria-busy', 'true');
    const liveEdits = new Map();
    let previewUnavailable = false;
    const previewIfPossible = base => {
      if (previewUnavailable) return;
      try { stageReview(thread, [...liveEdits.values()], base, epoch, true); }
      catch { previewUnavailable = true; clearReview(thread); }
    };
    thread.draft = ''; thread.question = question; $('projectChatInput').value = ''; status(thread, ''); controls();
    const requestController = new AbortController(); thread.controller = requestController;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; requestController.abort(); }, 380000);
    const valid = () => alive && !thread.deleted && epoch === thread.generation;
    const restoreEarlierEdit = () => {
      const earlier = thread.previousReview;
      if (earlier && current(earlier.base) && getEditor() === earlier.editor) {
        thread.review = {...earlier, epoch, earlier: true};
        if (thread === active && !panel.hidden) {
          try { paintReview(thread.review); } catch { clearReview(thread); }
        }
      }
    };
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
        if (event.type !== 'answer_delta' && event.type !== 'edit_preview' && event.type !== 'edit_delta' && event.type !== 'retrying') return;
        const messages = $('projectChatMessages');
        const follow = thread === active && messages.scrollHeight - messages.clientHeight - messages.scrollTop < 48;
        if (event.type === 'retrying') {
          clearReview(thread); liveEdits.clear(); previewUnavailable = false; answerText = null;
          reply.body.textContent = 'Checking the edit against your text…';
        }
        if (event.type === 'answer_delta' && event.text) {
          if (!answerText) { answerText = reply.body.ownerDocument.createTextNode(''); reply.body.replaceChildren(answerText); }
          answerText.appendData(event.text);
        }
        if (event.type === 'edit_preview') {
          if (!Number.isInteger(event.from) || !Number.isInteger(event.to) || typeof event.original !== 'string') return;
          liveEdits.set(`${event.file}:${event.from}:${event.to}`, event);
          previewIfPossible(base);
        }
        if (event.type === 'edit_delta' && typeof event.text === 'string') {
          const key = `${event.file}:${event.from}:${event.to}`, previous = liveEdits.get(key);
          if (previous) {
            liveEdits.set(key, {...previous, replacement: previous.replacement + event.text});
            previewIfPossible(base);
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
      if (!result.edits.length) restoreEarlierEdit();
      thread.history.push({role: 'assistant', content: result.answer});
      status(thread, '');
    } catch (error) {
      if (!valid()) return;
      if (thread.review?.epoch === epoch) clearReview(thread); thread.history.splice(historyStart);
      if (error.name === 'AbortError') restoreEarlierEdit();
      if (answerText?.length) {
        const partial = element('details', '');
        partial.append(element('summary', 'Incomplete reply'), element('div', reply.body.textContent, 'project-chat-text'));
        reply.item.append(partial);
      }
      reply.body.textContent = '';
      reply.item.append(element('p', error.name === 'AbortError' ? (timedOut ? 'The model took too long. Try a shorter passage. Nothing was applied.' : 'Request stopped. No changes applied.') : error.message, 'project-chat-warning'));
      if (valid()) {
        if (!thread.draft) { thread.draft = question; if (thread === active) $('projectChatInput').value = question; }
        if (/choose an installed|open models|model is unavailable/i.test(error.message) && openModels) {
          const models = element('button', 'Models'); models.type = 'button';
          models.onclick = () => { window.focus(); openModels(); }; reply.item.append(models);
        }
      }
    } finally {
      clearTimeout(timeout); reply.item.removeAttribute('aria-busy');
      if (valid()) { thread.previousReview = null; thread.controller = null; thread.busy = false; thread.unread = thread !== active; controls(); }
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
  $('projectChatInput').oninput = () => { active.draft = $('projectChatInput').value; composer(); };
  $('projectChatInput').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!$('projectChatSend').disabled) $('projectChatForm').requestSubmit();
    }
  };
  $('projectChatStop').onclick = () => active.controller?.abort();
  $('projectChatReview').onclick = () => {
    const value = active.review;
    if (!value || value.pending) return;
    if (!current(value.base)) { clearReview(); status(active, 'The draft changed. Ask again for updated suggestions.'); return; }
    revealReview(value.edits[0], value.base); paintReview(value);
  };
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
    if (active.review && !active.review.pending) {
      if (current(active.review.base)) { revealReview(active.review.edits[0], active.review.base, true); paintReview(active.review); }
      else { clearReview(); status(active, 'The draft changed. Ask again for updated suggestions.'); }
    }
    controls(); $('projectChatInput').focus(); windowControl.focus();
  }, destroy() { windowControl.destroy(); }};
  function remember() {
    if (!session) return;
    const threads = [active, ...conversations.filter(t => t !== active).reverse()].slice(0, 20).map(thread => {
      const history = thread.history.slice(-24);
      if (thread.busy && history.at(-1)?.role === 'user') history.pop();
      const value = thread.review && !thread.review.pending ? thread.review : thread.previousReview;
      return {id: thread.id, name: thread.name, renamed: thread.renamed, history,
        draft: thread.draft || (thread.busy ? thread.question : ''), interrupted: thread.busy,
        selection: thread.selection, review: value ? {revision: value.base.revision, edits: value.edits,
          earlier: Boolean(value.earlier || thread.busy)} : null};
    });
    session.write(sessionKey, {open: !panel.hidden, active: active.id, threads});
  }
  const saved = restoredChats(session?.read(sessionKey));
  if (saved) {
    conversations.length = 0;
    for (const row of saved.threads.sort((a,b) => a.id - b.id)) {
      const t = createChat(); t.id = row.id; t.name = row.name; t.renamed = row.renamed;
      t.draft = row.draft || ''; t.history = row.history || [];
      for (const item of t.history) message(t, item.role, item.content);
      if (row.interrupted) t.status = 'Your previous request stopped when you left. Send it again to continue.';
      const base = getProject(), sel = row.selection;
      if (sel && typeof sel.file === 'string' && Object.hasOwn(base.files, sel.file) &&
          typeof sel.text === 'string' && Number.isInteger(sel.from) && Number.isInteger(sel.to) &&
          sel.from >= 0 && sel.to > sel.from && sel.text.length <= 8000 &&
          base.files[sel.file].slice(sel.from, sel.to) === sel.text) t.selection = sel;
      if (row.review && row.review.revision === base.revision) {
        try {
          const edits = normalizeEdits(row.review.edits, base, 0);
          if (edits.length) t.review = {thread:t, edits, base:{...base, files:{...base.files}}, epoch:0, pending:false,
            earlier: row.review.earlier === true, editor:getEditor()};
        } catch { /* A stale proposal is never applied. */ }
      } else if (row.review) t.status = 'The draft changed. Ask again for updated suggestions.';
    }
    serial = Math.max(...conversations.map(t => t.id));
    active = conversations.find(t => t.id === saved.active) || conversations[0];
    $('projectChatInput').value = active.draft; status(active, active.status); selectionDisplay();
    if (saved.open) instance.open();
  }
  $('projectChatMessages').append(active.log);
  activeChat = instance; controls();
  return instance;
}
