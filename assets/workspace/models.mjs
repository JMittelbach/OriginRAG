import {requestJSON} from './request.mjs';

const $ = id => document.getElementById(id);
const dialog = $('modelDialog');
let snapshot = null, timer = null, loading = false, mutating = false;
const size = bytes => bytes ? `${(bytes / 1e9).toFixed(1)} GB` : '';

function render() {
  if (!snapshot) return;
  const provider = $('modelProvider').value;
  const busy = snapshot.job?.status === 'running';
  const disabled = !snapshot.can_use || busy || mutating || snapshot.active_requests > 0;
  const downloadDisabled = !snapshot.can_download || busy || mutating || snapshot.active_requests > 0;
  $('modelMemoryLabel').hidden = provider !== 'colibri';
  $('modelSelection').textContent = snapshot.selection
    ? `Selected: ${snapshot.selection.model} · ${snapshot.selection.provider}`
    : 'Using the configured default model';
  $('modelAccess').textContent = snapshot.active_requests
    ? 'An answer is running. Model changes will be available when it finishes.'
    : `Everyone signed in can use installed models. Selection changes apply to all new requests on this host. ${snapshot.can_download ? 'You can download new models.' : 'New downloads require an authorised account.'}`;
  $('modelWarning').textContent = snapshot.selection_error || (provider === 'ollama' ? snapshot.ollama_error || ''
    : !snapshot.colibri_available ? 'The host has not installed Colibri yet. See the model setup guide.'
      : 'Colibri supports specific model formats. Smaller caches save process memory but can make answers slower. Check each model’s context limit.');
  const rows = [];
  for (const model of snapshot.models.filter(row => row.provider === provider)) {
    const row = document.createElement('div'); row.className = 'model-row';
    const info = document.createElement('div'); info.className = 'model-row-info';
    const name = document.createElement('strong'); name.textContent = model.name;
    const status = document.createElement('small');
    status.textContent = `${model.loaded ? 'Loaded' : model.installed ? 'On this host' : 'Not downloaded'} · ${size(model.bytes)}${model.context ? ` · ${model.context.toLocaleString()} context` : ''}`;
    if (model.accelerator) status.textContent += ` · ${model.accelerator.startsWith('cuda') ? 'GPU + RAM' : 'CPU / RAM'}`;
    if (model.shared_service && !model.loaded) status.textContent += ' · loads on first request';
    info.append(name, status);
    if (model.measurement) {
      const stat = document.createElement('small');
      stat.textContent = `${model.measurement.tokens_per_second} tokens/s · ${model.measurement.metric === 'decode' ? 'decode' : 'whole request'} · last measured ${new Date(model.measurement.measured_at * 1000).toLocaleTimeString()}`;
      info.append(stat);
    }
    if (model.note) {
      const note = document.createElement('small'); note.textContent = model.note; info.append(note);
    }
    const actions = document.createElement('div'); actions.className = 'model-row-actions';
    const button = (label, action) => {
      const element = document.createElement('button'); element.type = 'button'; element.textContent = label;
      element.disabled = action === 'download' ? downloadDisabled : disabled;
      element.onclick = () => change(action, model);
      actions.append(element);
    };
    if (model.installed) { button('Use model', 'select'); button('Test speed', 'benchmark'); }
    else if (model.downloadable) button('Download', 'download');
    row.append(info, actions); rows.push(row);
  }
  $('modelList').replaceChildren(...rows);
  $('modelDefault').disabled = disabled;
  $('modelStop').disabled = !snapshot.can_manage || disabled;
  $('modelDefault').hidden = !snapshot.can_use;
  $('modelStop').hidden = !snapshot.can_manage;
  $('modelStop').hidden ||= provider !== 'colibri';
  $('modelStop').hidden ||= snapshot.models.some(model => model.shared_service && model.loaded)
    && !snapshot.models.some(model => model.provider === 'colibri' && !model.shared_service && model.loaded);
  $('modelJob').hidden = !snapshot.job;
  if (snapshot.job) {
    $('modelJobText').textContent = `${snapshot.job.status}: ${snapshot.job.message}`;
    const progress = $('modelProgress');
    progress.hidden = !busy;
    if (snapshot.job.total > 0) { progress.max = snapshot.job.total; progress.value = snapshot.job.completed; }
    else progress.removeAttribute('value');
    $('modelCancel').hidden = !busy || snapshot.job.action !== 'download' || !snapshot.can_download;
    $('modelCancel').disabled = mutating;
  }
}

async function refresh() {
  if (loading || !dialog.open) return;
  loading = true;
  try {
    snapshot = await requestJSON('/api/models', {timeout:15000});
    render();
  } catch (error) { $('modelError').textContent = error.message; }
  finally {
    loading = false;
    clearTimeout(timer);
    if (dialog.open) timer = setTimeout(refresh, snapshot?.job?.status === 'running' ? 2000 : 10000);
  }
}

async function change(action, model = {}) {
  if (mutating) return;
  if (action === 'download' && !confirm(`Download ${model.name} (${size(model.bytes)}) on this host?${model.model === 'olmoe-1b-7b' ? ' Conversion requires at least 15 GB free disk space.' : ' Allow at least 5 GB additional free disk space.'}`)) return;
  if (action === 'select' && !confirm(`Use ${model.name} for new OriginRAG requests on this host?`)) return;
  mutating = true; $('modelError').textContent = ''; render();
  try {
    const csrf = document.cookie.split('; ').find(v => v.startsWith('pfempire_csrf='))?.split('=')[1];
    await requestJSON('/api/models', {
      payload:{action, provider:model.provider, model:model.model, mode:$('modelMemory').value, confirmed:action === 'download'},
      headers:{...(csrf ? {'X-CSRF-Token':decodeURIComponent(csrf)} : {}),
               ...(snapshot?.local_token ? {'X-OriginRAG-Local-Token':snapshot.local_token} : {})},
    });
    await refresh();
  } catch (error) { $('modelError').textContent = error.message; }
  finally { mutating = false; render(); }
}

export function openModels() {
  $('globalSettingsDialog').close();
  $('modelError').textContent = '';
  dialog.showModal();
  refresh();
}
$('modelClose').onclick = () => dialog.close();
dialog.addEventListener('close', () => clearTimeout(timer));
$('modelProvider').onchange = render;
$('modelRefresh').onclick = refresh;
$('modelCancel').onclick = () => change('cancel');
$('modelDefault').onclick = () => change('default');
$('modelStop').onclick = () => change('stop');
