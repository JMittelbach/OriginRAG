import {closeHistory} from './editor.mjs';

const formattingMarks = new Set(['bold', 'italic', 'underline', 'strike', 'subscript', 'superscript', 'textStyle', 'highlight']);
const paragraphAttrs = ['textAlign', 'lineHeight', 'spaceBefore', 'spaceAfter', 'indent'];

export function captureFormatting(editor) {
  const {selection, storedMarks} = editor.state;
  let source = storedMarks || selection.$from.marks(), paragraph = selection.$from.parent, found = false;
  if (!selection.empty) editor.state.doc.nodesBetween(selection.from, selection.to, node => {
    if (found) return false;
    if (['paragraph', 'heading'].includes(node.type.name)) paragraph = node;
    if (node.isText) { source = node.marks; found = true; }
  });
  return {marks: source.filter(mark => formattingMarks.has(mark.type.name)).map(mark => mark.toJSON()),
    paragraph: ['paragraph', 'heading'].includes(paragraph.type.name)
      ? Object.fromEntries(paragraphAttrs.map(key => [key, paragraph.attrs[key]])) : null};
}

export function applyFormatting(editor, format) {
  const {selection, schema, tr} = editor.state;
  if (!format || selection.empty) return false;
  for (const range of selection.ranges) {
    const from = range.$from.pos, to = range.$to.pos;
    for (const name of formattingMarks) if (schema.marks[name]) tr.removeMark(from, to, schema.marks[name]);
    for (const mark of format.marks) tr.addMark(from, to, schema.markFromJSON(mark));
    if (format.paragraph) tr.doc.nodesBetween(from, to, (node, pos) => {
      if (['paragraph', 'heading'].includes(node.type.name)) tr.setNodeMarkup(pos, undefined, {...node.attrs, ...format.paragraph});
    });
  }
  editor.view.dispatch(closeHistory(tr));
  editor.view.dispatch(closeHistory(editor.state.tr));
  return true;
}

export function fontSizePoints(editor) {
  const value = editor.getAttributes('textStyle').fontSize || '11pt';
  return Math.round((parseFloat(value) || 11) * (value.endsWith('px') ? 0.75 : 1) * 100) / 100;
}

export function applyFontSize(editor, value) {
  const size = Number(value);
  if (!String(value).trim() || !Number.isFinite(size) || size < 6 || size > 96) throw new Error('Choose a font size from 6 to 96 points.');
  const applied = editor.chain().setFontSize(`${Math.round(size * 100) / 100}pt`).run();
  editor.view.focus();
  return applied;
}
