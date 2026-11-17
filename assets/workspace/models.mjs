import {requestJSON} from './request.mjs';

const $ = id => document.getElementById(id);
const dialog = $('modelDialog');
let snapshot = null, timer = null, loading = false, mutating = false;
let searchTimer = null, searchController = null, searchVersion = 0, searchResults = null, searchPending = false;
const size = bytes => Number.isFinite(bytes) && bytes > 0 ? `${(bytes / 1e9).toFixed(1)} GB` : 'size unknown';
const memory = bytes => Number.isFinite(bytes) ? `${(bytes / 1024 ** 3).toFixed(0)} GiB` : 'unknown';
const fitLabels = {fits: 'Likely fits', loaded: 'Running', tight: 'Check free memory',
  hybrid: 'GPU + RAM · slower', too_large: 'Likely too large', unknown: 'Fit unknown'};

function renderHardware() {
  const host = snapshot.hardware || {};
  const main = document.createElement('strong');
  main.textContent = 'This host · ' + (host.unified_memory
    ? `${memory(host.ram_total)} shared CPU / GPU memory`
    : `${memory(host.ram_total)} RAM${host.gpus?.some(gpu => gpu.total) ? ` · ${host.gpus.filter(gpu => gpu.total).map(gpu => `${memory(gpu.total)} VRAM`).join(' / ')}` : ''}`);
  const detail = document.createElement('small');
  detail.textContent = `${host.gpus?.map(gpu => gpu.name).join(', ') || 'GPU not detected'} · about ${memory(host.ram_available)} RAM available now`;
  $('modelHardware').replaceChildren(main, detail);
}

function render() {
  if (!snapshot) return;
  const provider = $('modelProvider').value;
  const busy = snapshot.job?.status === 'running';
  const disabled = !snapshot.can_use || busy || mutating || snapshot.active_requests > 0;
  const downloadDisabled = !snapshot.can_download || busy || mutating || snapshot.active_requests > 0;
  $('modelMemoryLabel').hidden = provider !== 'colibri';
  const configured = snapshot.configured_selection || snapshot.selection || snapshot.default;
  const active = snapshot.effective_selection;
  const configuredRow = snapshot.models.find(row => row.model === configured?.model && row.provider === configured?.provider);
  $('modelSelection').textContent = snapshot.selection_error ? 'Check model selection'
    : active ? `Using ${active.model}`
      : configuredRow?.runtime_reachable === false ? 'Model service offline' : 'No model ready';
  const configuredLabel = $('modelConfigured');
  if (configuredLabel) configuredLabel.textContent = snapshot.default ? `Configured default: ${snapshot.default.model}` : '';
  for (const id of ['chatModels', 'quillModels']) {
    const trigger = $(id);
    if (trigger) trigger.title = active ? `Selected model: ${active.model}. Manage models.` : 'No model ready. Choose or download a model.';
  }
  renderHardware();
  $('modelAccess').textContent = snapshot.active_requests
    ? 'An answer is running. Wait before changing models.'
    : `${snapshot.local_token ? 'Local workspace.' : 'Signed-in users can use installed models.'} Selection applies to new requests across this host, including Quill. ${snapshot.can_download ? 'You can download models.' : 'Downloads require an authorised account.'}`;
  $('modelWarning').textContent = snapshot.selection_error || (provider === 'ollama' ? snapshot.ollama_error || ''
    : !snapshot.colibri_available ? 'The host has not installed Colibri yet. See the model setup guide.'
      : 'Colibri is experimental. File size does not predict its streaming performance or cache requirements.');
  const query = $('modelSearch').value.trim().toLowerCase(), filter = $('modelFilter').value;
  const catalogue = filter === 'search' && provider === 'ollama';
  $('modelSearchStatus').textContent = catalogue
    ? searchPending ? 'Searching…'
      : query.length < 2 ? 'Enter a model name.'
        : searchResults ? `${searchResults.length} result${searchResults.length === 1 ? '' : 's'}` : ''
    : '';
  const rows = [];
  const expanded = new Set([...$('modelList').querySelectorAll('.model-row')]
    .filter(row => row.querySelector('details')?.open).map(row => row.dataset.key));
  const source = catalogue ? searchResults || [] : snapshot.models;
  for (const model of source.filter(row => row.provider === provider
      && (catalogue || filter === 'all' && (row.installed || row.recommended || provider === 'colibri')
        || filter === 'installed' && row.installed || filter === 'recommended' && row.recommended
        || filter === 'available' && !row.installed && row.downloadable)
      && (catalogue || `${row.name} ${row.model}`.toLowerCase().includes(query)))) {
    const row = document.createElement('article'); row.className = 'model-row';
    row.dataset.key = `${model.provider}:${model.model}`;
    const info = document.createElement('div'); info.className = 'model-row-info';
    const name = document.createElement('strong'); name.textContent = model.name;
    const meta = document.createElement('div'); meta.className = 'model-meta';
    const status = document.createElement('span');
    status.textContent = `${model.installed ? 'Installed' : model.runtime_reachable === false ? 'Not verified' : 'Not installed'} · ${size(model.bytes)}`;
    meta.append(status);
    if (model.recommended && !model.installed) {
      const suggested = document.createElement('span'); suggested.className = 'model-suggested'; suggested.textContent = 'Suggested'; meta.append(suggested);
    }
    const fit = document.createElement('span'); fit.className = `model-fit model-fit-${model.fit?.status || 'unknown'}`;
    fit.textContent = fitLabels[model.fit?.status] || 'Fit unknown';
    meta.append(fit); info.append(name, meta);
    const details = document.createElement('details'); details.className = 'model-details';
    details.open = expanded.has(row.dataset.key);
    const summary = document.createElement('summary'); summary.textContent = 'Details'; details.append(summary);
    const technical = document.createElement('small');
    technical.textContent = `${model.provider}${model.purpose ? ` · ${model.purpose}` : ''}${model.fit?.estimated_max ? ` · estimated memory ${size(model.fit.estimated_min)}–${size(model.fit.estimated_max)} at 16K context` : ''}${model.loaded_context ? ` · running context ${model.loaded_context.toLocaleString()}` : ''}${model.default ? ' · configured default' : ''}`;
    details.append(technical);
    const placement = document.createElement('small');
    placement.textContent = model.loaded ? `Running on ${model.device || 'the runtime'}`
      : model.loaded_verified === false ? 'Running status unknown'
        : `${model.fit?.label || 'Memory fit unknown'}${model.fit?.device ? ' · ' + model.fit.device : ''}${model.installed ? ' · loads when needed' : ''}`;
    details.append(placement);
    if (model.measurement) {
      const stat = document.createElement('small');
      stat.textContent = `${model.measurement.tokens_per_second} tokens/s · ${model.measurement.metric === 'decode' ? 'decode only' : 'whole request'} · measured ${new Date(model.measurement.measured_at * 1000).toLocaleString()}`;
      details.append(stat);
    }
    if (model.note) {
      const note = document.createElement('small'); note.textContent = model.note; details.append(note);
    }
    if (model.source_url) {
      const link = document.createElement('a'); link.href = model.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Model information'; details.append(link);
    }
    const actions = document.createElement('div'); actions.className = 'model-row-actions';
    const extra = document.createElement('div'); extra.className = 'model-row-actions';
    const button = (label, action, parent = actions) => {
      const element = document.createElement('button'); element.type = 'button'; element.textContent = label;
      element.disabled = action === 'download' ? downloadDisabled : disabled || (action === 'select' && model.selected);
      if (action === 'download') element.disabled ||= model.runtime_reachable === false;
      if (action === 'delete') { element.className = 'model-delete'; element.disabled ||= !snapshot.can_manage || !model.deletable; }
      if (action === 'delete' && !model.deletable) element.title = 'Selected, default, loaded or externally managed models are protected.';
      element.onclick = () => change(action, model); parent.append(element);
    };
    if (model.installed) {
      button(model.selected ? 'Selected' : 'Use model', 'select'); button('Test speed', 'benchmark', extra);
      if (snapshot.can_manage) button('Delete download', 'delete', extra);
    } else if (model.downloadable) button('Download', 'download');
    details.append(extra); info.append(details);
    row.append(info, actions); rows.push(row);
  }
  $('modelList').replaceChildren(...rows);
  if (!rows.length) $('modelList').textContent = searchPending ? 'Searching…'
    : filter === 'installed' ? 'No installed models.'
      : filter === 'recommended' ? 'No suggestions right now.' : 'No models found. Try a search.';
  $('modelDefault').disabled = disabled;
  $('modelStop').disabled = !snapshot.can_manage || disabled;
  $('modelDefault').hidden = !snapshot.can_use;
  $('modelStop').hidden = !snapshot.can_manage || provider !== 'colibri';
  $('modelStop').hidden ||= snapshot.models.some(model => model.shared_service && model.loaded)
    && !snapshot.models.some(model => model.provider === 'colibri' && !model.shared_service && model.loaded);
  $('modelJob').hidden = !busy && snapshot.job?.status !== 'error';
  if (snapshot.job) {
    $('modelJobText').textContent = snapshot.job.message;
    const progress = $('modelProgress'); progress.hidden = !busy;
    if (snapshot.job.total > 0) { progress.max = snapshot.job.total; progress.value = snapshot.job.completed; }
    else progress.removeAttribute('value');
    $('modelCancel').hidden = !busy || snapshot.job.action !== 'download' || !snapshot.can_download;
    $('modelCancel').disabled = mutating;
  }
}

function syncSearchRows() {
  if (!searchResults || !snapshot) return;
  searchResults = searchResults.map(model => snapshot.models.find(row => row.provider === model.provider && row.model === model.model) || model);
}

async function refresh() {
  if (loading || !dialog.open) return;
  loading = true;
  try {
    const first = !snapshot;
    snapshot = await requestJSON('/api/models', {timeout:25000});
    if (first && snapshot.configured_selection) $('modelProvider').value = snapshot.configured_selection.provider;
    syncSearchRows(); render();
  } catch (error) { $('modelError').textContent = error.message; }
  finally {
    loading = false; clearTimeout(timer);
    if (dialog.open) timer = setTimeout(refresh, snapshot?.job?.status === 'running' ? 2000 : 10000);
  }
}

function cancelSearch() {
  clearTimeout(searchTimer); searchController?.abort(); searchVersion++; searchPending = false;
}

function scheduleSearch() {
  cancelSearch(); searchResults = null; $('modelError').textContent = '';
  const query = $('modelSearch').value.trim();
  if ($('modelProvider').value !== 'ollama' || $('modelFilter').value !== 'search' || query.length < 2) { render(); return; }
  searchPending = true; render();
  const version = searchVersion;
  searchTimer = setTimeout(async () => {
    searchController = new AbortController();
    try {
      const data = await requestJSON(`/api/models?search=${encodeURIComponent(query)}`, {signal:searchController.signal, timeout:25000});
      if (version !== searchVersion || !dialog.open) return;
      snapshot = data; searchResults = data.models.filter(row => data.search_models?.includes(row.model) && row.provider === 'ollama');
    } catch (error) {
      if (version === searchVersion && error.name !== 'AbortError') $('modelError').textContent = `Catalogue search: ${error.message} Installed models remain available.`;
    } finally {
      if (version === searchVersion) { searchPending = false; render(); }
    }
  }, 550);
}

async function change(action, model = {}) {
  if (mutating) return;
  if (action === 'download' && !confirm(`Download ${model.name} (approximately ${size(model.bytes)}) on the model host? ${model.fit?.label || ''}. This does not select the model. Allow additional free disk space for installation.`)) return;
  if (action === 'select' && !confirm(`Use ${model.name} for new OriginRAG requests on this host?`)) return;
  if (action === 'delete' && !confirm(`Delete the downloaded model “${model.name}” from the configured runtime? This affects all users of that runtime. Papers and manuscripts are not removed. You will need to download it again to use it later.`)) return;
  mutating = true; $('modelError').textContent = ''; render();
  try {
    const csrf = document.cookie.split('; ').find(v => v.startsWith('pfempire_csrf='))?.split('=')[1];
    await requestJSON('/api/models', {
      payload:{action, provider:model.provider, model:model.model, mode:$('modelMemory').value, confirmed:['download', 'delete'].includes(action),
        ...(model.catalog_digest ? {catalog_digest:model.catalog_digest} : {})},
      headers:{...(csrf ? {'X-CSRF-Token':decodeURIComponent(csrf)} : {}),
               ...(snapshot?.local_token ? {'X-OriginRAG-Local-Token':snapshot.local_token} : {})},
    });
    await refresh();
  } catch (error) { $('modelError').textContent = error.message; }
  finally { mutating = false; render(); }
}

export function openModels() {
  $('globalSettingsDialog').close(); $('modelError').textContent = '';
  if (!dialog.open) dialog.showModal();
  if (snapshot?.configured_selection) $('modelProvider').value = snapshot.configured_selection.provider;
  refresh();
  if ($('modelFilter').value === 'search') scheduleSearch();
}
$('modelClose').onclick = () => dialog.close();
dialog.addEventListener('close', () => { clearTimeout(timer); cancelSearch(); });
$('modelProvider').onchange = scheduleSearch;
$('modelFilter').onchange = scheduleSearch;
$('modelSearch').oninput = () => {
  if ($('modelFilter').value !== 'installed' && $('modelProvider').value === 'ollama' && $('modelSearch').value.trim().length >= 2) $('modelFilter').value = 'search';
  scheduleSearch();
};
$('modelRefresh').onclick = () => { refresh(); if ($('modelFilter').value === 'search') scheduleSearch(); };
$('modelCancel').onclick = () => change('cancel');
$('modelDefault').onclick = () => change('default');
$('modelStop').onclick = () => change('stop');
