import {getDocument, GlobalWorkerOptions, TextLayer} from '/assets/vendor/pdfjs/legacy/build/pdf.min.mjs';
import {navigation} from './navigation.mjs';
import {readingTools} from './reading-tools.mjs';
import {readingStatus} from './reading-status.mjs';
import {requestJSON} from '/assets/workspace/request.mjs';

GlobalWorkerOptions.workerSrc = '/assets/vendor/pdfjs/legacy/build/pdf.worker.min.mjs';
const $ = id => document.getElementById(id);
const paperId = new URLSearchParams(location.search).get('paper_id');
const original = '/api/library/pdf?paper_id=' + encodeURIComponent(paperId || '');
const base = '/assets/vendor/pdfjs/';
let pdf, fingerprint, currentPage = 1, zoom = 1, marks = [], selection = null;
let generation = 0, saving = false, reloading = false, ready = false, notesReady = false, noteError = false;
let views=[], navigating=false, scrollFrame=0;
const drafts = new Map();
const colorDrafts = new Map();
const recoveredDrafts = new Map();
const colors = new Set(['yellow', 'mint', 'rose', 'red']);
const colorNames = {yellow:'Yellow',mint:'Green',red:'Red',rose:'Rose'};
let focusedNote='', pendingNoteClick, pointerStart;
const status = (message, error = false) => { $('status').textContent = message; $('status').classList.toggle('error', error); };
const node = (tag, text, className) => { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; };
const changed = mark => (drafts.has(mark.id) && drafts.get(mark.id) !== mark.note) || (colorDrafts.has(mark.id) && colorDrafts.get(mark.id) !== mark.color);
const dirty = () => recoveredDrafts.size>0 || [...drafts].some(([id,value])=>value!==marks.find(mark=>mark.id===id)?.note) || [...colorDrafts].some(([id,value])=>value!==marks.find(mark=>mark.id===id)?.color);
const navigate = navigation({getPdf:()=>pdf,getPage:()=>currentPage,getZoom:()=>zoom,renderPage});
const reading = readingTools({getPdf:()=>pdf,getPage:()=>currentPage,getZoom:()=>zoom,renderPage,status,getReady:()=>ready,getSaving:()=>saving});
const readTracker = readingStatus({paperId,status});
function noteState() {
  $('notesState').textContent = saving ? 'Saving…' : reloading ? 'Refreshing…' : noteError ? 'Save not confirmed' : !notesReady ? 'Notes unavailable' : dirty() ? 'Unsaved changes' : 'Saved';
  $('notesState').dataset.state = noteError ? 'error' : dirty() ? 'dirty' : 'saved';
}
function controls() {
  for (const id of ['zoomIn','zoomOut','fit','pageNumber','reload','export','find','contents']) $(id).disabled = !ready || saving;
  $('export').disabled = !ready || saving || !notesReady;
  $('reload').disabled = !pdf || saving || reloading;
  $('exportFormat').disabled = $('export').disabled;
  $('previous').disabled = !ready || currentPage <= 1;
  $('next').disabled = !ready || currentPage >= (pdf?.numPages || 0);
  $('highlight').disabled = !ready || !selection || saving || reloading || !notesReady;
  $('selectionTools').hidden = $('highlight').disabled;
  if(document.activeElement!==$('pageNumber')) $('pageNumber').value = currentPage;
  $('zoomLabel').textContent = Math.round(zoom * 100) + '%';
  noteState(); reading.update();
}
async function api(method, payload) {
  const token = document.cookie.split('; ').find(c => c.startsWith('pfempire_csrf='))?.slice('pfempire_csrf='.length) || '';
  const url = '/api/reader/notes' + (method === 'GET' ? '?' + new URLSearchParams({paper_id:paperId,fingerprint}) : '');
  const data = await requestJSON(url, {headers:{'X-CSRF-Token':decodeURIComponent(token)},timeout:method==='GET'?15000:30000,...(payload?{payload:{paper_id:paperId,fingerprint,...payload}}:{})});
  if(!Array.isArray(data?.items)) throw new Error('Your notes could not be loaded. Please reload notes and try again.');
  return data;
}
function applyNotes(data) {
  notesReady = true;
  const available=new Set(data.items.map(mark=>mark.id));
  for(const mark of marks)if(!available.has(mark.id)) {
    if(changed(mark))recoveredDrafts.set(mark.id,{...mark,note:drafts.get(mark.id) ?? mark.note,color:colorDrafts.get(mark.id) ?? mark.color});
    drafts.delete(mark.id);colorDrafts.delete(mark.id);
  }
  marks = data.items;
  $('versionWarning').hidden = !data.other_version_count;
  $('versionWarning').textContent = 'You have notes on a different PDF version of this paper. They are preserved separately and are not overlaid on this version.';
  drawMarks(); renderNotes(); controls();
}
function drawMarks() {
  for(const view of views) {
    view.highlights.replaceChildren();
    if(!view.rendered)continue;
    for (const mark of marks.filter(m => m.page === view.number)) {
    for (const [x,y,w,h] of mark.rects) {
      const el = node('div','', 'mark ' + (colors.has(mark.color) ? mark.color : 'yellow'));
      el.dataset.noteId=mark.id;
      el.classList.toggle('selected',mark.id===focusedNote);
      Object.assign(el.style,{left:x*100+'%',top:y*100+'%',width:w*100+'%',height:h*100+'%'});
      view.highlights.append(el);
    }
    }
  }
}
function focusNote(id) {
  if(saving || !marks.some(mark=>mark.id===id))return;
  focusedNote=id;
  $('notesPanel').hidden=false;
  $('notesToggle').setAttribute('aria-expanded','true');
  $('notesFilter').value=''; $('notesColor').value='all'; filterNotes();
  for(const item of $('notes').querySelectorAll('.note')) item.classList.toggle('selected',item.dataset.noteId===id);
  drawMarks();
  const input=document.querySelector(`[data-note-id="${id}"].note textarea`);
  input?.focus();
  input?.scrollIntoView({block:'nearest'});
}
function markAt(x,y) {
  const wrapper=document.elementFromPoint(x,y)?.closest('.reader-page');
  if(!wrapper)return;
  const box=wrapper.getBoundingClientRect();
  if(!box.width || !box.height)return;
  const px=(x-box.left)/box.width,py=(y-box.top)/box.height;
  return [...marks].reverse().find(mark=>mark.page===Number(wrapper.dataset.page) && mark.rects.some(([left,top,width,height])=>px>=left && px<=left+width && py>=top && py<=top+height));
}
function cancelNoteClick() {clearTimeout(pendingNoteClick);pendingNoteClick=null;}
$('workspace').addEventListener('pointerdown',event=>{
  cancelNoteClick();const wrapper=event.target.closest('.reader-page');pointerStart=event.button===0 && wrapper?{x:event.clientX,y:event.clientY,moved:false,page:Number(wrapper.dataset.page)}:null;
});
$('workspace').addEventListener('pointermove',event=>{
  if(pointerStart && Math.hypot(event.clientX-pointerStart.x,event.clientY-pointerStart.y)>5)pointerStart.moved=true;
  const layer=event.target.closest('.textLayer');
  if(layer)layer.title=markAt(event.clientX,event.clientY)?'Click to comment. Drag or double-click to select and copy text.':'';
});
$('workspace').addEventListener('pointercancel',()=>{pointerStart=null;cancelNoteClick();});
$('workspace').addEventListener('dblclick',cancelNoteClick);
window.addEventListener('blur',cancelNoteClick);
$('workspace').addEventListener('click',event=>{
  const gesture=pointerStart;pointerStart=null;cancelNoteClick();
  if(event.button!==0 || event.detail!==1 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !gesture || gesture.moved || !ready || saving || !getSelection()?.isCollapsed)return;
  const mark=markAt(event.clientX,event.clientY);
  if(!mark)return;
  pendingNoteClick=setTimeout(()=>{
    if(!saving && gesture.page===mark.page && getSelection()?.isCollapsed) focusNote(mark.id);
  },350);
});
async function change(payload, savedId, focusNew=false) {
  if (saving || reloading) return;
  const previousIds=new Set(marks.map(m=>m.id));
  let newId;
  saving = true; noteError=false; controls(); renderNotes(); status('Saving…');
  try {
    const data = await api('POST',payload);
    if (savedId) {drafts.delete(savedId);colorDrafts.delete(savedId);}
    if (payload.action === 'add') { selection = null; window.getSelection()?.removeAllRanges(); }
    applyNotes(data); status('Saved to your personal notes.');
    if (focusNew) newId=marks.find(m=>!previousIds.has(m.id))?.id;
  } catch(error) { noteError=true; status(error.message, true); }
  finally { saving = false; controls(); renderNotes();
    if(newId) focusNote(newId);
  }
}
function filterNotes() {
  const query=$('notesFilter').value.trim().toLocaleLowerCase(),color=$('notesColor').value;
  let shown=0;
  const byId=new Map(marks.map(mark=>[mark.id,mark]));
  for(const item of $('notes').querySelectorAll('.note')) {
    if(item.classList.contains('draft-recovery'))continue;
    const mark=byId.get(item.dataset.noteId);
    const text=mark ? [mark.quote,drafts.get(mark.id) ?? mark.note].join('\n').toLocaleLowerCase() : '';
    item.hidden=!mark || (color!=='all' && color!==mark.color) || (query && !text.includes(query));
    if(!item.hidden)shown++;
  }
  $('notesCount').textContent=(shown===marks.length?String(marks.length):`${shown} of ${marks.length}`)+(marks.length===1?' highlight':' highlights');
  $('notesEmpty').hidden=!marks.length || shown>0;
  $('notesEmpty').textContent='No matching notes. Try another phrase or colour.';
}
$('notesFilter').addEventListener('input',filterNotes);
$('notesColor').addEventListener('change',filterNotes);
function downloadFile(content,type,filename) {
  const url=URL.createObjectURL(new Blob([content],{type}));
  const link=node('a','');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderNotes() {
  const container = $('notes'); container.replaceChildren();
  for(const [id,draft] of recoveredDrafts) {
    const item=node('section','','note draft-recovery');
    item.append(node('strong','Unsaved draft · Page '+draft.page),node('p','This highlight was removed in another window. Save a copy of your draft before dismissing it.','warning'),node('blockquote',draft.quote));
    const input=document.createElement('textarea');input.readOnly=true;input.value=draft.note;input.setAttribute('aria-label','Recovered draft from page '+draft.page);item.append(input);
    const actions=node('div','','note-actions'),copy=node('button','Save draft copy'),discard=node('button','Dismiss draft');
    copy.onclick=()=>downloadFile(JSON.stringify({format:'originrag-reader-draft-v1',paper_id:paperId,fingerprint,annotation:draft},null,2),'application/json','originrag-reading-draft.json');
    discard.onclick=()=>{if(confirm('Dismiss this unsaved draft? Save a copy first if you want to keep it.')){recoveredDrafts.delete(id);renderNotes();controls();}};
    actions.append(copy,discard);item.append(actions);container.append(item);
  }
  if (!marks.length) { container.append(node('p',notesReady?'No highlights yet. Select a passage and choose a colour to save it.':'Your personal notes have not loaded yet.','hint')); filterNotes();noteState();return; }
  for (const mark of [...marks].sort((a,b)=>a.page-b.page || a.rects[0][1]-b.rects[0][1])) {
    const item = node('section','','note'); item.dataset.noteId = mark.id; item.dataset.color=colors.has(mark.color)?mark.color:'yellow';
    item.classList.toggle('selected',mark.id===focusedNote);
    const heading = node('div','','note-heading');
    const jump = node('button','Page ' + mark.page); jump.onclick = () => renderPage(mark.page, zoom, mark.rects[0][1]);
    const recolor=document.createElement('select');recolor.className='note-color';recolor.setAttribute('aria-label','Highlight colour on page '+mark.page);
    for(const [value,label] of Object.entries(colorNames)){const option=node('option',label);option.value=value;recolor.append(option);}
    recolor.value=colorDrafts.get(mark.id) ?? mark.color;recolor.disabled=saving || reloading;
    recolor.onchange=()=>{colorDrafts.set(mark.id,recolor.value);save.disabled=!changed(mark);noteState();};
    heading.append(jump,recolor); item.append(heading,node('blockquote',mark.quote || 'Highlighted area'));
    const input = document.createElement('textarea'); input.setAttribute('aria-label','Note for highlight on page ' + mark.page); input.placeholder='What stood out to you?'; input.maxLength=10000;
    input.value = drafts.has(mark.id) ? drafts.get(mark.id) : mark.note; input.disabled = saving || reloading;
    input.oninput = () => { drafts.set(mark.id,input.value); save.disabled = !changed(mark);noteState(); };
    const actions = node('div','','note-actions');
    const save = node('button','Save note'); save.disabled = saving || reloading || !changed(mark);
    save.onclick = () => { const {page,rects,quote} = mark; change({action:'update',id:mark.id,revision:mark.revision,mark:{page,rects,quote,color:recolor.value,note:input.value}}, mark.id); };
    const copy=node('button','Copy quote','note-copy');copy.disabled=!mark.quote;
    copy.onclick=async()=>{try {await navigator.clipboard.writeText(mark.quote);status('Quote copied.');}catch {status('Select the quote and use Ctrl/Cmd+C to copy it.',true);}};
    const remove = node('button','Delete'); remove.disabled = saving || reloading;
    remove.onclick = () => { if (confirm('Delete this highlight and its note?')) change({action:'delete',id:mark.id,revision:mark.revision},mark.id); };
    actions.append(save,copy,remove); item.append(input,actions); container.append(item);
  }
  filterNotes();noteState();
}
function pageTop(view) {return view.wrapper.getBoundingClientRect().top-$('workspace').getBoundingClientRect().top+$('workspace').scrollTop;}
function sizeView(view) {
  view.wrapper.style.width=view.width*zoom+'px';view.wrapper.style.height=view.height*zoom+'px';
}
function activate(view) {
  if(!view)return;
  for(const id of ['page','canvas','textLayer','highlights','searchHighlights']) $(id)?.removeAttribute('id');
  view.wrapper.id='page';view.canvas.id='canvas';view.textLayer.id='textLayer';view.highlights.id='highlights';view.searchHighlights.id='searchHighlights';
  currentPage=view.number;ready=view.rendered;controls();
}
async function initialisePages() {
  if(views.length)return;
  const first=await pdf.getPage(1), viewport=first.getViewport({scale:1});
  const fragment=document.createDocumentFragment();
  for(let number=1;number<=pdf.numPages;number++) {
    const wrapper=node('div','','reader-page');wrapper.dataset.page=number;wrapper.setAttribute('aria-label','PDF page '+number);
    const canvas=document.createElement('canvas');canvas.className='page-canvas';
    const textLayer=node('div','','textLayer'),highlights=node('div','','page-highlights'),searchHighlights=node('div','','page-search-highlights');
    highlights.setAttribute('aria-hidden','true');searchHighlights.setAttribute('aria-hidden','true');
    const placeholder=node('span','Page '+number,'page-placeholder');
    wrapper.append(canvas,highlights,searchHighlights,textLayer,placeholder);
    const view={number,wrapper,canvas,textLayer,highlights,searchHighlights,placeholder,width:viewport.width,height:viewport.height,rendered:false,pending:null};
    views.push(view);sizeView(view);fragment.append(wrapper);
  }
  $('workspace').replaceChildren(fragment);activate(views[0]);
}
function release(view) {
  if(!view.rendered && !view.canvas.width)return;
  view.canvas.width=0;view.canvas.height=0;view.textLayer.replaceChildren();view.highlights.replaceChildren();view.searchHighlights.replaceChildren();view.rendered=false;view.placeholder.hidden=false;
  view.pdfPage?.cleanup();
}
async function ensureView(view) {
  if(view.rendered)return;
  if(view.pending)return view.pending;
  const token=generation;
  view.pending=(async()=>{
    try {
      const page=await pdf.getPage(view.number);view.pdfPage=page;
      if(token!==generation)return;
      if(!navigating && Math.abs(view.number-currentPage)>2)return;
      const viewport=page.getViewport({scale:zoom}),unscaled=page.getViewport({scale:1});
      const active=views[currentPage-1],anchor=pageTop(active)-$('workspace').scrollTop;
      view.width=unscaled.width;view.height=unscaled.height;sizeView(view);
      if(!navigating)$('workspace').scrollTop=pageTop(active)-anchor;
      const ratio=Math.min(devicePixelRatio || 1,2,Math.sqrt(4000000/(viewport.width*viewport.height)));
      view.canvas.width=Math.floor(viewport.width*ratio);view.canvas.height=Math.floor(viewport.height*ratio);
      view.canvas.style.width=viewport.width+'px';view.canvas.style.height=viewport.height+'px';
      view.wrapper.style.setProperty('--scale-factor',String(viewport.scale));view.wrapper.style.setProperty('--user-unit',String(viewport.userUnit || 1));
      view.canvasTask=page.render({canvasContext:view.canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0],isEditing:false});
      await view.canvasTask.promise;if(token!==generation)return;
      view.textLayer.replaceChildren();
      view.textTask=new TextLayer({textContentSource:page.streamTextContent(),container:view.textLayer,viewport});
      await view.textTask.render();if(token!==generation)return;
      view.rendered=true;view.placeholder.hidden=true;drawMarks();navigate.draw();
      if(currentPage===view.number){ready=true;controls();}
    } catch(error) {
      if(token!==generation || error.name==='RenderingCancelledException' || error.name==='AbortException')return;
      view.placeholder.textContent='Page '+view.number+' could not be displayed. Use Original PDF or try another page.';
      if(currentPage===view.number){ready=true;controls();status(view.placeholder.textContent,true);}
    } finally {view.pending=null;}
  })();
  return view.pending;
}
async function renderNearby() {
  const token=generation,centre=currentPage;
  const selected=window.getSelection();
  const selectedPage=selected?.anchorNode?.parentElement?.closest('.reader-page');
  for(const view of views)if(Math.abs(view.number-centre)>2 && view.wrapper!==selectedPage) {
    if(view.pending){view.canvasTask?.cancel();view.textTask?.cancel();}
    else release(view);
  }
  for(const offset of [0,1,-1,2,-2]) {
    if(token!==generation || centre!==currentPage || navigating)return;
    const view=views[centre-1+offset];if(view)await ensureView(view);
  }
  if(token===generation && centre===currentPage)for(const view of views)if(Math.abs(view.number-centre)>2 && !view.pending && view.wrapper!==selectedPage)release(view);
}
function scrolled() {
  scrollFrame=0;if(navigating || !views.length)return;
  const workspace=$('workspace'),target=workspace.getBoundingClientRect().top+Math.min(150,workspace.clientHeight*.3);
  const view=views.find(v=>v.wrapper.getBoundingClientRect().bottom>target) || views.at(-1);
  if(view.number!==currentPage){cancelNoteClick();activate(view);}
  renderNearby();
}
async function renderPage(number, scale, jumpY=null) {
  if(!pdf)return;
  await initialisePages();
  cancelNoteClick();pointerStart=null;
  number=Math.max(1,Math.min(pdf.numPages,number));
  const previous=views[currentPage-1],fraction=jumpY ?? (number===currentPage ? Math.max(0,($('workspace').scrollTop-pageTop(previous)+50)/(previous.height*zoom)):0);
  const topInset=jumpY===0 ? parseFloat(getComputedStyle($('workspace')).paddingTop) || 0 : 50;
  navigating=true;const token=++generation;
  selection=null;window.getSelection()?.removeAllRanges();ready=false;controls();
  for(const view of views){view.canvasTask?.cancel();view.textTask?.cancel();}
  await Promise.all(views.map(view=>view.pending).filter(Boolean));
  if(token!==generation)return;
  const nextZoom=Math.max(.35,Math.min(3,scale));
  if(nextZoom!==zoom){zoom=nextZoom;for(const view of views){release(view);sizeView(view);}}
  const view=views[number-1];activate(view);$('pageNumber').value=number;
  $('workspace').scrollTop=Math.max(0,pageTop(view)+view.height*zoom*fraction-topInset);
  await ensureView(view);if(token!==generation)return;
  $('workspace').scrollTop=Math.max(0,pageTop(view)+view.height*zoom*fraction-topInset);
  navigating=false;ready=true;controls();navigate.draw();renderNearby();
  if(!saving && !noteError)status(!notesReady?'Your notes could not be loaded. Reload notes to retry.':'Select text to copy or highlight. Your notes are private.',!notesReady);
}
function captureSelection() {
  const selected = window.getSelection();
  if (!ready || !selected?.rangeCount || selected.isCollapsed) { selection=null; controls(); return; }
  const wrapper=selected.anchorNode?.parentElement?.closest('.reader-page');
  const layer=wrapper?.querySelector('.textLayer');
  if(!layer){selection=null;controls();return;}
  if (!layer.contains(selected.anchorNode) || !layer.contains(selected.focusNode)) { selection=null; controls(); return; }
  const box = wrapper.getBoundingClientRect();
  const rects = [...selected.getRangeAt(0).getClientRects()].filter(r => r.width > 1 && r.height > 1).map(r => {
    const left=Math.max(box.left,r.left), top=Math.max(box.top,r.top), right=Math.min(box.right,r.right), bottom=Math.min(box.bottom,r.bottom);
    return [(left-box.left)/box.width,(top-box.top)/box.height,(right-left)/box.width,(bottom-top)/box.height];
  }).filter(r => r[2]>0 && r[3]>0);
  const unique = [...new Map(rects.map(r=>[r.map(n=>n.toFixed(5)).join(','),r])).values()];
  const quote = selected.toString().trim();
  selection = unique.length && unique.length <= 200 && quote.length <= 10000 ? {page:Number(wrapper.dataset.page),rects:unique,quote} : null;
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
$('workspace').addEventListener('scroll',()=>{cancelNoteClick();if(selection)captureSelection();if(!scrollFrame)scrollFrame=requestAnimationFrame(scrolled);},{passive:true});
window.addEventListener('resize',()=>{if(selection)captureSelection();});
for(const button of $('selectionTools').querySelectorAll('[data-highlight], #selectionNote')) {
  button.onpointerdown=event=>event.preventDefault();
  button.onclick=()=>{if(selection){const color=button.dataset.highlight || $('color').value;$('color').value=color;change({action:'add',mark:{...selection,color,note:''}},undefined,button.id==='selectionNote');}};
}
$('selectionCopy').onpointerdown=event=>event.preventDefault();
$('selectionCopy').onclick=async()=>{
  if(!selection)return;
  try {await navigator.clipboard.writeText(selection.quote);status('Copied. No highlight was saved.');}
  catch {status('Use Ctrl/Cmd+C to copy the selected text. No highlight was saved.');}
};
$('highlight').onpointerdown = event => event.preventDefault();
$('highlight').onclick = () => { if(selection) change({action:'add',mark:{...selection,color:$('color').value,note:''}}); };
$('previous').onclick = () => renderPage(currentPage-1,zoom);
$('next').onclick = () => renderPage(currentPage+1,zoom);
$('pageNumber').onchange = () => { const value=Number($('pageNumber').value); if(Number.isInteger(value)) renderPage(value,zoom); else controls(); };
$('zoomIn').onclick = () => renderPage(currentPage,zoom+.2);
$('zoomOut').onclick = () => renderPage(currentPage,zoom-.2);
async function fit() { if (!pdf) return; const p = await pdf.getPage(currentPage); const width = p.getViewport({scale:1}).width; const style=getComputedStyle($('workspace')); await renderPage(currentPage,Math.min(2,Math.max(.35,($('workspace').clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight))/width))); }
$('fit').onclick = fit;
$('notesToggle').onclick = () => { $('notesPanel').hidden = !$('notesPanel').hidden; $('notesToggle').setAttribute('aria-expanded',String(!$('notesPanel').hidden)); };
$('theme').onclick = () => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme=next; try {localStorage.setItem('originrag-reader-theme',next);} catch {} };
try {document.documentElement.dataset.theme=localStorage.getItem('originrag-reader-theme') || 'light';} catch {}
$('reload').onclick = async () => {
  if(saving || reloading || !fingerprint)return;
  reloading=true;controls();renderNotes();
  try {applyNotes(await api('GET'));noteError=false;status(dirty()?'Notes refreshed. Your unsaved changes have been kept. Review them before saving.':'Notes refreshed.');}
  catch(e) {status(e.message,true);}
  finally {reloading=false;controls();renderNotes();}
};
const markdownText = text => String(text).replace(/[\\`*_{}\[\]<>()#!|]/g,'\\$&');
$('export').onclick = () => {
  const data = {format:'originrag-reader-notes-v1',paper_id:paperId,fingerprint,exported_at:new Date().toISOString(),annotations:marks};
  const markdown=$('exportFormat').value==='markdown';
  let content=JSON.stringify(data,null,2);
  if(markdown) {
    const lines=[`# ${markdownText($('title').textContent)}`, '', `Paper: ${markdownText(paperId)}`, `PDF fingerprint: ${fingerprint}`, `Exported: ${data.exported_at}`, '', 'Saved highlights and notes. Page numbers refer to PDF pages.', ''];
    for(const mark of [...marks].sort((a,b)=>a.page-b.page || a.rects[0][1]-b.rects[0][1])) {
      lines.push(`## Page ${mark.page} · ${colorNames[mark.color] || 'Highlight'}`, '', ...markdownText(mark.quote || 'Highlighted area').split('\n').map(line=>'> '+line), '');
      if(mark.note)lines.push(markdownText(mark.note),'');
    }
    content=lines.join('\n');
  }
  downloadFile(content,markdown?'text/markdown;charset=utf-8':'application/json','originrag-reading-notes.'+(markdown?'md':'json'));
  status(dirty()?'Exported saved notes only. Save your draft to include it.':'Notes exported.');
};
window.addEventListener('beforeunload',event=>{if(dirty() || saving){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{
  if(event.isComposing || document.querySelector('dialog[open]'))return;
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
    const info=await requestJSON('/api/reader/paper?'+new URLSearchParams({paper_id:paperId}),{timeout:15000}); $('title').textContent=info.title || paperId; $('title').title=$('title').textContent;document.title=(info.title || paperId)+' · OriginRAG Reader';
    void readTracker.init();
    $('downloadPDF').href=original;$('downloadPDF').download=($('title').textContent.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_').slice(0,120) || 'paper')+'.pdf';$('downloadPDF').hidden=false;
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
  } catch(error) {status(error.message || 'Could not open this PDF. Please use Original PDF.',true); if($('empty'))$('empty').textContent='Your original PDF is still available using the link above.';}
}
await start();
