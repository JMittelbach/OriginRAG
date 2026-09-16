CSS = r"""
    header:has(#shelfButton) { align-items: flex-start; }
    #shelfDialog { width: min(1060px, calc(100vw - 28px)); max-height: 90dvh; padding: 0; border: 1px solid var(--line); border-radius: 24px; color: var(--text); background: var(--panel, #101b20); box-shadow: 0 30px 100px #0009; }
    #shelfDialog::backdrop { background: #030a10a8; backdrop-filter: blur(8px); }
    .shelf-heading { display: flex; gap: 18px; align-items: center; justify-content: space-between; padding: 26px 28px 16px; }
    .shelf-heading h2 { margin: 0; font: 34px Georgia, serif; letter-spacing: -.7px; }
    .shelf-heading p { margin: 7px 0 0; color: var(--muted); font-size: 14px; }
    #shelfClose { flex: 0 0 auto; width: auto; padding: 0 18px; }
    .shelf-layout { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(240px, 1fr); gap: 22px; padding: 0 28px 26px; }
    .shelf-case { padding: 24px 22px 0; border: 9px solid #513a30; border-radius: 12px; background: repeating-linear-gradient(92deg, #392c25 0, #392c25 3px, #3d3028 5px, #3d3028 90px); box-shadow: inset 0 12px 26px #0007, 0 12px 20px #0003; }
    :root[data-theme="light"] .shelf-case { border-color: #aa8260; background: repeating-linear-gradient(92deg, #ddc5a8 0, #ddc5a8 3px, #e4ceb3 5px, #e4ceb3 90px); }
    .shelf-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); align-items: end; gap: 14px; padding: 10px 0 0; margin: 0 0 28px; position: relative; isolation: isolate; }
    .shelf-row::after { content: ''; position: absolute; z-index: -1; left: -22px; right: -22px; bottom: -16px; height: 20px; border-top: 5px solid #be906c; background: linear-gradient(#956849, #5a3d2d); box-shadow: 0 10px 12px #0006; }
    .shelf-book { --book-hue: 168; display: flex; flex-direction: column; align-items: stretch; justify-content: space-between; gap: 12px; height: var(--book-height, 204px); width: 100%; min-width: 0; border: 0; border-radius: 4px 8px 7px 3px; padding: 18px 13px 16px 18px; text-align: left; color: #fff9e9; background: linear-gradient(90deg, hsl(var(--book-hue) 27% 22%) 0 7px, hsl(var(--book-hue) 34% 36%) 9px, hsl(var(--book-hue) 30% 30%) 20px, hsl(var(--book-hue) 30% 38%) 100%); box-shadow: inset 0 1px #ffffff40, inset -3px 0 #0003, 5px 6px 10px #0007; transform: perspective(700px) rotateY(-13deg); transform-origin: left bottom; transition: transform .2s ease, box-shadow .2s ease; position: relative; }
    .shelf-book::after { content: ''; position: absolute; right: -5px; top: 5px; bottom: 5px; width: 5px; background: repeating-linear-gradient(#dbc9a4 0 2px, #f2e6c9 2px 4px); transform: skewY(-18deg); border-radius: 0 2px 2px 0; }
    .shelf-book:hover, .shelf-book:focus-visible, .shelf-book[aria-pressed="true"] { transform: perspective(700px) rotateY(-3deg) translateY(-9px); box-shadow: 6px 14px 18px #0007; outline: 2px solid #ead5a7; outline-offset: 3px; }
    .shelf-book-title { font: 600 clamp(13px, 1.6vw, 17px)/1.25 Georgia, serif; display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
    .shelf-book-author { font-size: 10px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .85; }
    .shelf-book-mark { width: 10px; height: 16px; background: #ead5a7; clip-path: polygon(0 0,100% 0,100% 100%,50% 70%,0 100%); }
    .shelf-detail { align-self: start; padding: 22px 0; min-width: 0; }
    .shelf-detail h3 { margin: 0 0 12px; font: 25px/1.25 Georgia, serif; overflow-wrap: anywhere; }
    .shelf-detail p { color: var(--muted); font-size: 14px; line-height: 1.6; overflow-wrap: anywhere; }
    .shelf-detail-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }
    .shelf-detail-actions a { padding: 11px 14px; border: 1px solid var(--line); border-radius: 12px; text-decoration: none; color: var(--text); }
    .shelf-empty { min-height: 230px; padding: 22px 8px 40px; text-align: center; color: #f6e8d3; font: 20px/1.5 Georgia, serif; }
    :root[data-theme="light"] .shelf-empty { color: #513a30; }
    .shelf-empty span { display: block; font-size: 44px; margin-bottom: 14px; }
    #shelfNotice { min-height: 22px; margin: 0 28px 12px; color: var(--muted); font-size: 13px; }
    .shelf-save { font-size: 12px; padding: 7px 11px; margin: 5px 5px 0 0; border-radius: 9px; }
    .shelf-save[aria-pressed="true"] { color: var(--teal); border-color: var(--teal); }
    @media (max-width: 800px) { .shelf-layout { grid-template-columns: 1fr; } .shelf-detail { padding-top: 4px; } }
    @media (max-width: 640px) { .shelf-heading { padding: 22px 18px 12px; } .shelf-layout { padding: 0 18px 22px; } .shelf-row { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px; } .shelf-book-title { font-size: 17px; } .shelf-heading h2 { font-size: 29px; } }
    @media (prefers-reduced-motion: reduce) { .shelf-book { transition: none; } }
    #shelfDialog .shelf-book { color: #fff9e9; background: linear-gradient(90deg, hsl(var(--book-hue) 27% 22%) 0 7px, hsl(var(--book-hue) 34% 36%) 9px, hsl(var(--book-hue) 30% 30%) 20px, hsl(var(--book-hue) 30% 38%) 100%); }
    #shelfDialog .shelf-detail button { width: auto; padding: 0 14px; }
    @media (max-width: 640px) {
      .shelf-case { padding: 18px 12px 0; border-width: 6px; }
      .shelf-row { gap: 12px; }
      .shelf-row::after { left: -12px; right: -12px; }
      .shelf-book { padding: 14px 10px 14px 14px; }
      .shelf-book-title { font-size: 15px; }
    }
"""

MARKUP = r"""
  <dialog id="shelfDialog" aria-labelledby="shelfTitle">
    <div class="shelf-heading"><div><h2 id="shelfTitle">My little bookshelf</h2><p>Your current reads, a little closer.</p></div><button id="shelfClose" type="button" aria-label="Close my shelf">Close</button></div>
    <p id="shelfNotice" role="status" aria-live="polite"></p>
    <div class="shelf-layout"><div class="shelf-case" id="shelfBooks" aria-label="Saved papers"></div><div class="shelf-detail" id="shelfDetail"></div></div>
  </dialog>
"""

SCRIPT = r"""
    (() => {
      const button = document.getElementById('shelfButton');
      const dialog = document.getElementById('shelfDialog');
      const books = document.getElementById('shelfBooks');
      const detail = document.getElementById('shelfDetail');
      const notice = document.getElementById('shelfNotice');
      let saved = new Map();
      let selected = '';
      let loaded = false;
      let loading = null;
      let changing = false;
      const small = window.matchMedia('(max-width: 640px)');
      const textNode = (tag, text, className) => {
        const el = document.createElement(tag);
        el.textContent = text;
        if (className) el.className = className;
        return el;
      };
      function syncButtons() {
        document.querySelectorAll('[data-shelf-paper]').forEach(el => {
          const active = saved.has(el.dataset.shelfPaper);
          el.textContent = active ? '▣ On my shelf' : '▱ Save to shelf';
          el.setAttribute('aria-pressed', String(active));
          el.disabled = changing || !loaded;
        });
      }
      function apply(data) {
        saved = new Map((data.items || []).map(p => [p.paper_id, p]));
        loaded = true;
        if (!saved.has(selected)) selected = '';
        syncButtons();
        if (dialog.open) render();
      }
      async function load() {
        if (loading) return loading;
        loading = (async () => {
          const response = await fetch('/api/shelf');
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not open your shelf.');
          apply(data);
        })();
        try { await loading; } finally { loading = null; }
      }
      async function change(paper, action) {
        if (changing) return;
        changing = true;
        syncButtons();
        try {
          if (loading) await loading;
          const response = await fetch('/api/shelf', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action, paper_id:paper.paper_id})});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not update your shelf.');
          apply(data);
          statusEl.textContent = action === 'add' ? 'Saved to your shelf.' : 'Removed from your shelf. The paper stays in Library.';
          notice.textContent = statusEl.textContent;
        } catch (error) {
          statusEl.textContent = error.message;
          notice.textContent = error.message;
        } finally {
          changing = false;
          syncButtons();
          if (dialog.open) { renderDetail(); if (action === 'remove' && !saved.has(paper.paper_id)) detail.querySelector('button')?.focus(); }
        }
      }
      window.originragShelfButton = paper => {
        const el = textNode('button', saved.has(paper.paper_id) ? '▣ On my shelf' : '▱ Save to shelf', 'shelf-save');
        el.type = 'button';
        el.dataset.shelfPaper = paper.paper_id;
        el.setAttribute('aria-pressed', String(saved.has(paper.paper_id)));
        el.setAttribute('aria-label', `Save or remove ${paper.title || 'this paper'} on my shelf`);
        el.disabled = !loaded || changing;
        el.addEventListener('click', () => change(paper, saved.has(paper.paper_id) ? 'remove' : 'add'));
        if (!loaded) load().catch(error => { statusEl.textContent = error.message; });
        return el;
      };
      function close() { dialog.close(); }
      function renderDetail() {
        detail.replaceChildren();
        const paper = saved.get(selected);
        if (!paper) {
          detail.append(textNode('h3', saved.size ? 'Pick a good read' : 'A place for your next idea'),
                        textNode('p', saved.size ? 'Choose a book to open the paper or read its summary.' : 'Save papers from Library or from the references in your chat. They will be waiting here next time.'));
          const browse = textNode('button', 'Browse Library');
          browse.type = 'button';
          browse.addEventListener('click', () => { close(); openLibraryBrowser(); });
          detail.append(browse);
          return;
        }
        detail.append(textNode('h3', paper.title), textNode('p', [paper.author,paper.year,paper.journal].filter(Boolean).join(' · ')));
        if (!paper.available) detail.append(textNode('p', 'This paper is no longer in the current library. Your bookmark is still here.'));
        const actions = textNode('div', '', 'shelf-detail-actions');
        if (paper.available && paper.pdf_url) {
          const open = textNode('a', 'Open reader');
          open.href = '/reader?paper_id=' + encodeURIComponent(paper.paper_id);
          open.target = '_blank'; open.rel = 'noopener noreferrer';
          actions.append(open);
        } else if (paper.available) {
          detail.append(textNode('p', 'No PDF is stored in the library for this paper yet.'));
        }
        if (/^https?:\/\//.test(paper.doi_url || '')) {
          const open = textNode('a', 'Publisher page');
          open.href = paper.doi_url; open.target = '_blank'; open.rel = 'noopener noreferrer';
          actions.append(open);
        }
        const summarize = textNode('button', 'Summarize');
        summarize.type = 'button'; summarize.disabled = !paper.summary_available;
        summarize.title = summarize.disabled ? 'A searchable paper or abstract is needed first.' : 'Read a summary with source passages';
        summarize.addEventListener('click', () => {
          if (submittingTurn || message.value.trim()) { notice.textContent = 'Send or clear your current chat draft first.'; return; }
          close();
          message.value = 'Summarize this paper: ' + (paper.doi || paper.title);
          resizeComposer(); form.requestSubmit();
        });
        const remove = textNode('button', 'Remove from shelf');
        remove.type = 'button'; remove.disabled = changing;
        remove.addEventListener('click', () => change(paper, 'remove'));
        actions.append(summarize, remove); detail.append(actions);
      }
      function render() {
        books.replaceChildren();
        notice.textContent = `${saved.size} ${saved.size === 1 ? 'paper' : 'papers'} · Only your bookmarks. Your library is unchanged.`;
        const entries = [...saved.values()];
        if (!entries.length) {
          const empty = textNode('div', '', 'shelf-empty');
          empty.append(textNode('span', '📚'), textNode('div', 'Your next good read belongs here.'));
          books.append(empty);
        }
        const width = small.matches ? 2 : 4;
        for (let start=0; start<entries.length; start+=width) {
          const row = textNode('div', '', 'shelf-row');
          entries.slice(start,start+width).forEach(paper => {
            let hash = 0;
            for (const character of paper.paper_id) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
            const book = textNode('button', '', 'shelf-book');
            book.type = 'button'; book.setAttribute('aria-label', `${paper.title}, ${paper.author || ''} ${paper.year || ''}`);
            book.setAttribute('aria-pressed', String(selected === paper.paper_id));
            book.style.setProperty('--book-hue', String([164,206,18,337,42,266][hash%6]));
            book.style.setProperty('--book-height', `${190 + hash%35}px`);
            const ribbon = textNode('span', '', 'shelf-book-mark'); ribbon.setAttribute('aria-hidden','true');
            book.append(ribbon, textNode('span', paper.title, 'shelf-book-title'), textNode('span', [paper.author,paper.year].filter(Boolean).join(' · '), 'shelf-book-author'));
            book.addEventListener('click', () => { selected=paper.paper_id; books.querySelectorAll('.shelf-book').forEach(el=>el.setAttribute('aria-pressed','false')); book.setAttribute('aria-pressed','true'); renderDetail(); if (window.matchMedia('(max-width: 800px)').matches) detail.scrollIntoView({block:'nearest'}); });
            row.append(book);
          });
          books.append(row);
        }
        renderDetail();
      }
      button.addEventListener('click', async () => {
        dialog.showModal(); button.setAttribute('aria-expanded','true');
        render(); notice.textContent='Opening your shelf…';
        try { await load(); } catch(error) { notice.textContent=error.message; }
      });
      document.getElementById('shelfClose').addEventListener('click', close);
      dialog.addEventListener('close', () => { button.setAttribute('aria-expanded','false'); if (!libraryBrowserBackdrop.classList.contains('open') && !submittingTurn) button.focus(); });
      dialog.addEventListener('click', event => { if(event.target===dialog) { const box=dialog.getBoundingClientRect(); if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom) close(); } });
      small.addEventListener('change', () => { if(dialog.open) render(); });
    })();
"""


def with_shelf(html: str) -> str:
    html = html.replace('</style>', CSS + '\n</style>', 1)
    html = html.replace('<button class="header-button" id="libraryButton"',
                        '<button class="header-button" id="shelfButton" type="button" aria-haspopup="dialog" aria-controls="shelfDialog" aria-expanded="false">My shelf</button>\n          <button class="header-button" id="libraryButton"', 1)
    html = html.replace('  <script>\n    const browserFetch', MARKUP + '\n  <script>\n    const browserFetch', 1)
    return html.replace('    loadStatus();\n  </script>', SCRIPT + '\n    loadStatus();\n  </script>', 1)
