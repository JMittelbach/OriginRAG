const $ = id => document.getElementById(id);
const key = 'origin.workspace.settings.v1';
let settings = {};
try { settings = JSON.parse(localStorage.getItem(key)) || {}; } catch {}
document.documentElement.dataset.reduceMotion = String(Boolean(settings.reduceMotion));
const profile = await fetch('/api/app-profile').then(response => { if (!response.ok) throw new Error('Account unavailable'); return response.json(); }).catch(() => null);
if (profile) {
  for (const item of $('globalNavigation').querySelectorAll('[data-feature]')) item.hidden = !profile.features[item.dataset.feature];
  for (const item of $('globalStartPage').querySelectorAll('[data-feature]')) item.disabled = !profile.features[item.dataset.feature];
  document.querySelector('.origin-brand').href = profile.home;
  const routes = [...$('globalStartPage').options].filter(option => !option.disabled).map(option => option.value);
  $('globalStartPage').value = routes.includes(settings.startPage) ? settings.startPage : profile.home;
  $('globalAccount').textContent = profile.user?.display_name || profile.user?.username || 'Account';
  $('globalAccountDetails').textContent = profile.user ? `${profile.user.display_name || profile.user.username} (@${profile.user.username})` : 'Local workspace · no sign-in required';
  $('globalSignOut').hidden = !profile.authenticated;
}
for (const item of $('globalNavigation').querySelectorAll('nav a')) {
  if (item.pathname === location.pathname || (location.pathname === '/' && item.pathname === '/chat')) item.setAttribute('aria-current', 'page');
}
$('globalSettings').onclick = () => $('globalSettingsDialog').showModal();
$('globalAccount').onclick = () => $('globalAccountDialog').showModal();
$('globalReduceMotion').checked = Boolean(settings.reduceMotion);
function saveSettings() {
  settings = {startPage: $('globalStartPage').value, reduceMotion: $('globalReduceMotion').checked};
  document.documentElement.dataset.reduceMotion = String(settings.reduceMotion);
  try { localStorage.setItem(key, JSON.stringify(settings)); } catch {}
}
$('globalStartPage').onchange = $('globalReduceMotion').onchange = saveSettings;
$('globalSignOut').onclick = async () => {
  try {
    await window.originragSaveDraft?.();
    const token = document.cookie.split('; ').find(value => value.startsWith('pfempire_csrf='))?.split('=')[1];
    const response = await fetch('/api/logout', {method: 'POST', headers: {'Content-Type': 'application/json', ...(token ? {'X-CSRF-Token': decodeURIComponent(token)} : {})}, body: '{}'});
    if (!response.ok) throw new Error('Sign out failed. Your session is still open.');
    location.href = '/login';
  } catch (error) { $('globalAccountError').textContent = error.message; }
};
window.addEventListener('storage', event => {
  if (event.key === key) {
    try { const value = JSON.parse(event.newValue) || {}; document.documentElement.dataset.reduceMotion = String(Boolean(value.reduceMotion)); } catch {}
  }
});
