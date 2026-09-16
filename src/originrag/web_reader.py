HTML = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reader · OriginRAG</title>
<link rel="stylesheet" href="/assets/vendor/pdfjs/web/pdf_viewer.css">
<link rel="stylesheet" href="/assets/reader/reader.css">
</head><body>
<header><a class="brand" href="/">OriginRAG <span>/ Reader</span></a><h1 id="title">Your reading space</h1><a id="original" target="_blank" rel="noopener noreferrer" hidden>Original PDF ↗</a></header>
<nav aria-label="Reading controls">
<div class="controls"><button id="previous" aria-label="Previous page" disabled>←</button><label>Page <input id="pageNumber" type="number" min="1" value="1" disabled></label><span id="pageCount">/ —</span><button id="next" aria-label="Next page" disabled>→</button></div>
<div class="controls"><button id="zoomOut" aria-label="Zoom out" disabled>−</button><output id="zoomLabel">100%</output><button id="zoomIn" aria-label="Zoom in" disabled>+</button><button id="fit" disabled>Fit width</button><button id="find" title="Find in this PDF (Ctrl/Cmd+F)" disabled>Find</button><button id="contents" disabled>Contents</button></div>
<div class="controls"><label class="sr-only" for="color">Highlight color</label><select id="color"><option value="yellow">Butter yellow</option><option value="mint">Soft mint</option><option value="rose">Rose</option></select><button id="highlight" class="primary" disabled>Highlight selection</button><button id="notesToggle" aria-expanded="true">Notes</button><button id="theme" aria-label="Change reading theme">◐</button></div>
</nav>
<form id="searchBar" role="search" hidden><label for="searchQuery">Find in paper</label><input id="searchQuery" type="search" maxlength="200" autocomplete="off" placeholder="Word or phrase…"><button type="submit">Search</button><button type="button" id="searchPrevious" aria-label="Previous match" disabled>↑</button><button type="button" id="searchNext" aria-label="Next match" disabled>↓</button><output id="searchStatus" aria-live="polite"></output><button type="button" id="searchClose" aria-label="Close search">×</button></form>
<div id="status" role="status" aria-live="polite">Opening your paper…</div>
<main><section id="workspace" aria-label="PDF page"><div id="page"><canvas id="canvas"></canvas><div id="highlights" aria-hidden="true"></div><div id="searchHighlights" aria-hidden="true"></div><div id="textLayer" class="textLayer"></div></div><div id="empty">A little space to read, think, and make connections.</div></section>
<aside id="notesPanel" aria-label="Personal notes"><div class="notes-heading"><div><p class="eyebrow">YOUR MARGINS</p><h2>Thoughts & highlights</h2></div><button id="reload" title="Reload saved notes" disabled>↻</button></div><p class="hint">Select a passage, then highlight it. Add a thought below. Your original paper stays untouched.</p><p id="versionWarning" class="warning" hidden></p><div id="notes"></div><button id="export" disabled>Export notes</button></aside></main>
<div id="selectionTools" role="toolbar" aria-label="Selected passage" hidden><button data-highlight="yellow" aria-label="Highlight in yellow" title="Yellow highlight (H)">●</button><button data-highlight="mint" aria-label="Highlight in mint" title="Mint highlight">●</button><button data-highlight="rose" aria-label="Highlight in rose" title="Rose highlight">●</button><button id="selectionNote">+ Note</button></div>
<dialog id="outlineDialog" aria-labelledby="outlineTitle"><div class="notes-heading"><h2 id="outlineTitle">Contents</h2><button id="outlineClose" aria-label="Close contents">Close</button></div><div id="outlineItems"></div></dialog>
<script type="module" src="/assets/reader/reader.mjs"></script></body></html>'''
