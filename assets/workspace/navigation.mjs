import {requestJSON} from './request.mjs';
import {getAppProfile} from './profile.mjs';
import {openModels} from './models-launcher.mjs';
import {bindDeviceHandoff} from './device-handoff.mjs';
import {bindDocumentSync} from './document-sync.mjs';

const $ = id => document.getElementById(id);
const navigation = $('globalNavigation') || $('applicationHeader');
const key = 'origin.workspace.settings.v1';
bindDeviceHandoff($('deviceHandoff'));
bindDocumentSync($('documentSync'));
let settings = {};
try {
  const stored = JSON.parse(localStorage.getItem(key));
  if (stored && typeof stored === 'object' && !Array.isArray(stored)) settings = stored;
} catch {}
document.documentElement.dataset.reduceMotion = String(Boolean(settings.reduceMotion));
const startPage = $('globalStartPage');
startPage.value = [...startPage.options].some(option => option.value === settings.startPage)
  ? settings.startPage : new URL(document.querySelector('.origin-brand').href).pathname;
let profileReady = false, loadingProfile = false;
async function loadProfile() {
  if (loadingProfile) return;
  loadingProfile = true;
  $('globalAccountRetry').disabled = true;
  $('globalAccountDetails').textContent = 'Loading account…';
  try {
    const profile = await getAppProfile();
    if ($('documentSync')) $('documentSync').hidden = profile.local_document_sync !== true;
    $('globalModels').hidden = !profile.features.rag && !profile.features.write;
    for (const item of navigation.querySelectorAll('[data-feature]')) item.hidden = !profile.features[item.dataset.feature];
    for (const item of startPage.querySelectorAll('[data-feature]')) item.disabled = !profile.features[item.dataset.feature];
    document.querySelector('.origin-brand').href = profile.home;
    const routes = [...startPage.options].filter(option => !option.disabled).map(option => option.value);
    startPage.value = routes.includes(settings.startPage) ? settings.startPage : profile.home;
    $('globalAccount').hidden = profile.authenticated !== true;
    $('globalAccount').textContent = profile.user?.display_name || profile.user?.username || 'Account';
    $('globalAccountDetails').textContent = profile.user ? `${profile.user.display_name || profile.user.username} (@${profile.user.username})` : 'Local workspace · no sign-in required';
    $('globalSignOut').hidden = !profile.authenticated;
    $('globalAccountError').textContent = '';
    $('globalAccountRetry').hidden = true;
    profileReady = true;
  } catch (error) {
    $('globalAccountDetails').textContent = 'Account information could not be loaded.';
    $('globalAccountError').textContent = error.message;
    $('globalAccountRetry').hidden = false;
  } finally {
    loadingProfile = false;
    $('globalAccountRetry').disabled = false;
  }
}
for (const item of navigation.querySelectorAll('nav a')) {
  if (item.pathname === location.pathname || (location.pathname === '/' && item.pathname === '/chat')) item.setAttribute('aria-current', 'page');
}
$('globalSettings').onclick = () => $('globalSettingsDialog').showModal();
$('globalModels').onclick = openModels;
$('chatModels')?.addEventListener('click', openModels);
$('globalAccount').onclick = () => $('globalAccountDialog').showModal();
$('globalAccountRetry').onclick = loadProfile;
$('globalReduceMotion').checked = Boolean(settings.reduceMotion);
function saveSettings() {
  settings = {startPage: $('globalStartPage').value, reduceMotion: $('globalReduceMotion').checked};
  document.documentElement.dataset.reduceMotion = String(settings.reduceMotion);
  try { localStorage.setItem(key, JSON.stringify(settings)); } catch {}
}
$('globalStartPage').onchange = $('globalReduceMotion').onchange = saveSettings;
$('globalSignOut').onclick = async () => {
  const signOut = $('globalSignOut');
  if (signOut.disabled) return;
  signOut.disabled = true;
  signOut.setAttribute('aria-busy', 'true');
  $('globalAccountError').textContent = '';
  try {
    await window.originragSaveDraft?.();
    const token = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
    await requestJSON('/api/logout', {payload: {}, timeout: 15000,
      headers: token ? {'X-CSRF-Token': decodeURIComponent(token)} : {}});
    location.href = '/login';
  } catch (error) {
    $('globalAccountError').textContent = error.message;
  } finally {
    signOut.disabled = false;
    signOut.removeAttribute('aria-busy');
  }
};
window.addEventListener('online', () => { if (!profileReady) loadProfile(); });
window.addEventListener('storage', event => {
  if (event.key === key) {
    try { const value = JSON.parse(event.newValue) || {}; document.documentElement.dataset.reduceMotion = String(Boolean(value.reduceMotion)); } catch {}
  }
});
loadProfile();
