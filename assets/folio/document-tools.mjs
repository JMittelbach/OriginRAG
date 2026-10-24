import {closeHistory} from './editor.mjs';

export const STYLE_SETS = [
  {name: 'Classic', font: 'Arial', color: '#365e94', body: 11, headings: [24, 18, 14]},
  {name: 'Manuscript', font: 'Times New Roman', color: '#202020', body: 12, headings: [18, 16, 14]},
  {name: 'Modern', font: 'Calibri', color: '#2370a3', body: 11, headings: [28, 20, 15]},
  {name: 'Editorial', font: 'Georgia', color: '#535353', body: 11, headings: [26, 19, 14]},
];

export function applyStyleSet(editor, style) {
  if (!STYLE_SETS.includes(style)) throw new Error('Choose a document style.');
  const {tr, schema} = editor.state;
  let changed = false;
  tr.doc.descendants((node, pos) => {
    if (!node.isTextblock || node.type.name === 'codeBlock') return;
    const heading = node.type.name === 'heading';
    node.descendants((child, offset) => {
      if (!child.isText) return;
      const attrs = child.marks.find(mark => mark.type.name === 'textStyle')?.attrs || {};
      tr.addMark(pos + 1 + offset, pos + 1 + offset + child.nodeSize, schema.marks.textStyle.create({
        ...attrs, fontFamily: style.font,
        fontSize: `${heading ? style.headings[Math.min(2, node.attrs.level - 1)] : style.body}pt`,
        color: heading ? style.color : '#202020',
      }));
      changed = true;
    });
    return false;
  });
  if (!changed) throw new Error('Add some text before applying a document style.');
  editor.view.dispatch(closeHistory(tr));
  editor.commands.focus(undefined, {scrollIntoView: false});
}

function newAnchor() {
  return 'folio-anchor-' + Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
}

export function documentHeadings(doc) {
  const headings = [];
  doc.descendants((node, pos) => {
    if (node.type.name === 'heading' && node.textContent.trim()) headings.push({
      pos, text: node.textContent, level: node.attrs.level, id: node.attrs.anchorId,
    });
  });
  return headings;
}

function assignAnchors(tr) {
  const used = new Set();
  tr.doc.descendants((node, pos) => {
    if (!['paragraph', 'heading'].includes(node.type.name)) return;
    let id = node.attrs.anchorId;
    if ((node.type.name === 'heading' && node.textContent.trim()) || id) {
      if (!id || used.has(id)) {
        id = newAnchor();
        tr.setNodeMarkup(pos, undefined, {...node.attrs, anchorId: id});
      }
      used.add(id);
    }
  });
}

export function updateContents(editor, updateOnly = false) {
  const {tr, schema} = editor.state;
  const old = [];
  tr.doc.forEach((node, pos) => { if (node.type.name === 'paragraph' && node.attrs.tocEntry) old.push({pos, size: node.nodeSize}); });
  if (updateOnly && !old.length) throw new Error('Insert a table of contents first.');
  const headings = documentHeadings(tr.doc);
  if (!headings.length) throw new Error('Apply Heading 1, 2 or 3 to your headings first.');
  if (headings.length > 500) throw new Error('A table of contents supports up to 500 headings.');
  const position = old[0]?.pos ?? 0;
  for (const item of old.reverse()) tr.delete(item.pos, item.pos + item.size);
  assignAnchors(tr);
  const entries = [schema.nodes.paragraph.create({tocEntry: true}, schema.text('Contents', [schema.marks.bold.create()]))];
  for (const heading of documentHeadings(tr.doc)) {
    entries.push(schema.nodes.paragraph.create({tocEntry: true, indent: Math.min(5, heading.level - 1)},
      schema.text(heading.text, [schema.marks.link.create({href: '#' + heading.id, target: null})])));
  }
  tr.insert(position, entries);
  editor.view.dispatch(closeHistory(tr));
}

export function insertCrossReference(editor, position) {
  const {tr, schema, selection} = editor.state;
  const heading = tr.doc.nodeAt(position);
  if (heading?.type.name !== 'heading' || !heading.textContent.trim()) throw new Error('That heading is no longer available.');
  assignAnchors(tr);
  const target = tr.doc.nodeAt(position);
  tr.replaceWith(selection.from, selection.to, schema.text(target.textContent,
    [schema.marks.link.create({href: '#' + target.attrs.anchorId, target: null})]));
  editor.view.dispatch(closeHistory(tr.scrollIntoView()));
  editor.commands.focus(undefined, {scrollIntoView: false});
}
