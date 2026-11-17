import {StateEffect, StateField} from '@codemirror/state';
import {Decoration, EditorView, WidgetType} from '@codemirror/view';

export const assistantReviewEffect = StateEffect.define();

class ReviewWidget extends WidgetType {
  constructor(item, options) { super(); this.item = item; this.options = options; }
  eq(other) { return this.item === other.item && this.options === other.options; }
  toDOM(view) {
    const doc = view.dom.ownerDocument, box = doc.createElement('section');
    box.className = 'cm-ai-review'; box.contentEditable = 'false'; box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'Quill suggestion');
    const heading = doc.createElement('div'); heading.className = 'cm-ai-heading';
    const title = doc.createElement('span'); title.className = 'cm-ai-title'; heading.append(title);
    const next = doc.createElement('button'); next.type = 'button'; next.className = 'cm-ai-next'; next.textContent = 'Next ›'; heading.append(next);
    const proposed = doc.createElement('pre'); proposed.className = 'cm-ai-proposed'; proposed.setAttribute('aria-label', 'Proposed text');
    const notes = doc.createElement('div'); notes.className = 'cm-ai-notes';
    const actions = doc.createElement('div'); actions.className = 'cm-ai-actions';
    for (const [kind, label] of [['accept', 'Accept'], ['reject', 'Reject']]) {
      const button = doc.createElement('button'); button.type = 'button'; button.className = 'cm-ai-' + kind;
      button.textContent = label; actions.append(button);
    }
    box.append(heading, proposed, notes, actions); this.updateDOM(box); return box;
  }
  updateDOM(box) {
    const {item, options} = this;
    box.dataset.reviewId = item.id;
    box.classList.toggle('cm-ai-pending', Boolean(options.pending));
    box.querySelector('.cm-ai-title').textContent = options.pending ? 'Quill · writing…' : 'Quill';
    box.querySelector('.cm-ai-proposed').textContent = item.replacement;
    const notes = box.querySelector('.cm-ai-notes'); notes.replaceChildren();
    for (const text of item.review_notes || []) {
      const note = box.ownerDocument.createElement('p'); note.textContent = text; notes.append(note);
    }
    const next = box.querySelector('.cm-ai-next'); next.hidden = options.pending || options.total < 2;
    next.disabled = options.busy; next.onclick = () => options.navigate(item.id);
    const accept = box.querySelector('.cm-ai-accept'), reject = box.querySelector('.cm-ai-reject');
    accept.disabled = options.pending || options.busy; reject.disabled = options.busy;
    accept.onclick = () => options.accept(item.id); reject.onclick = () => options.reject(item.id);
    return true;
  }
  ignoreEvent() { return true; }
}

export const assistantReviewField = StateField.define({
  create: () => Decoration.none,
  update(value, transaction) {
    if (transaction.docChanged) return Decoration.none;
    for (const effect of transaction.effects) {
      if (!effect.is(assistantReviewEffect)) continue;
      const {items, options} = effect.value, decorations = [], doc = transaction.state.doc;
      for (const item of items) {
        if (item.from < 0 || item.to > doc.length || item.to <= item.from || doc.sliceString(item.from, item.to) !== item.original) continue;
        decorations.push(Decoration.mark({class: options.pending ? 'cm-ai-original cm-ai-original-pending' : 'cm-ai-original'}).range(item.from, item.to));
        const end = doc.lineAt(Math.max(item.from, item.to - 1)).to;
        decorations.push(Decoration.widget({widget: new ReviewWidget(item, options), block: true, side: 1}).range(end));
      }
      return Decoration.set(decorations, true);
    }
    return value;
  },
  provide: field => EditorView.decorations.from(field),
});

export const assistantReviewTheme = EditorView.baseTheme({
  '.cm-ai-original': {backgroundColor: 'rgba(212,70,82,.12)', textDecoration: 'line-through', textDecorationColor: '#c3646b'},
  '.cm-ai-original-pending': {backgroundColor: 'rgba(212,164,70,.12)', textDecoration: 'none'},
  '.cm-ai-review': {margin: '8px 18px 12px 12px', padding: '10px 12px', border: '1px solid var(--code-line)', borderLeft: '3px solid #409a70', borderRadius: '8px', backgroundColor: 'rgba(64,154,112,.07)', color: 'var(--code-text)', whiteSpace: 'normal', font: '12px/1.5 system-ui,sans-serif'},
  '.cm-ai-pending': {borderStyle: 'dashed'},
  '.cm-ai-heading': {display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px'},
  '.cm-ai-title': {fontSize: '11px', opacity: '.75'},
  '.cm-ai-review .cm-ai-proposed': {maxHeight: '240px', overflow: 'auto', margin: '8px 0', color: 'var(--code-text)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', font: 'inherit', fontSize: 'var(--editor-font-size,11px)', fontFamily: 'ui-monospace,monospace'},
  '.cm-ai-actions': {display: 'flex', gap: '8px'},
  '.cm-ai-review button': {padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--code-line)', backgroundColor: 'var(--code-bg)', color: 'var(--code-text)', font: '12px/1.5 system-ui,sans-serif', cursor: 'pointer'},
  '.cm-ai-review button:disabled': {opacity: '.45', cursor: 'default'},
  '.cm-ai-review .cm-ai-accept': {borderColor: '#409a70'},
  '.cm-ai-review .cm-ai-next': {padding: '2px 6px', border: '0', backgroundColor: 'transparent'},
  '.cm-ai-review [hidden]': {display: 'none'},
  '.cm-ai-notes p': {fontSize: '11px', margin: '6px 0', color: 'var(--code-muted)'},
});
