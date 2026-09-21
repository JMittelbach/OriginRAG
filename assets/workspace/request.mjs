export async function requestJSON(path, {payload, body, headers = {}, signal, timeout = 30000} = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  if (signal?.aborted) cancel();
  else signal?.addEventListener('abort', cancel, {once: true});
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  const mutation = payload !== undefined || body !== undefined;
  try {
    const outgoing = new Headers(headers);
    if (payload !== undefined) outgoing.set('Content-Type', 'application/json');
    const response = await fetch(path, {
      method: mutation ? 'POST' : 'GET', headers: outgoing,
      body: payload !== undefined ? JSON.stringify(payload) : body,
      signal: controller.signal,
    });
    if (response.redirected && new URL(response.url).pathname === '/login') {
      const error = new Error('Your session has expired. Sign in again in another tab, then retry here.');
      error.status = 401;
      throw error;
    }
    let data;
    try { data = await response.json(); }
    catch (error) {
      if (controller.signal.aborted) throw error;
      data = null;
    }
    if (!response.ok || data === null) {
      const fallback = response.status === 401
        ? 'Your session has expired. Sign in again in another tab, then retry here.'
        : response.status === 403
          ? 'This action is not available with your current access.'
          : 'The server could not complete this request. Please try again.';
      const error = new Error(typeof data?.error === 'string' ? data.error : fallback);
      error.status = response.status;
      error.retryable = [408, 429, 502, 503, 504].includes(response.status) || (response.ok && data === null);
      throw error;
    }
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Request cancelled.', 'AbortError');
    if (timedOut || error instanceof TypeError) {
      const problem = new Error(timedOut
        ? (mutation ? 'The server has not confirmed this change yet. Check its status before trying again.' : 'The server took too long to respond. Please try again.')
        : 'Connection lost. Check your connection and try again.');
      problem.retryable = true;
      throw problem;
    }
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}
