function el(tag, text, cls) {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;
}

export function renderTemplates(parent, templates, onUse) {
  const search = el('input'); search.type = 'search'; search.placeholder = 'Find a journal or template…'; search.setAttribute('aria-label', 'Search templates'); search.className = 'template-search';
  const cards = el('div', undefined, 'template-gallery'); parent.append(search, cards);
  function render() {
    cards.replaceChildren();
    const q = search.value.toLowerCase();
    for (const item of templates.filter(t => [t.name, t.publisher, ...(t.journals || [])].join(' ').toLowerCase().includes(q))) {
      const card = el('article', undefined, 'template-card');
      const preview = el('div', undefined, 'template-preview');
      const img = el('img'); img.src = '/assets/writing_templates/' + encodeURIComponent(item.id) + '/preview.png'; img.alt = `${item.name} compiled first page`; img.loading = 'lazy'; preview.append(img);
      const info = el('div', undefined, 'template-info'); info.append(el('small', item.publisher, 'eyebrow'), el('h3', item.name), el('p', item.description), el('span', item.layout, 'template-layout'));
      const use = el('button', 'Use template', 'primary'); use.dataset.template = item.id;
      use.onclick = () => Promise.resolve(onUse(item)).catch(error => { const message = el('p', error.message, 'error'); info.append(message); });
      const details = el('details'); details.append(el('summary', 'Source & licence'), el('p', item.licence));
      const source = el('a', 'Publisher information ↗'); source.href = item.source; source.target = '_blank'; source.rel = 'noopener noreferrer'; details.append(source);
      info.append(use, details); card.append(preview, info); cards.append(card);
    }
    if (!cards.children.length) cards.append(el('p', 'No matching installed template.', 'muted'));
  }
  search.oninput = render; render();
}

export function openTemplateGallery(templates, onUse) {
  document.getElementById('templateDialog')?.remove();
  const dialog = el('dialog', undefined, 'template-dialog'); dialog.id = 'templateDialog';
  const header = el('div', undefined, 'panel-heading'); header.append(el('h2', 'Choose a template'));
  const close = el('button', 'Close'); close.onclick = () => dialog.close(); header.append(close);
  dialog.append(header, el('p', 'Opens a new manuscript with the class, styles and supporting files included. Your current manuscript stays unchanged.', 'muted'));
  const body = el('div', undefined, 'template-scroll'); dialog.append(body);
  renderTemplates(body, templates, async item => { dialog.close(); await onUse(item); });
  dialog.addEventListener('close', () => dialog.remove(), {once: true}); document.body.append(dialog); dialog.showModal();
}
