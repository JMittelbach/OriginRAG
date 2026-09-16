const $ = id => document.getElementById(id);

export function searchable(parts, withLocations=true) {
  let text = '';
  const locations = [];
  parts.forEach((part, index) => {
    if (index && !text.endsWith(' ')) {text += ' '; if(withLocations)locations.push(null);}
    for (let offset = 0; offset < part.length;) {
      const character = String.fromCodePoint(part.codePointAt(offset));
      const lowered = character.toLowerCase();
      if (/\s/u.test(character)) {
        if (!text.endsWith(' ')) {text += ' '; if(withLocations)locations.push({index, offset, length:character.length});}
      } else if (character !== '\u00ad') {
        text += lowered;
        if(withLocations)for (let i=0;i<lowered.length;i++) locations.push({index, offset, length:character.length});
      }
      offset += character.length;
    }
  });
  return {text, locations};
}
export function matches(text, query, limit=1000) {
  const found=[];
  if (!query) return found;
  let from=0;
  while(found.length<limit) {const at=text.indexOf(query,from);if(at<0)break;found.push(at);from=at+query.length;}
  return found;
}

export function navigation({getPdf, getPage, getZoom, renderPage}) {
  let sequence=0, query='', hits=[], active=-1, searching=false, incomplete=false;
  const cache=new Map();
  function summary() {
    $('searchPrevious').disabled=$('searchNext').disabled=!hits.length || searching;
    $('searchStatus').textContent=searching?'Searching…':!query?'':hits.length?`${active+1} / ${hits.length}${incomplete?' (partial results)':''}`:incomplete?'Search incomplete; no matches on checked pages.':'No matches in selectable text.';
  }
  function draw() {
    const overlay=$('searchHighlights'); overlay.replaceChildren();
    if (!query || $('searchBar').hidden) return;
    const spans=[...$('textLayer').querySelectorAll('span:not(.markedContent)')].filter(el=>el.firstChild?.nodeType===Node.TEXT_NODE);
    const {text, locations}=searchable(spans.map(el=>el.textContent));
    const pageHits=matches(text,query);
    const ordinal=active<0?-1:hits.slice(0,active).filter(h=>h.page===getPage()).length;
    const box=$('page').getBoundingClientRect();
    pageHits.forEach((at,index)=>{
      const first=locations[at],last=locations[at+query.length-1];
      if(!first || !last)return;
      const range=document.createRange();
      range.setStart(spans[first.index].firstChild,first.offset);
      range.setEnd(spans[last.index].firstChild,last.offset+last.length);
      for(const rect of range.getClientRects()) {
        if(rect.width<=0 || rect.height<=0)continue;
        const mark=document.createElement('div');mark.className='search-hit'+(hits[active]?.page===getPage() && ordinal===index?' active':'');
        Object.assign(mark.style,{left:(rect.left-box.left)+'px',top:(rect.top-box.top)+'px',width:rect.width+'px',height:rect.height+'px'});overlay.append(mark);
      }
    });
  }
  async function jump(index) {
    if(!hits.length || searching)return;
    active=(index+hits.length)%hits.length; summary();
    const token=sequence, chosen=active;
    await renderPage(hits[active].page,getZoom());
    if(token!==sequence || chosen!==active)return;
    draw();
    const mark=document.querySelector('#searchHighlights .active');
    if(mark) $('workspace').scrollTop=Math.max(0,mark.offsetTop-80);
  }
  async function search() {
    const token=++sequence;
    query=searchable([$('searchQuery').value]).text.trim(); hits=[];active=-1;incomplete=false;
    draw();
    if(!query || !getPdf()) {searching=false;summary();return;}
    searching=true;summary();
    const document=getPdf();
    for(let page=1;page<=Math.min(document.numPages,2000);page++) {
      if(token!==sequence)return;
      $('searchStatus').textContent=`Searching page ${page} / ${document.numPages}…`;
      try {
        let text=cache.get(page);
        if(text===undefined) {
          const pdfPage=await document.getPage(page);
          const content=await pdfPage.getTextContent();
          if(token!==sequence)return;
          text=searchable(content.items.filter(item=>typeof item.str==='string').map(item=>item.str),false).text;
          if(text.length>1000000){text=text.slice(0,1000000);incomplete=true;}
          else {if(cache.size>=32)cache.delete(cache.keys().next().value);cache.set(page,text);}
        }
        for(const offset of matches(text,query,1000-hits.length)) hits.push({page,offset});
        if(hits.length>=1000){incomplete=true;break;}
      } catch {if(token!==sequence)return;incomplete=true;}
      await new Promise(resolve=>setTimeout(resolve,0));
    }
    if(token!==sequence)return;
    if(document.numPages>2000)incomplete=true;
    searching=false;summary();
    if(hits.length)await jump(0);
  }
  function close() {sequence++;searching=false;query='';hits=[];active=-1;$('searchBar').hidden=true;draw();summary();$('find').focus();}
  function open() {$('searchBar').hidden=false;$('searchQuery').focus();$('searchQuery').select();}
  $('find').onclick=open;
  $('searchBar').onsubmit=event=>{event.preventDefault();search();};
  $('searchQuery').oninput=()=>{sequence++;searching=false;query='';hits=[];active=-1;draw();summary();};
  $('searchPrevious').onclick=()=>jump(active-1);$('searchNext').onclick=()=>jump(active+1);
  $('searchClose').onclick=close;
  $('searchQuery').onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();close();}else if(event.key==='Enter' && query && !searching){event.preventDefault();jump(active+(event.shiftKey?-1:1));}};
  $('contents').onclick=async()=>{
    const container=$('outlineItems');container.textContent='Loading contents…';$('outlineDialog').showModal();
    try {
      const outline=await getPdf().getOutline();container.replaceChildren();
      let count=0;
      function append(items,depth=0) {
        if(depth>20)return;
        for(const item of items || []) {
          if(++count>500)return;
          const button=document.createElement('button');button.textContent=item.title || 'Untitled section';button.style.paddingLeft=(12+depth*14)+'px';button.disabled=!item.dest;
          button.onclick=async()=>{
            try {const dest=typeof item.dest==='string'?await getPdf().getDestination(item.dest):item.dest;
              if(!Array.isArray(dest) || !dest.length)throw new Error();
              const number=typeof dest[0]==='number'?dest[0]:await getPdf().getPageIndex(dest[0]);
              $('outlineDialog').close();await renderPage(number+1,getZoom());
            }catch {container.prepend(Object.assign(document.createElement('p'),{textContent:'This section could not be located. Use the page controls instead.'}));}
          };container.append(button);append(item.items,depth+1);
        }
      }
      append(outline);
      if(!count)container.textContent='This PDF has no embedded table of contents. Use Find or the page controls to navigate.';
      else if(count>500)container.append(Object.assign(document.createElement('p'),{textContent:'Showing the first 500 entries.'}));
    } catch {container.textContent='Contents are unavailable for this PDF.';}
  };
  $('outlineClose').onclick=()=>$('outlineDialog').close();
  return {draw,open,close};
}
