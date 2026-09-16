import {EditorState} from '@codemirror/state';
import {EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, dropCursor, highlightSpecialChars} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab, undo, redo, toggleComment} from '@codemirror/commands';
import {StreamLanguage, syntaxHighlighting, HighlightStyle, bracketMatching, indentUnit} from '@codemirror/language';
import {stex} from '@codemirror/legacy-modes/mode/stex';
import {search, searchKeymap, openSearchPanel, highlightSelectionMatches} from '@codemirror/search';
import {autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap} from '@codemirror/autocomplete';
import {tags} from '@lezer/highlight';

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

export function createLatexEditor(parent, onChange, onCursor) {
  const documents = new Map();
  let current = '', view;
  const theme = EditorView.theme({
    '&': {height: '100%', color: 'var(--code-text)', backgroundColor: 'var(--code-bg)', fontSize: 'var(--editor-font-size, 14px)'},
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
      autocompletion({override: [complete]}), EditorState.allowMultipleSelections.of(true),
      keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
      EditorView.editable.of(editable), EditorState.readOnly.of(!editable), theme,
      EditorView.contentAttributes.of({'aria-label': 'LaTeX source', spellcheck: 'false'}),
      EditorView.updateListener.of(update => {
        if (update.docChanged) onChange(current, update.state.doc.toString());
        if (update.docChanged || update.selectionSet) cursor();
      }),
    ]});
  }
  function cursor() {
    const pos = view.state.selection.main.head, line = view.state.doc.lineAt(pos);
    onCursor(line.number, pos - line.from + 1);
  }
  view = new EditorView({parent, state: state('', true)});
  return {
    open(name, text, editable = true) {
      if (current === name && view.state.doc.toString() === text) return;
      if (current) documents.set(current, {state: view.state, top: view.scrollDOM.scrollTop, left: view.scrollDOM.scrollLeft});
      current = name;
      const old = documents.get(name), reuse = old && old.state.doc.toString() === text;
      view.setState(reuse ? old.state : state(text, editable));
      view.scrollDOM.scrollTop = reuse ? old.top : 0;
      view.scrollDOM.scrollLeft = reuse ? old.left : 0;
      cursor();
    },
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
