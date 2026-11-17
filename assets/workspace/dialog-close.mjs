export function addDialogClose(dialog, label, {id, onClose} = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'dialog-close';
  button.textContent = '×';
  button.setAttribute('aria-label', label);
  button.title = label;
  if (id) button.id = id;
  button.onclick = onClose || (() => dialog.close());
  dialog.prepend(button);
  return button;
}
