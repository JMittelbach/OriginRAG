import {addDialogClose} from './dialog-close.mjs';

function el(tag, text, cls) {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;
}

export function renderTemplates(parent, templates, onUse) {
  const search = el('input'); search.type = 'search'; search.placeholder = 'Find a journal or template…'; search.setAttribute('aria-label', 'Search templates'); search.className = 'template-search';
  const publisher = el('select'); publisher.setAttribute('aria-label', 'Filter templates by publisher');
  for (const name of ['', ...new Set(templates.map(item => item.publisher))]) {
    const option = el('option', name || 'All publishers'); option.value = name; publisher.append(option);
  }
  const clear = el('button', 'Clear'); clear.type = 'button'; clear.hidden = true;
  const toolbar = el('div', undefined, 'template-filters'); toolbar.append(search, publisher, clear);
  const count = el('p', undefined, 'template-count muted'); count.setAttribute('role', 'status');
  const cards = el('div', undefined, 'template-gallery'); parent.append(toolbar, count, cards);
  const entries = templates.map(item => {
      const card = el('article', undefined, 'template-card');
      const preview = el('div', undefined, 'template-preview');
      const img = el('img'); img.src = '/assets/writing_templates/' + encodeURIComponent(item.id) + '/preview.png'; img.alt = `${item.name} compiled first page`; img.loading = 'lazy'; img.decoding = 'async'; preview.append(img);
      img.onerror = () => preview.replaceChildren(el('p', 'Manuscript preview unavailable', 'muted'));
      const info = el('div', undefined, 'template-info'); info.append(el('small', item.publisher, 'eyebrow'), el('h3', item.name), el('p', item.description), el('span', item.layout, 'template-layout'));
      const use = el('button', 'Use template', 'primary'); use.dataset.template = item.id;
      const message = el('p', undefined, 'error'); message.setAttribute('role', 'alert'); message.hidden = true;
      use.onclick = async () => {
        use.disabled = true; message.hidden = true;
        try { await onUse(item); }
        catch (error) { message.textContent = error.message; message.hidden = false; }
        finally { use.disabled = false; }
      };
      const details = el('details'); details.append(el('summary', 'Source & licence'), el('p', item.licence));
      const source = el('a', 'Publisher information ↗'); source.href = item.source; source.target = '_blank'; source.rel = 'noopener noreferrer'; details.append(source);
      info.append(use, message, details); card.append(preview, info);
      return {item, card, text: [item.name, item.publisher, ...(item.journals || [])].join(' ').toLowerCase()};
  });
  function render() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = entries.filter(entry => words.every(word => entry.text.includes(word)) && (!publisher.value || publisher.value === entry.item.publisher));
    cards.replaceChildren(...matches.map(entry => entry.card));
    count.textContent = `${matches.length} of ${templates.length} templates · ready to use`;
    clear.hidden = !words.length && !publisher.value;
    if (!matches.length) cards.append(el('p', 'No matching template. Try another journal or clear the filters.', 'muted'));
  }
  search.oninput = render; publisher.onchange = render;
  clear.onclick = () => { search.value = ''; publisher.value = ''; render(); search.focus(); };
  render();
}

export function openTemplateGallery(templates, onUse) {
  document.getElementById('templateDialog')?.remove();
  const dialog = el('dialog', undefined, 'template-dialog'); dialog.id = 'templateDialog';
  const header = el('div', undefined, 'panel-heading'); header.append(el('h2', 'Choose a template'));
  addDialogClose(dialog, 'Close templates');
  dialog.append(header, el('p', 'Opens a new manuscript with the class, styles and supporting files included. Your current manuscript stays unchanged.', 'muted'));
  const body = el('div', undefined, 'template-scroll'); dialog.append(body);
  renderTemplates(body, templates, async item => { dialog.close(); await onUse(item); });
  dialog.addEventListener('close', () => dialog.remove(), {once: true}); document.body.append(dialog); dialog.showModal();
}
