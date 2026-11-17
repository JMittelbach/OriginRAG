import {EditorState, Transaction, Compartment} from '@codemirror/state';
import {EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, dropCursor, highlightSpecialChars} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab, undo, redo, toggleComment, isolateHistory} from '@codemirror/commands';
import {StreamLanguage, syntaxHighlighting, HighlightStyle, bracketMatching, indentUnit} from '@codemirror/language';
import {stex} from '@codemirror/legacy-modes/mode/stex';
import {search, searchKeymap, openSearchPanel, highlightSelectionMatches} from '@codemirror/search';
import {autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap} from '@codemirror/autocomplete';
import {tags} from '@lezer/highlight';
import {citationCompletion} from '../citation-completion.mjs';
import {assistantReviewEffect, assistantReviewField, assistantReviewTheme} from './assistant-review.js';

const colours = HighlightStyle.define([
  {tag: [tags.keyword, tags.tagName], class: 'tex-command'},
  {tag: [tags.atom, tags.string, tags.special(tags.string)], class: 'tex-value'},
  {tag: [tags.number, tags.bool], class: 'tex-number'},
  {tag: [tags.bracket, tags.punctuation], class: 'tex-bracket'},
  {tag: tags.comment, class: 'tex-comment'},
  {tag: [tags.variableName, tags.attributeName], class: 'tex-variable'},
]);
const commands = ['documentclass', 'usepackage', 'begin', 'end', 'title', 'author', 'date', 'maketitle',
  'section', 'subsection', 'subsubsection', 'paragraph', 'textbf', 'textit', 'emph', 'label', 'ref',
  'cite', 'citep', 'citet', 'includegraphics', 'caption', 'item', 'input', 'include', 'bibliography',
  'bibliographystyle', 'footnote', 'url', 'href'];
function complete(context) {
  const match = context.matchBefore(/\\[a-zA-Z]*/);
  if (!match) return null;
  return {from: match.from, options: commands.map(name => ({label: '\\' + name, type: 'keyword'})), validFor: /\\[a-zA-Z]*/};
}

export function diffTextChanges(before, after) {
  if (typeof before !== 'string' || typeof after !== 'string') return null;
  if (before === after) return [];
  if (before.length + after.length > 2_000_000) return null;
  let from = 0, endBefore = before.length, endAfter = after.length;
  while (from < endBefore && from < endAfter && before[from] === after[from]) from++;
  if (from && /[\uD800-\uDBFF]/.test(before[from - 1])) from--;
  while (endBefore > from && endAfter > from && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter--; }
  if (endBefore < before.length && /[\uDC00-\uDFFF]/.test(before[endBefore])) { endBefore++; endAfter++; }
  if (from === endBefore || from === endAfter) return [{from, to: endBefore, insert: after.slice(from, endAfter)}];
  const a = Array.from(before.slice(from, endBefore)), b = Array.from(after.slice(from, endAfter));
  const frontier = new Map([[1, 0]]), trace = [];
  let work = 0, stored = 0;
  for (let depth = 0; depth <= a.length + b.length; depth++) {
    stored += frontier.size;
    if (depth > 512 || stored > 100_000) return null;
    trace.push(new Map(frontier));
    for (let diagonal = -depth; diagonal <= depth; diagonal += 2) {
      if (++work > 1_000_000) return null;
      let x = diagonal === -depth || (diagonal !== depth && frontier.get(diagonal - 1) < frontier.get(diagonal + 1))
        ? frontier.get(diagonal + 1) : frontier.get(diagonal - 1) + 1;
      let y = x - diagonal;
      while (x < a.length && y < b.length && a[x] === b[y]) {
        if (++work > 1_000_000) return null;
        x++; y++;
      }
      frontier.set(diagonal, x);
      if (x < a.length || y < b.length) continue;
      const edits = [];
      for (let d = depth; d > 0; d--) {
        const previous = trace[d], k = x - y;
        const previousK = k === -d || (k !== d && previous.get(k - 1) < previous.get(k + 1)) ? k + 1 : k - 1;
        const previousX = previous.get(previousK), previousY = previousX - previousK;
        const inserted = previousK === k + 1;
        edits.push({at: previousX, insert: inserted ? b[previousY] : null});
        x = previousX; y = previousY;
      }
      const offsets = [from];
      for (const character of a) offsets.push(offsets.at(-1) + character.length);
      const changes = [];
      for (const edit of edits.reverse()) {
        let change = changes.at(-1);
        if (!change || change.to !== offsets[edit.at]) {
          change = {from: offsets[edit.at], to: offsets[edit.at], insert: ''}; changes.push(change);
        }
        if (edit.insert === null) change.to = offsets[edit.at + 1];
        else change.insert += edit.insert;
      }
      return changes;
    }
  }
  return null;
}

export function createLatexEditor(parent, onChange, onCursor, getFiles = () => ({})) {
  const documents = new Map();
  const editability = new Compartment();
  let current = '', view, assistantReview = null, assistantApplying = false, disposed = false;
  function paintAssistantReview() {
    if (disposed) return;
    const items = assistantReview?.items.filter(item => item.file === current) || [];
    view.dispatch({effects: assistantReviewEffect.of({items, options: assistantReview?.options || {}}), annotations: Transaction.addToHistory.of(false)});
  }
  function clearAssistantReview(reason = '') {
    const previous = assistantReview; assistantReview = null;
    for (const [name, saved] of documents) {
      if (name !== current && saved.state.field(assistantReviewField).size) {
        documents.set(name, {...saved, state: saved.state.update({effects: assistantReviewEffect.of({items: [], options: {}}), annotations: Transaction.addToHistory.of(false)}).state});
      }
    }
    paintAssistantReview();
    if (reason && previous) previous.options.invalidated?.(reason);
  }
  const theme = EditorView.theme({
    '&': {height: '100%', color: 'var(--code-text)', backgroundColor: 'var(--code-bg)', fontSize: 'var(--editor-font-size, 11px)'},
    '&.cm-focused': {outline: 'none'},
    '.cm-scroller': {overflow: 'auto', fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace', lineHeight: '1.65'},
    '.cm-content': {padding: '16px 0', caretColor: 'var(--code-text)'},
    '.cm-line': {padding: '0 18px 0 12px'},
    '.cm-gutters': {backgroundColor: 'var(--code-gutter)', color: 'var(--code-muted)', borderRight: '1px solid var(--code-line)', minWidth: '48px'},
    '.cm-lineNumbers .cm-gutterElement': {padding: '0 12px 0 8px'},
    '.cm-activeLine, .cm-activeLineGutter': {backgroundColor: 'var(--code-active)'},
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': {backgroundColor: 'var(--code-selection)'},
    '.cm-cursor': {borderLeftColor: 'var(--code-text)'},
    '.cm-matchingBracket': {backgroundColor: 'var(--code-selection)', outline: '1px solid var(--code-muted)'},
    '.cm-panels, .cm-tooltip': {backgroundColor: 'var(--code-gutter)', color: 'var(--code-text)', borderColor: 'var(--code-line)'},
    '.cm-textfield, .cm-button': {background: 'var(--code-bg)', color: 'var(--code-text)', border: '1px solid var(--code-line)', borderRadius: '4px'},
    '.cm-searchMatch': {backgroundColor: '#eab94d55', outline: '1px solid #a78032'},
    '.cm-searchMatch-selected': {backgroundColor: '#eab94d99'},
  });
  function state(text, editable) {
    return EditorState.create({doc: text, extensions: [
      lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(), drawSelection(), dropCursor(),
      highlightActiveLine(), highlightSelectionMatches(), bracketMatching(), closeBrackets(),
      search({top: true}), StreamLanguage.define(stex), syntaxHighlighting(colours), indentUnit.of('  '),
      autocompletion({override: [citationCompletion(getFiles, () => /\.tex$/i.test(current)), complete]}), EditorState.allowMultipleSelections.of(true),
      keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
      editability.of([EditorView.editable.of(editable), EditorState.readOnly.of(!editable)]), theme, assistantReviewField, assistantReviewTheme,
      EditorView.contentAttributes.of({'aria-label': 'LaTeX source', spellcheck: 'false'}),
      EditorView.updateListener.of(update => {
        if (update.docChanged && assistantReview && !assistantApplying) {
          const previous = assistantReview;
          queueMicrotask(() => { if (assistantReview === previous) clearAssistantReview('The draft changed. Previous suggestions were cleared.'); });
        }
        if (update.docChanged && update.transactions.some(tr => tr.docChanged && !tr.annotation(Transaction.remote))) onChange(current, update.state.doc.toString());
        if (update.docChanged || update.selectionSet) cursor();
      }),
    ]});
  }
  function cursor() {
    const pos = view.state.selection.main.head, line = view.state.doc.lineAt(pos);
    onCursor(line.number, pos - line.from + 1);
  }
  function remoteTransaction(before, text, editable) {
    const changes = diffTextChanges(before.doc.toString(), text);
    if (changes === null) return null;
    return before.update({changes,
      effects: before.readOnly === !editable ? [] : editability.reconfigure([EditorView.editable.of(editable), EditorState.readOnly.of(!editable)]),
      annotations: [Transaction.remote.of(true), Transaction.addToHistory.of(false), isolateHistory.of('full')],
    });
  }
  function applyRemote(name, text, editable = true) {
    if (typeof name !== 'string' || typeof text !== 'string') return false;
    if (name === current) {
      if (view.state.doc.toString() === text && view.state.readOnly === !editable) return true;
      if (view.compositionStarted) return false;
      const transaction = remoteTransaction(view.state, text, editable);
      if (!transaction) return false;
      const top = view.scrollDOM.scrollTop, left = view.scrollDOM.scrollLeft;
      view.dispatch(transaction);
      view.scrollDOM.scrollTop = top; view.scrollDOM.scrollLeft = left;
      return true;
    }
    const old = documents.get(name);
    if (!old) { documents.set(name, {state: state(text, editable), top: 0, left: 0}); return true; }
    const transaction = remoteTransaction(old.state, text, editable);
    if (!transaction) return false;
    documents.set(name, {...old, state: transaction.state});
    return true;
  }
  view = new EditorView({parent, state: state('', true)});
  return {
    open(name, text, editable = true) {
      if (current === name) return applyRemote(name, text, editable);
      if (!applyRemote(name, text, editable)) return false;
      if (current) documents.set(current, {state: view.state, top: view.scrollDOM.scrollTop, left: view.scrollDOM.scrollLeft});
      current = name;
      const old = documents.get(name);
      view.setState(old.state);
      view.scrollDOM.scrollTop = old?.top || 0;
      view.scrollDOM.scrollLeft = old?.left || 0;
      paintAssistantReview();
      cursor();
      return true;
    },
    applyRemote,
    showAssistantReview(items, expectedFiles, options) {
      if (disposed || !Array.isArray(items) || !items.length || items.length > 4) return false;
      for (const item of items) {
        if (typeof expectedFiles[item.file] !== 'string' || !Number.isInteger(item.from) || !Number.isInteger(item.to)
            || item.from < 0 || item.to <= item.from || item.to > expectedFiles[item.file].length || expectedFiles[item.file].slice(item.from, item.to) !== item.original
            || typeof item.replacement !== 'string') return false;
        if (item.file === current && view.state.doc.toString() !== expectedFiles[item.file]) return false;
      }
      assistantReview = {items, options: {...options, total: items.length}};
      paintAssistantReview(); return true;
    },
    clearAssistantReview,
    isComposing() { return view.compositionStarted; },
    focus() { view.focus(); },
    selection() {
      const {from, to} = view.state.selection.main;
      return {file: current, from, to, text: view.state.sliceDoc(from, to)};
    },
    replaceSelection(selected, replacement, expectedSource) {
      if (view.state.readOnly || view.compositionStarted || current !== selected.file
          || typeof replacement !== 'string' || view.state.doc.toString() !== expectedSource
          || view.state.sliceDoc(selected.from, selected.to) !== selected.text) return false;
      view.dispatch({changes: {from: selected.from, to: selected.to, insert: replacement},
        selection: {anchor: selected.from, head: selected.from + replacement.length},
        annotations: isolateHistory.of('full'), userEvent: 'input.proofread'});
      view.focus();
      return true;
    },
    applyProjectEdits(edits, expectedFiles) {
      if (view.compositionStarted || !Array.isArray(edits) || !edits.length || edits.length > 4) return false;
      const groups = new Map(), transactions = [];
      for (const edit of edits) {
        if (!edit || typeof edit.file !== 'string' || !/\.(tex|bib|sty|cls|bst|bbl|txt|md|csv)$/i.test(edit.file)
            || typeof expectedFiles[edit.file] !== 'string' || typeof edit.original !== 'string' || !edit.original
            || typeof edit.replacement !== 'string') return false;
        const source = expectedFiles[edit.file], anchored = Number.isInteger(edit.from) && Number.isInteger(edit.to);
        const from = anchored ? edit.from : source.indexOf(edit.original), to = anchored ? edit.to : from + edit.original.length;
        if (from < 0 || to <= from || to > source.length || source.slice(from, to) !== edit.original
            || !anchored && source.indexOf(edit.original, from + 1) !== -1) return false;
        const changes = groups.get(edit.file) || [];
        if (changes.some(change => from < change.to && to > change.from)) return false;
        changes.push({from, to, insert: edit.replacement}); groups.set(edit.file, changes);
      }
      for (const [name, changes] of groups) {
        let before = name === current ? view.state : documents.get(name)?.state || state(expectedFiles[name], true);
        if (before.readOnly) return false;
        if (before.doc.toString() !== expectedFiles[name]) {
          if (name === current) return false;
          const remote = remoteTransaction(before, expectedFiles[name], true);
          if (!remote) return false;
          before = remote.state;
        }
        const transaction = before.update({changes: changes.sort((a, b) => a.from - b.from),
          annotations: isolateHistory.of('full'), userEvent: 'input.assistant'});
        transactions.push({name, transaction});
      }
      assistantApplying = true;
      try {
        for (const {name, transaction} of transactions) {
          if (name === current) view.dispatch(transaction);
          else {
            const saved = documents.get(name);
            documents.set(name, {state: transaction.state, top: saved?.top || 0, left: saved?.left || 0});
            onChange(name, transaction.state.doc.toString());
          }
        }
      } finally { assistantApplying = false; }
      return true;
    },
    destroy() { clearAssistantReview('The editor closed.'); disposed = true; view.destroy(); documents.clear(); current = ''; },
    jump(line) {
      const target = view.state.doc.line(Math.max(1, Math.min(line, view.state.doc.lines)));
      view.dispatch({selection: {anchor: target.from, head: target.to}, effects: EditorView.scrollIntoView(target.from, {y: 'center'})});
      view.focus();
    },
    command(name) {
      if (name === 'search') return openSearchPanel(view);
      if (view.state.readOnly) return;
      if (name === 'undo') undo(view);
      else if (name === 'redo') redo(view);
      else if (name === 'comment') toggleComment(view);
      else if (name === 'bold' || name === 'italic') {
        const {from, to} = view.state.selection.main;
        const start = name === 'bold' ? '\\textbf{' : '\\textit{';
        const text = view.state.sliceDoc(from, to) || 'text';
        view.dispatch({changes: {from, to, insert: start + text + '}'}, selection: {anchor: from + start.length, head: from + start.length + text.length}, userEvent: 'input'});
      }
      view.focus();
    },
    insertCitation(key) {
      if (view.state.readOnly || !/^[A-Za-z0-9_.:-]+$/.test(key)) return false;
      const {from, to} = view.state.selection.main;
      const insert = '\\cite{' + key + '}';
      view.dispatch({changes: {from, to, insert}, selection: {anchor: from + insert.length}, userEvent: 'input'});
      view.focus();
      return true;
    },
    measure() { view.requestMeasure(); },
  };
}
