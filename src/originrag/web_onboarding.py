STYLE = """
    .guide-dialog {
      color: var(--text); background: var(--bg); border: 1px solid var(--line);
      border-radius: 20px; padding: 26px; width: min(570px, calc(100vw - 32px));
      max-height: calc(100dvh - 40px); overflow-y: auto; box-sizing: border-box;
    }
    .guide-dialog::backdrop { background: rgba(0,0,0,.65); }
    .guide-dialog h2 { margin: 10px 0; font-size: 25px; }
    .guide-dialog p { color: var(--soft); line-height: 1.65; }
    .guide-dialog button, .library-start button { min-height: 40px; padding: 8px 14px; width: auto; }
    .guide-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
    .guide-count { color: var(--muted); font-size: 12px; }
    .guide-dialog label { display: block; margin: 12px 0 6px; }
    .guide-dialog input, .guide-dialog select {
      width: 100%; padding: 10px; border: 1px solid var(--line); border-radius: 8px;
      background: var(--bg); color: var(--text); box-sizing: border-box;
    }
    .discovery-filters { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .discovery-item { border-top: 1px solid var(--line); padding: 14px 0; }
    .discovery-item a { color: var(--accent); line-height: 1.5; }
    .discovery-item p { font-size: 12px; margin: 5px 0; }
    .library-start { padding: 12px 22px; border-bottom: 1px solid var(--line); }
    .library-start p { margin: 0 0 8px; color: var(--soft); font-size: 13px; line-height: 1.5; }
    .library-start[hidden] { display: none; }
    .library-start button + button { margin-left: 8px; }
    @media (max-width: 480px) { .guide-dialog { padding: 18px; } }
"""

MARKUP = """
  <dialog class="guide-dialog" id="welcomeGuide" aria-labelledby="guideTitle" aria-describedby="guideText">
    <div class="guide-count" id="guideCount"></div>
    <h2 id="guideTitle"></h2>
    <p id="guideText" aria-live="polite"></p>
    <div class="guide-actions">
      <button type="button" id="guideSkip">Skip tour</button>
      <button type="button" id="guideBack">Back</button>
      <button type="button" id="guideNext">Next</button>
    </div>
  </dialog>
  <dialog class="guide-dialog" id="discoveryDialog" aria-labelledby="discoveryTitle" aria-describedby="discoveryPrivacy">
    <h2 id="discoveryTitle">Find papers</h2>
    <p id="discoveryPrivacy">Search across disciplines. Only the search terms and filters are sent to Crossref, not your library or chats.</p>
    <form id="discoveryForm">
      <label for="discoveryExample">Topic examples (optional)</label>
      <select id="discoveryExample">
        <option value="">Your own topic</option>
        <option value="biodiversity ecosystem resilience">Biology &amp; ecology</option>
        <option value="cardiovascular disease prevention">Medicine &amp; health</option>
        <option value="graph algorithms complexity">Computing &amp; mathematics</option>
        <option value="quantum materials superconductivity">Physics &amp; chemistry</option>
        <option value="renewable energy storage">Engineering &amp; energy</option>
        <option value="climate adaptation urban flooding">Earth &amp; environment</option>
        <option value="education inequality social mobility">Social sciences &amp; economics</option>
        <option value="historical linguistics language change">Humanities &amp; languages</option>
      </select>
      <label for="discoveryQuery">Research topic or keywords</label>
      <input id="discoveryQuery" type="search" minlength="2" maxlength="240" required placeholder="Any topic, in your own words" />
      <label for="discoveryKind">Publication type</label>
      <select id="discoveryKind"><option value="journal-article">Journal articles</option><option value="proceedings-article">Conference papers</option><option value="posted-content">Posted content (including preprints)</option><option value="all">All types, including books and chapters</option></select>
      <div class="discovery-filters">
        <div><label for="discoveryYear">Published since</label><input id="discoveryYear" type="number" min="1800" placeholder="Any year" /></div>
        <div><label for="discoveryOrder">Sort by</label><select id="discoveryOrder"><option value="relevance">Relevance</option><option value="newest">Newest first</option></select></div>
      </div>
      <div class="guide-actions"><button type="submit" id="discoverySearch">Search papers</button><button type="button" id="discoveryClose">Close</button></div>
    </form>
    <p id="discoveryStatus" role="status"></p>
    <div id="discoveryResults"></div>
    <p>Results are bibliographic records, not downloaded PDFs. Open a paper to find an accessible copy, then use Add papers. Some publications require institutional access. Coverage varies by field.</p>
    <button type="button" id="discoveryUpload">Add PDFs to library</button>
    <p id="discoveryUploadHint" hidden>Web uploads are not available in this session. Ask the host to enable uploads, or use ./originrag import-local followed by ./originrag prepare-corpus with the web service stopped.</p>
  </dialog>
"""

SCRIPT = r"""
<script>
(() => {
  const el = (id) => document.getElementById(id);
  const guide = el("welcomeGuide"), discovery = el("discoveryDialog");
  let storageKey = null, step = 0, stats = null, uploadAvailable = false;
  let returnFocus = null, searchController = null, latestSearch = 0;
  const steps = [
    ["Your papers, one place", "Add papers uploads PDFs to the shared library. Accepted files are checked for identifiers and duplicates, named and sorted, then indexed automatically. Watch the update status. Scanned or unrecognised files may need attention."],
    ["Find your next paper", "Find papers searches external literature by any topic, with year and sorting options. Open a result to obtain an accessible PDF, then add it here. Library searches the papers already in this collection by author, title or DOI."],
    ["Ask and continue", "Type a question in the chat, then ask follow-up questions in the same conversation. Saved chats are in the sidebar. The queue shows who is waiting, and running work continues when you switch chats."],
    ["Follow the evidence", "Open citations to inspect the source passages. Study overlap is checked automatically: several papers may reuse one study or dataset. An unknown independent-study count means the overlap is not yet resolved, not that every paper is independent."],
    ["Make it yours", "Choose a light or dark theme and wallpaper in Appearance. Answer style adjusts how replies are written. Use ? to reopen this tour whenever you need it."]
  ];
  const banner = document.createElement("section");
  banner.className = "library-start"; banner.hidden = true;
  banner.setAttribute("aria-label", "Getting your library ready");
  const bannerText = document.createElement("p");
  const add = document.createElement("button"); add.type = "button"; add.textContent = "Add papers";
  banner.append(bannerText, add);
  el("chat").before(banner);
  const findButton = document.createElement("button");
  findButton.type = "button"; findButton.id = "discoveryButton";
  findButton.className = "header-button"; findButton.textContent = "Find papers";
  findButton.setAttribute("aria-haspopup", "dialog"); findButton.setAttribute("aria-controls", "discoveryDialog");
  document.querySelector("header .header-left").append(findButton);
  const replay = document.createElement("button"); replay.type = "button";
  replay.id = "guideReplay"; replay.textContent = "Quick tour";
  el("aboutClose").before(replay);
  function updateBanner(data) {
    stats = data;
    const ready = Number(data.searchable || 0), total = Number(data.total || 0), pdfs = Number(data.local_pdf || 0);
    banner.hidden = ready > 0;
    add.hidden = !uploadAvailable;
    if (ready > 0) return;
    bannerText.textContent = total === 0
      ? "Your library is empty. Add article PDFs to start asking questions. Accepted uploads are organised and indexed automatically."
      : pdfs === 0
        ? "You can browse the catalogue, but no full-text papers are ready for chat. Add PDFs; accepted uploads are organised and indexed automatically."
        : "PDFs are present, but no papers are ready for chat yet. Check the library update status; files may still need processing or attention.";
    if (!uploadAvailable) bannerText.textContent += " Ask the host to import PDFs or enable web uploads.";
  }
  function renderStep() {
    el("guideCount").textContent = `${step + 1} of ${steps.length}`;
    el("guideTitle").textContent = steps[step][0];
    el("guideText").textContent = steps[step][1];
    if (step === 0 && stats && !Number(stats.searchable || 0)) {
      const intro = Number(stats.total || 0) === 0 ? "Your library is empty. " : "No full-text papers are ready for chat yet. ";
      el("guideText").textContent = intro + (uploadAvailable ? steps[0][1] : "Ask the host to import article PDFs or enable web uploads. Once accepted PDFs are indexed, they become available for answers.");
    }
    el("guideBack").hidden = step === 0;
    el("guideNext").textContent = step === steps.length - 1 ? "Start exploring" : "Next";
  }
  function openGuide() {
    if (guide.open) return;
    returnFocus = document.activeElement; step = 0; renderStep(); guide.showModal(); el("guideNext").focus();
  }
  function finishGuide() {
    try { if (storageKey) localStorage.setItem(storageKey, "done"); } catch {}
    guide.close();
    if (returnFocus?.isConnected) returnFocus.focus();
  }
  el("guideSkip").addEventListener("click", finishGuide);
  guide.addEventListener("cancel", (event) => { event.preventDefault(); finishGuide(); });
  el("guideBack").addEventListener("click", () => { step = Math.max(0, step - 1); renderStep(); });
  el("guideNext").addEventListener("click", () => { if (step === steps.length - 1) finishGuide(); else { step++; renderStep(); } });
  replay.addEventListener("click", () => { el("aboutClose").click(); openGuide(); });
  function openDiscovery() {
    if (discovery.open) return;
    returnFocus = document.activeElement;
    el("discoveryUpload").hidden = !uploadAvailable;
    el("discoveryUploadHint").hidden = uploadAvailable;
    discovery.showModal(); el("discoveryQuery").focus();
  }
  function closeDiscovery() { discovery.close(); }
  discovery.addEventListener("close", () => {
    if (el("discoverySearch").disabled) el("discoveryStatus").textContent = "Search cancelled.";
    latestSearch++; searchController?.abort(); el("discoverySearch").disabled = false;
    if (returnFocus?.isConnected) returnFocus.focus();
  });
  findButton.addEventListener("click", openDiscovery);
  el("discoveryClose").addEventListener("click", closeDiscovery);
  const openUpload = () => { if (uploadAvailable) el("paperUploadButton").click(); };
  add.addEventListener("click", openUpload);
  el("discoveryUpload").addEventListener("click", () => { closeDiscovery(); openUpload(); });
  el("discoveryExample").addEventListener("change", (event) => {
    if (event.target.value) el("discoveryQuery").value = event.target.value;
    el("discoveryQuery").focus();
  });
  el("discoveryYear").max = String(new Date().getFullYear());
  el("discoveryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const serial = ++latestSearch;
    searchController?.abort(); searchController = new AbortController();
    el("discoverySearch").disabled = true;
    el("discoveryResults").replaceChildren(); el("discoveryStatus").textContent = "Searching literature…";
    const params = new URLSearchParams({query: el("discoveryQuery").value.trim(), since: el("discoveryYear").value, order: el("discoveryOrder").value, kind: el("discoveryKind").value});
    try {
      const response = await fetch(`/api/discover-papers?${params}`, {signal: searchController.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Search unavailable.");
      if (serial !== latestSearch) return;
      el("discoveryStatus").textContent = data.items.length ? `${data.items.length} ${data.items.length === 1 ? "result" : "results"} from Crossref` : "No matches found. Try broader terms or an earlier year.";
      for (const paper of data.items) {
        const row = document.createElement("article"); row.className = "discovery-item";
        const title = document.createElement("a"); title.textContent = paper.title;
        const url = new URL(paper.url);
        if (url.protocol !== "https:" || url.hostname !== "doi.org") continue;
        title.href = url.href; title.target = "_blank"; title.rel = "noopener noreferrer";
        const detail = document.createElement("p");
        detail.textContent = [paper.author, paper.year, paper.journal, paper.type].filter(Boolean).join(" · ");
        const availability = document.createElement("p");
        availability.textContent = paper.in_library ? "Already in the catalogue · open Library to check full-text availability" : "Open publication · access may require a subscription";
        row.append(title, detail, availability); el("discoveryResults").append(row);
      }
    } catch (error) {
      if (serial === latestSearch) el("discoveryStatus").textContent = error.name === "AbortError" ? "Search cancelled." : error.message;
    } finally { if (serial === latestSearch) el("discoverySearch").disabled = false; }
  });
  window.addEventListener("originrag:library-stats", (event) => updateBanner(event.detail));
  async function start() {
    try {
      const responses = await Promise.all([fetch("/api/status"), fetch("/api/library-stats"), fetch("/api/library-status")]);
      if (responses.some((response) => !response.ok)) return;
      const [status, data, library] = await Promise.all(responses.map((response) => response.json()));
      uploadAvailable = Boolean(status.user) && library.state !== "unavailable";
      updateBanner(data);
      storageKey = `originrag.quick-tour.v1.${status.user?.user_id || "local"}`;
      let seen = false;
      try { seen = localStorage.getItem(storageKey) === "done"; } catch {}
      if (!seen && !document.querySelector("dialog[open], .about-backdrop.open, .paper-upload-backdrop.open")) openGuide();
    } catch {}
  }
  start();
})();
</script>
"""


def with_onboarding(html: str) -> str:
    return (html.replace("</style>", STYLE + "</style>", 1)
            .replace("</body>", MARKUP + SCRIPT + "</body>", 1))
