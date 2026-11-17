export function projectChatList(panel, {threads, current, choose, remove, blocked}) {
  const toggle = panel.querySelector('#projectChatListToggle'), list = panel.querySelector('#projectChatList');
  const element = (tag, text, cls) => { const node = panel.ownerDocument.createElement(tag); node.textContent = text; if (cls) node.className = cls; return node; };
  let editing = null, deleting = null;
  function button(label, title, action, cls = '') {
    const node = element('button', label, cls); node.type = 'button'; node.title = title;
    node.setAttribute('aria-label', title); node.onclick = action; return node;
  }
  function close() { list.hidden = true; toggle.setAttribute('aria-expanded', 'false'); editing = deleting = null; }
  function render() {
    toggle.textContent = current().name + ' ▾'; toggle.title = 'Chats';
    toggle.classList.toggle('has-running-chat', threads().some(thread => thread.busy));
    toggle.disabled = blocked();
    if (list.hidden || editing) return;
    list.replaceChildren();
    for (const thread of threads()) {
      const row = element('div', '', 'project-chat-list-row');
      row.dataset.chatId = thread.id;
      const name = button(thread.name, 'Open chat: ' + thread.name, () => { if (!blocked()) { choose(thread); close(); } }, 'project-chat-list-name');
      name.setAttribute('aria-current', String(thread === current()));
      name.disabled = blocked();
      const state = element('small', thread.busy ? 'Running' : thread.unread ? 'New reply' : '', 'project-chat-list-state');
      if (deleting === thread.id) {
        row.append(element('span', 'Delete this chat?', 'project-chat-list-name'),
          button('Delete', 'Delete chat permanently', () => { if (!blocked()) { deleting = null; remove(thread); render(); } }, 'project-chat-delete'),
          button('Cancel', 'Keep chat', () => { deleting = null; render(); }));
      } else {
        row.append(name, state,
          button('✎', 'Rename chat: ' + thread.name, () => rename(thread, row)),
          button('×', 'Delete chat: ' + thread.name, () => { deleting = thread.id; render(); }, 'project-chat-delete'));
      }
      list.append(row);
    }
  }
  function rename(thread, row) {
    if (blocked()) return;
    editing = thread.id; deleting = null;
    const input = element('input', '', 'project-chat-rename'); input.value = thread.name; input.maxLength = 80;
    input.setAttribute('aria-label', 'Chat name');
    const finish = commit => {
      if (commit && input.value.trim()) { thread.name = input.value.trim(); thread.renamed = true; }
      editing = null; render(); toggle.focus();
    };
    input.onkeydown = event => {
      if (event.isComposing) return;
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
    };
    row.replaceChildren(input, button('✓', 'Save chat name', () => finish(true)), button('×', 'Cancel rename', () => finish(false)));
    input.focus(); input.select();
  }
  toggle.onclick = () => {
    if (blocked()) return;
    if (!list.hidden) close();
    else { list.hidden = false; toggle.setAttribute('aria-expanded', 'true'); render(); }
  };
  panel.addEventListener('pointerdown', event => { if (!list.contains(event.target) && !toggle.contains(event.target)) close(); });
  panel.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Escape' && !list.hidden) { event.stopPropagation(); close(); toggle.focus(); }
  });
  return {render, close};
}
