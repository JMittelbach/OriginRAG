import {getDocument, GlobalWorkerOptions, TextLayer} from '/assets/vendor/pdfjs/legacy/build/pdf.min.mjs';
import {navigation} from './navigation.mjs';

GlobalWorkerOptions.workerSrc = '/assets/vendor/pdfjs/legacy/build/pdf.worker.min.mjs';
const $ = id => document.getElementById(id);
const paperId = new URLSearchParams(location.search).get('paper_id');
const original = '/api/library/pdf?paper_id=' + encodeURIComponent(paperId || '');
const base = '/assets/vendor/pdfjs/';
let pdf, fingerprint, currentPage = 1, zoom = 1, marks = [], selection = null;
let generation = 0, canvasTask, textTask, saving = false, ready = false, notesReady = false;
const drafts = new Map();
const colors = new Set(['yellow', 'mint', 'rose']);
const status = (message, error = false) => { $('status').textContent = message; $('status').classList.toggle('error', error); };
const node = (tag, text, className) => { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; };
const dirty = () => [...drafts].some(([id, value]) => value !== marks.find(m => m.id === id)?.note);
const navigate = navigation({getPdf:()=>pdf,getPage:()=>currentPage,getZoom:()=>zoom,renderPage});
function controls() {
  for (const id of ['zoomIn','zoomOut','fit','pageNumber','reload','export','find','contents']) $(id).disabled = !ready || saving;
  $('export').disabled = !ready || saving || !notesReady;
  $('previous').disabled = !ready || currentPage <= 1;
  $('next').disabled = !ready || currentPage >= (pdf?.numPages || 0);
  $('highlight').disabled = !ready || !selection || saving || !notesReady;
  $('selectionTools').hidden = $('highlight').disabled;
  if(document.activeElement!==$('pageNumber')) $('pageNumber').value = currentPage;
  $('zoomLabel').textContent = Math.round(zoom * 100) + '%';
}
async function api(method, payload) {
  const token = document.cookie.split('; ').find(c => c.startsWith('pfempire_csrf='))?.slice('pfempire_csrf='.length) || '';
  const url = '/api/reader/notes' + (method === 'GET' ? '?' + new URLSearchParams({paper_id:paperId,fingerprint}) : '');
  const response = await fetch(url, {method, headers:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(token)}, ...(payload ? {body:JSON.stringify({paper_id:paperId,fingerprint,...payload})} : {})});
  if (response.status === 401) throw new Error('Please sign in again in another tab, then retry. Your unsaved note is still here.');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not save your notes. Please retry.');
  return data;
}
function applyNotes(data) {
  notesReady = true;
  marks = data.items;
  $('versionWarning').hidden = !data.other_version_count;
  $('versionWarning').textContent = 'You have notes on a different PDF version of this paper. They are preserved separately and are not overlaid on this version.';
  drawMarks(); renderNotes(); controls();
}
function drawMarks() {
  $('highlights').replaceChildren();
  for (const mark of marks.filter(m => m.page === currentPage)) {
    for (const [x,y,w,h] of mark.rects) {
      const el = node('div','', 'mark ' + (colors.has(mark.color) ? mark.color : 'yellow'));
      Object.assign(el.style,{left:x*100+'%',top:y*100+'%',width:w*100+'%',height:h*100+'%'});
      $('highlights').append(el);
    }
  }
}
async function change(payload, savedId, focusNew=false) {
  if (saving) return;
  const previousIds=new Set(marks.map(m=>m.id));
  let newId;
  saving = true; controls(); renderNotes(); status('Saving…');
  try {
    const data = await api('POST',payload);
    if (savedId) drafts.delete(savedId);
    if (payload.action === 'add') { selection = null; window.getSelection()?.removeAllRanges(); }
    applyNotes(data); status('Saved to your personal notes.');
    if (focusNew) newId=marks.find(m=>!previousIds.has(m.id))?.id;
  } catch(error) { status(error.message, true); }
  finally { saving = false; controls(); renderNotes();
    if(newId) {$('notesPanel').hidden=false;$('notesToggle').setAttribute('aria-expanded','true');document.querySelector(`[data-note-id="${newId}"] textarea`)?.focus();}
  }
}
function renderNotes() {
  const container = $('notes'); container.replaceChildren();
  if (!marks.length) { container.append(node('p','No highlights yet. Start with a passage that catches your eye.','hint')); return; }
  for (const mark of [...marks].sort((a,b)=>a.page-b.page || a.rects[0][1]-b.rects[0][1])) {
    const item = node('section','','note'); item.dataset.noteId = mark.id; item.dataset.color=colors.has(mark.color)?mark.color:'yellow';
    const heading = node('div','','note-heading');
    const jump = node('button','Page ' + mark.page); jump.onclick = () => renderPage(mark.page, zoom, mark.rects[0][1]);
    heading.append(jump); item.append(heading,node('blockquote',mark.quote || 'Highlighted area'));
    const input = document.createElement('textarea'); input.setAttribute('aria-label','Note for highlight on page ' + mark.page); input.placeholder='What stood out to you?'; input.maxLength=10000;
    input.value = drafts.has(mark.id) ? drafts.get(mark.id) : mark.note; input.disabled = saving;
    input.oninput = () => { drafts.set(mark.id,input.value); save.disabled = input.value === mark.note; };
    const actions = node('div','','note-actions');
    const save = node('button','Save note'); save.disabled = saving || input.value === mark.note;
    save.onclick = () => { const {page,rects,quote,color} = mark; change({action:'update',id:mark.id,revision:mark.revision,mark:{page,rects,quote,color,note:input.value}}, mark.id); };
    const remove = node('button','Delete'); remove.disabled = saving;
    remove.onclick = () => { if (confirm('Delete this highlight and its note?')) change({action:'delete',id:mark.id,revision:mark.revision},mark.id); };
    actions.append(save,remove); item.append(input,actions); container.append(item);
  }
}
async function renderPage(number, scale, jumpY = null) {
  if (!pdf) return;
  const previousHeight=$('page').clientHeight;
  const scrollFraction=jumpY ?? (number===currentPage && previousHeight ? ($('workspace').scrollTop+50)/previousHeight : 0);
  const request = ++generation;
  ready = false; selection = null; window.getSelection()?.removeAllRanges(); controls();
  canvasTask?.cancel(); textTask?.cancel();
  await canvasTask?.promise.catch(()=>{});
  try {
    const page = await pdf.getPage(Math.max(1,Math.min(pdf.numPages,number)));
    if (request !== generation) return;
    currentPage = page.pageNumber; zoom = Math.max(.35,Math.min(3,scale));
    $('pageNumber').value=currentPage;
    const viewport = page.getViewport({scale:zoom});
    const ratio = Math.min(devicePixelRatio || 1, 2, Math.sqrt(16000000 / (viewport.width * viewport.height)));
    const canvas = $('canvas'), wrapper = $('page');
    canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = viewport.width+'px'; canvas.style.height = viewport.height+'px';
    wrapper.style.width = viewport.width+'px'; wrapper.style.height = viewport.height+'px';
    wrapper.style.setProperty('--scale-factor',String(viewport.scale));
    wrapper.style.setProperty('--user-unit',String(viewport.userUnit || 1));
    wrapper.style.display = 'block'; $('empty').hidden = true; $('textLayer').replaceChildren(); $('highlights').replaceChildren(); $('searchHighlights').replaceChildren();
    canvasTask = page.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0],isEditing:false});
    await canvasTask.promise;
    if (request !== generation) return;
    textTask = new TextLayer({textContentSource:page.streamTextContent(),container:$('textLayer'),viewport});
    await textTask.render();
    if (request !== generation) return;
    drawMarks(); navigate.draw(); ready = true; controls();
    $('workspace').scrollTop = Math.max(0,viewport.height*scrollFraction-50);
    if (!saving) status(!notesReady ? 'Your notes could not be loaded. You can keep reading. Press ↻ to retry before adding highlights.' : $('textLayer').textContent.trim() ? 'Select text to highlight a passage. Notes are private to your account.' : 'This page has no selectable text. Scanned PDFs need OCR before text highlighting is available.', !notesReady);
    page.cleanup();
  } catch(error) {
    if (request !== generation || error.name === 'RenderingCancelledException' || error.name === 'AbortException') return;
    status('This page could not be displayed. Try another page or open the original PDF.',true);
    ready = true; controls();
  }
}
function captureSelection() {
  const selected = window.getSelection();
  if (!ready || !selected?.rangeCount || selected.isCollapsed) { selection=null; controls(); return; }
  const layer = $('textLayer');
  if (!layer.contains(selected.anchorNode) || !layer.contains(selected.focusNode)) { selection=null; controls(); return; }
  const box = $('page').getBoundingClientRect();
  const rects = [...selected.getRangeAt(0).getClientRects()].filter(r => r.width > 1 && r.height > 1).map(r => {
    const left=Math.max(box.left,r.left), top=Math.max(box.top,r.top), right=Math.min(box.right,r.right), bottom=Math.min(box.bottom,r.bottom);
    return [(left-box.left)/box.width,(top-box.top)/box.height,(right-left)/box.width,(bottom-top)/box.height];
  }).filter(r => r[2]>0 && r[3]>0);
  const unique = [...new Map(rects.map(r=>[r.map(n=>n.toFixed(5)).join(','),r])).values()];
  const quote = selected.toString().trim();
  selection = unique.length && unique.length <= 200 && quote.length <= 10000 ? {page:currentPage,rects:unique,quote} : null;
  if (!selection && quote) status('Select a shorter passage to highlight.',true);
  controls();
  if(selection) {
    const rect=selected.getRangeAt(0).getBoundingClientRect();
    const toolbar=$('selectionTools');
    toolbar.style.left=Math.max(8,Math.min(innerWidth-toolbar.offsetWidth-8,rect.left))+'px';
    toolbar.style.top=Math.max(8,Math.min(innerHeight-toolbar.offsetHeight-8,rect.bottom+8))+'px';
  }
}
document.addEventListener('selectionchange',captureSelection);
$('workspace').addEventListener('scroll',()=>{if(selection)captureSelection();},{passive:true});
window.addEventListener('resize',()=>{if(selection)captureSelection();});
for(const button of $('selectionTools').querySelectorAll('button')) {
  button.onpointerdown=event=>event.preventDefault();
  button.onclick=()=>{if(selection){const color=button.dataset.highlight || $('color').value;change({action:'add',mark:{...selection,color,note:''}},undefined,button.id==='selectionNote');}};
}
$('highlight').onpointerdown = event => event.preventDefault();
$('highlight').onclick = () => { if(selection) change({action:'add',mark:{...selection,color:$('color').value,note:''}}); };
$('previous').onclick = () => renderPage(currentPage-1,zoom);
$('next').onclick = () => renderPage(currentPage+1,zoom);
$('pageNumber').onchange = () => { const value=Number($('pageNumber').value); if(Number.isInteger(value)) renderPage(value,zoom); else controls(); };
$('zoomIn').onclick = () => renderPage(currentPage,zoom+.2);
$('zoomOut').onclick = () => renderPage(currentPage,zoom-.2);
async function fit() { if (!pdf) return; const p = await pdf.getPage(currentPage); const width = p.getViewport({scale:1}).width; await renderPage(currentPage,Math.min(2,Math.max(.35,($('workspace').clientWidth-56)/width))); }
$('fit').onclick = fit;
$('notesToggle').onclick = () => { $('notesPanel').hidden = !$('notesPanel').hidden; $('notesToggle').setAttribute('aria-expanded',String(!$('notesPanel').hidden)); };
$('theme').onclick = () => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme=next; try {localStorage.setItem('originrag-reader-theme',next);} catch {} };
try {document.documentElement.dataset.theme=localStorage.getItem('originrag-reader-theme') || 'light';} catch {}
$('reload').onclick = async () => { try {applyNotes(await api('GET'));status(dirty()?'Notes refreshed. Your unsaved text has been kept. Review it before saving.':'Notes refreshed.');} catch(e) {status(e.message,true);} };
$('export').onclick = () => {
  const data = {format:'originrag-reader-notes-v1',paper_id:paperId,fingerprint,exported_at:new Date().toISOString(),annotations:marks};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const link=node('a',''); link.href=url; link.download='originrag-reading-notes.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  status(dirty()?'Exported saved notes only. Save your draft to include it.':'Notes exported.');
};
window.addEventListener('beforeunload',event=>{if(dirty() || saving){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{
  if(event.isComposing || $('outlineDialog').open)return;
  const editing=event.target.closest('input,textarea,select,[contenteditable="true"]');
  if((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==='f' && pdf){event.preventDefault();navigate.open();return;}
  if((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==='s' && event.target.matches('textarea')){event.preventDefault();event.target.closest('.note')?.querySelector('.note-actions button')?.click();return;}
  if(editing || event.ctrlKey || event.metaKey || event.altKey || !ready)return;
  if(event.key.toLowerCase()==='h' && selection){event.preventDefault();$('highlight').click();}
  else if(!selection && !event.target.closest('button,a')) {
    if(event.key==='ArrowRight'){event.preventDefault();$('next').click();}
    if(event.key==='ArrowLeft'){event.preventDefault();$('previous').click();}
  }
});
async function start() {
  try {
    if (!paperId) throw new Error('Choose a paper from your shelf first.');
    $('original').href=original; $('original').hidden=false;
    const metadata = await fetch('/api/reader/paper?'+new URLSearchParams({paper_id:paperId}));
    if (!metadata.ok) throw new Error(metadata.status===401?'Please sign in to OriginRAG first.':'This paper is not available in your library.');
    const info=await metadata.json(); $('title').textContent=info.title || paperId; document.title=(info.title || paperId)+' · OriginRAG Reader';
    if (!crypto.subtle) throw new Error('The reader needs localhost or HTTPS to safely identify PDF versions. You can still open the original PDF.');
    const response=await fetch(original);
    if (!response.ok) throw new Error('The PDF could not be opened. Please sign in again or check the library.');
    const limit=64*1024*1024, expected=Number(response.headers.get('Content-Length') || 0);
    if (expected>limit) {await response.body.cancel();throw new Error('This PDF is larger than the 64 MB reader limit. Please use Original PDF.');}
    const stream=response.body.getReader(), chunks=[]; let size=0;
    while(true) {const {done,value}=await stream.read(); if(done) break; size+=value.length; if(size>limit){await stream.cancel();throw new Error('This PDF is larger than the 64 MB reader limit. Please use Original PDF.');} chunks.push(value); status('Opening PDF… '+Math.round(size/1024)+' KB');}
    const bytes=new Uint8Array(size); let offset=0; for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;} chunks.length=0;
    fingerprint=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    pdf=await getDocument({data:bytes,cMapUrl:base+'cmaps/',cMapPacked:true,standardFontDataUrl:base+'standard_fonts/',wasmUrl:base+'wasm/',iccUrl:base+'iccs/',isEvalSupported:false,enableXfa:false}).promise;
    $('pageCount').textContent='/ '+pdf.numPages; $('pageNumber').max=pdf.numPages;
    try {applyNotes(await api('GET'));} catch {notesReady=false;}
    await fit();
  } catch(error) {status(error.message || 'Could not open this PDF. Please use Original PDF.',true); $('empty').textContent='Your original PDF is still available using the link above.';}
}
await start();
