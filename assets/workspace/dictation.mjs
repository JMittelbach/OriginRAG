export function attachDictation(form, field) {
  const mic = form.querySelector('#dictate');
  const panel = form.querySelector('#dictationPanel');
  const note = form.querySelector('#dictationNote');
  const lang = form.querySelector('#dictationLanguage');
  const retry = form.querySelector('#dictationRetry');
  const cancel = form.querySelector('#dictationCancel');
  let active = null;
  try { lang.value = localStorage.getItem('originrag.dictation.language') || 'auto'; } catch {}
  if (!lang.value) lang.value = 'auto';
  lang.onchange = () => { try { localStorage.setItem('originrag.dictation.language', lang.value); } catch {} };

  function show(text, state = '') {
    panel.hidden = !text;
    note.textContent = text;
    const recording = state === 'recording';
    mic.setAttribute('aria-pressed', String(recording));
    mic.setAttribute('aria-label', recording ? 'Finish dictation' : 'Dictate question');
    mic.title = mic.getAttribute('aria-label');
    mic.disabled = !!active && !recording;
    retry.hidden = state !== 'retry';
    lang.disabled = state === 'sending';
    form.dataset.dictating = active ? 'yes' : 'no';
  }
  function release(s) {
    clearTimeout(s.timer);
    s.stream?.getTracks().forEach(t => t.stop());
    if (s.ctx && s.ctx.state !== 'closed') s.ctx.close().catch(() => {});
    s.stream = s.ctx = null;
  }
  function dismiss() {
    const s = active;
    active = null;
    if (s) { s.request.abort(); release(s); }
    show('');
  }
  function fail(s, text) {
    if (s !== active) return;
    dismiss();
    show(text);
  }
  async function upload(s) {
    if (s !== active) return;
    release(s);
    show('Transcribing…', 'sending');
    s.request = new AbortController();
    const deadline = setTimeout(() => s.request.abort(), 165000);
    try {
      const response = await fetch('/api/dictation', {method:'POST', signal:s.request.signal,
        headers:{'Content-Type':'application/octet-stream', 'X-Dictation-Language':lang.value,
                 'X-OriginRAG-Local-Token':s.token}, body:s.clip});
      const data = await response.json();
      if (s !== active) return;
      if (!response.ok) {
        if (response.status === 429 || response.status >= 500) { show(data.error || 'Please retry.', 'retry'); return; }
        fail(s, data.error || 'Could not transcribe this recording.'); return;
      }
      const text = String(data.text || '').trim();
      if (!text) { fail(s, 'No speech detected. Check your microphone and try again.'); return; }
      // Never replace edits made while the microphone or recognizer was running.
      const unchanged = s.draft === field.value;
      const start = unchanged ? s.start : field.selectionEnd;
      const end = unchanged ? s.end : start;
      const leftSpace = start && /[^\s([{]$/.test(field.value.slice(0,start)) && !/^[,.;:!?]/.test(text) ? ' ' : '';
      const rightSpace = /^[^\s,.;:!?)\]}]/.test(field.value.slice(end)) ? ' ' : '';
      field.setRangeText(leftSpace + text + rightSpace, start, end, 'end');
      field.dispatchEvent(new Event('input', {bubbles:true}));
      dismiss();
      field.focus();
    } catch {
      if (s === active) show('Connection interrupted. Retry this recording.', 'retry');
    } finally { clearTimeout(deadline); }
  }
  function finish(s) {
    if (s !== active || !s.node || s.stopping) return;
    s.stopping = true;
    show('Finishing recording…');
    s.node.port.postMessage('finish');
    clearTimeout(s.timer);
    s.timer = setTimeout(() => seal(s), 500);
  }
  function seal(s) {
    if (s !== active || s.clip) return;
    s.clip = new Blob(s.parts, {type:'application/octet-stream'});
    s.parts = [];
    if (s.clip.size < 8000) { fail(s, 'Recording too short. Try again.'); return; }
    upload(s);
  }
  async function start() {
    if (field.disabled) return;
    if (active) { finish(active); return; }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      show('Microphone unavailable. Open the chat on localhost or HTTPS in a current browser.'); return;
    }
    const s = active = {request:new AbortController(), draft:field.value, start:field.selectionStart,
                         end:field.selectionEnd, parts:[]};
    show('Preparing microphone…');
    try {
      s.ctx = new AudioContext();
      // Resume in the click gesture, before awaiting network or permission.
      s.ctx.resume().catch(() => {});
      const response = await fetch('/api/dictation', {signal:s.request.signal});
      const info = await response.json();
      if (s !== active) return;
      if (!response.ok || !info.ready) {
        fail(s, info.error || 'Not installed. Run ./originrag setup-dictation on the OriginRAG host.'); return;
      }
      s.token = info.token || '';
      show('Allow microphone access. Audio is transcribed on your OriginRAG host.');
      const stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:true}});
      if (s !== active) { stream.getTracks().forEach(t => t.stop()); return; }
      s.stream = stream;
      await s.ctx.audioWorklet.addModule('/assets/workspace/dictation-audio.mjs');
      if (s !== active) return;
      await s.ctx.resume();
      if (s !== active) return;
      s.node = new AudioWorkletNode(s.ctx, 'origin-dictation');
      s.node.port.onmessage = ({data}) => {
        if (s !== active || s.clip) return;
        if (data === 'end') seal(s); else s.parts.push(data);
      };
      const input = s.ctx.createMediaStreamSource(stream);
      input.connect(s.node);
      // Worklet output is silence; connecting it keeps the audio graph processing.
      s.node.connect(s.ctx.destination);
      stream.getAudioTracks()[0].onended = () => finish(s);
      s.timer = setTimeout(() => finish(s), 90000);
      show('Listening · click the microphone to finish', 'recording');
    } catch (e) {
      fail(s, e.name === 'NotAllowedError' ? 'Microphone access denied. Allow it in your browser and try again.'
        : e.name === 'NotFoundError' ? 'No microphone found. Connect one and try again.'
        : 'Could not start the microphone. Check your audio input and try again.');
    }
  }
  mic.onclick = start;
  retry.onclick = () => { if (active?.clip) upload(active); };
  cancel.onclick = dismiss;
  form.addEventListener('origin:cancel-dictation', dismiss);
  window.addEventListener('pagehide', dismiss);
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); dismiss(); }
  });
}
