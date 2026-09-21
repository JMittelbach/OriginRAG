function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function actionButton(label, handler) {
  const button = element('button', label);
  button.type = 'button';
  button.addEventListener('click', handler);
  return button;
}

export function readingNavigation(container, finished) {
  const navigation = element('nav', undefined, 'reading-tabs');
  navigation.setAttribute('aria-label', 'Bookshelf views');
  const shelf = element('a', 'On your shelf'), read = element('a', 'Finished');
  shelf.href = '/bookshelf'; read.href = '/bookshelf?view=read';
  (finished ? read : shelf).setAttribute('aria-current', 'page');
  const count = element('span', '', 'reading-tab-count');
  count.hidden = true; read.append(count); navigation.append(shelf, read); container.append(navigation);
  return total => {
    if (!Number.isSafeInteger(total) || total < 0) return;
    count.textContent = String(total); count.hidden = false;
  };
}

export async function renderReadingHistory(container, {api, notice, updateCount}) {
  const section = element('section', undefined, 'reading-history');
  section.id = 'readingHistory';
  const heading = element('div', undefined, 'reading-history-heading');
  const title = element('h2', 'Your reading journey');
  title.id = 'readingHistoryTitle'; section.setAttribute('aria-labelledby', title.id);
  const count = element('p', '', 'reading-history-count'); count.id = 'readingHistoryCount';
  heading.append(title, count);
  const description = element('p', 'Papers you have finished, ready to revisit whenever you need them.', 'muted');
  const form = element('form', undefined, 'search reading-history-search'); form.setAttribute('role', 'search');
  const query = document.createElement('input'); query.id = 'readingHistoryQuery'; query.type = 'search'; query.maxLength = 200;
  query.placeholder = 'Search your finished papers…'; query.setAttribute('aria-label', 'Search finished papers');
  const searchButton = element('button', 'Search'); searchButton.type = 'submit'; form.append(query, searchButton);
  const feedback = element('div', undefined, 'reading-history-feedback'); feedback.id = 'readingHistoryFeedback'; feedback.hidden = true; feedback.setAttribute('role', 'status');
  const actionStatus = element('div', undefined, 'reading-history-feedback'); actionStatus.id = 'readingHistoryActionStatus'; actionStatus.hidden = true; actionStatus.setAttribute('role', 'status');
  const results = element('p', '', 'muted reading-history-results'); results.id = 'readingHistoryResults'; results.setAttribute('role', 'status');
  const list = element('div', undefined, 'reading-history-list'); list.id = 'readingHistoryList';
  const paging = element('div', undefined, 'actions reading-history-paging');
  const previous = actionButton('Previous', () => { offset = Math.max(0, offset - limit); load(); }); previous.id = 'readingHistoryPrevious';
  const next = actionButton('Next', () => { offset += limit; load(); }); next.id = 'readingHistoryNext';
  const position = element('span', '', 'muted'); position.id = 'readingHistoryPage';
  paging.append(previous, position, next);
  section.append(heading, description, form, results, feedback, actionStatus, list, paging); container.append(section);
  const params = new URLSearchParams(location.search), limit = 24;
  let offset = Number(params.get('offset')) || 0, total = 0, sequence = 0, loading = false, writing = false, pending = null, request, debounce;
  offset = Number.isSafeInteger(offset) && offset >= 0 ? offset : 0;
  query.value = (params.get('query') || '').slice(0, query.maxLength);

  function controls() {
    previous.disabled = loading || writing || offset === 0;
    next.disabled = loading || writing || offset + limit >= total;
    for (const button of list.querySelectorAll('button')) button.disabled = loading || writing || Boolean(pending);
  }

  function announceCount(value) {
    updateCount(value);
    count.textContent = `${value} ${value === 1 ? 'paper' : 'papers'} finished`;
  }

  function updateURL() {
    const parameters = new URLSearchParams({view: 'read'});
    if (query.value.trim()) parameters.set('query', query.value.trim());
    if (offset) parameters.set('offset', String(offset));
    history.replaceState(null, '', '/bookshelf?' + parameters);
  }

  function actionMessage(message, check = false) {
    actionStatus.replaceChildren(element('p', message)); actionStatus.hidden = false;
    if (check) actionStatus.append(actionButton('Check status', checkPending));
  }

  const confirmed = (state, action) => action === 'mark_unread' ? state.is_read === false : state.on_shelf === true;

  function completion(action) {
    const message = action === 'mark_unread' ? 'Marked as unread. Your PDF and personal notes were kept.' : 'Saved to your bookshelf.';
    notice(message); actionStatus.hidden = true;
  }

  async function checkPending() {
    if (!pending || writing) return;
    writing = true; controls();
    const check = actionStatus.querySelector('button'); if (check) check.disabled = true;
    try {
      const state = await api('/api/reading?' + new URLSearchParams({paper_id: pending.paper.paper_id}));
      if (confirmed(state, pending.action)) completion(pending.action);
      else actionMessage('The current status is unchanged. You can try the action again when you are ready.');
      pending = null;
    } catch (error) {
      actionMessage(error.message + ' Check status before trying the change again.', true);
    } finally {
      writing = false; controls(); await load();
    }
  }

  async function mutate(paper, action) {
    if (writing || pending || loading) return;
    if (action === 'mark_unread' && !confirm(`Mark “${paper.title || paper.paper_id}” as unread? It will leave your finished list. Your PDF and personal notes will stay.`)) return;
    writing = true; sequence++; request?.abort(); controls();
    actionStatus.hidden = true;
    try {
      await api(action === 'mark_unread' ? '/api/reading' : '/api/shelf', {
        action: action === 'mark_unread' ? 'mark_unread' : 'add', paper_id: paper.paper_id,
      });
      completion(action);
    } catch (error) {
      if (error.retryable || error.status >= 500) {
        pending = {paper, action};
        try {
          const state = await api('/api/reading?' + new URLSearchParams({paper_id: paper.paper_id}));
          if (confirmed(state, action)) completion(action);
          else actionMessage('The current status is unchanged. You can try the action again when you are ready.');
          pending = null;
        } catch {
          actionMessage('The server has not confirmed this change. Check status before trying it again.', true);
        }
      } else actionMessage(error.message);
    } finally {
      writing = false; controls(); await load();
    }
  }

  function render(items) {
    list.replaceChildren();
    if (!items.length) {
      const empty = element('div', undefined, 'reading-history-empty');
      empty.append(element('h3', query.value.trim() ? 'No matching finished papers' : 'A place for the papers you have finished'));
      empty.append(element('p', query.value.trim() ? 'Try another title or author, or clear your search.' : 'When you finish a paper, mark it as read in the Reader. It will appear here.', 'muted'));
      if (query.value.trim()) empty.append(actionButton('Clear search', () => { query.value = ''; offset = 0; load(); query.focus(); }));
      else { const link = element('a', 'Go to your shelf', 'button'); link.href = '/bookshelf'; empty.append(link); }
      list.append(empty); return;
    }
    for (const paper of items) {
      const row = element('article', undefined, 'reading-history-paper'); row.dataset.paperId = paper.paper_id;
      const details = element('div', undefined, 'reading-history-details');
      details.append(element('h3', paper.title || paper.paper_id));
      details.append(element('p', [paper.author, paper.year, paper.journal].filter(Boolean).join(' · '), 'muted'));
      const date = new Date(paper.read_at), when = element('p', undefined, 'reading-history-date');
      if (paper.read_at && Number.isFinite(date.valueOf())) {
        const time = element('time', date.toLocaleDateString([], {year: 'numeric', month: 'short', day: 'numeric'}));
        time.dateTime = date.toISOString(); when.append(document.createTextNode('Finished '), time);
      } else when.textContent = 'Finished';
      details.append(when);
      const actions = element('div', undefined, 'actions');
      if (paper.available) { const read = element('a', 'Read PDF', 'button'); read.href = '/reader?' + new URLSearchParams({paper_id: paper.paper_id}); actions.append(read); }
      if (paper.on_shelf) actions.append(element('span', 'On your shelf', 'reading-on-shelf'));
      else actions.append(actionButton('Save to shelf', () => mutate(paper, 'save_to_shelf')));
      actions.append(actionButton('Mark as unread', () => mutate(paper, 'mark_unread')));
      row.append(details, actions); list.append(row);
    }
  }

  async function load() {
    if (writing) return;
    clearTimeout(debounce); request?.abort(); request = new AbortController();
    const current = ++sequence; loading = true; controls(); list.setAttribute('aria-busy', 'true');
    feedback.hidden = true; results.textContent = 'Finding your finished papers…'; updateURL();
    try {
      const parameters = new URLSearchParams({offset: String(offset), limit: String(limit)});
      if (query.value.trim()) parameters.set('query', query.value.trim());
      const data = await api('/api/reading?' + parameters, undefined, {signal: request.signal});
      if (current !== sequence) return;
      if (!Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0 || !Number.isSafeInteger(data.read_count) || data.read_count < 0) throw new Error('Your reading history could not be loaded. Please try again.');
      total = data.total; announceCount(data.read_count);
      if (offset > 0 && offset >= total) {
        offset = Math.max(0, Math.floor((total - 1) / limit) * limit);
        return await load();
      }
      render(data.items);
      results.textContent = query.value.trim() ? `${total} ${total === 1 ? 'matching paper' : 'matching papers'}` : 'Your most recently finished papers come first.';
      position.textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : '';
    } catch (error) {
      if (current !== sequence || error.name === 'AbortError') return;
      results.textContent = 'Reading history is temporarily unavailable';
      feedback.replaceChildren(element('p', error.message), actionButton('Retry', load)); feedback.hidden = false;
    } finally {
      if (current === sequence) { loading = false; list.removeAttribute('aria-busy'); controls(); }
    }
  }

  form.addEventListener('submit', event => { event.preventDefault(); offset = 0; load(); });
  query.addEventListener('input', () => {
    clearTimeout(debounce); sequence++; request?.abort(); offset = 0; loading = true; controls();
    debounce = setTimeout(load, 220);
  });
  window.addEventListener('pagehide', () => { clearTimeout(debounce); request?.abort(); }, {once: true});
  await load();
}
