export async function openModels() {
  try {
    const models = await import('./models.mjs');
    models.openModels();
  } catch {
    document.getElementById('globalSettingsDialog')?.close();
    const dialog = document.getElementById('modelDialog');
    const error = document.getElementById('modelError');
    if (error) error.textContent = 'The model controls could not be loaded. Reload this page and try again. Settings and your manuscript remain available.';
    const close = document.getElementById('modelClose');
    if (close) close.onclick = () => dialog?.close();
    if (dialog && !dialog.open) dialog.showModal();
  }
}
