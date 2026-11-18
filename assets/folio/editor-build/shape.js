import {Node} from '@tiptap/core';

const names = {rect: 'Rectangle', roundRect: 'Rounded rectangle', ellipse: 'Ellipse'};
const defaults = {geometry: 'rect', width: 160, height: 90, fill: '#729ad0', stroke: '#315887', weight: 2};
const number = (value, max) => Number.isInteger(value) && value >= 1 && value <= max;
const colour = value => typeof value === 'string' && /^(none|#[\da-f]{6})$/i.test(value);

export const FolioShape = Node.create({
  name: 'shape', group: 'block', atom: true, selectable: true, draggable: true,
  addAttributes() {
    return Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, {default: value, rendered: false}]));
  },
  parseHTML() { return [{tag: 'div[data-folio-shape]', getAttrs(dom) {
    const d = dom.dataset;
    if (!Object.hasOwn(names, d.folioShape) || !colour(d.fill) || !colour(d.stroke)
        || !number(Number(d.width), 2400) || !number(Number(d.height), 2400) || !number(Number(d.weight), 16)) return false;
    return {geometry: d.folioShape, fill: d.fill, stroke: d.stroke, width: Number(d.width), height: Number(d.height), weight: Number(d.weight)};
  }}]; },
  renderHTML({node}) {
    const {geometry, fill, stroke, width, height, weight} = node.attrs;
    const radius = geometry === 'ellipse' ? '50%' : geometry === 'roundRect' ? '12px' : '0';
    const paint = c => colour(c) && c !== 'none' ? c : 'transparent';
    return ['div', {'data-folio-shape': geometry, 'data-fill': fill, 'data-stroke': stroke,
      'data-width': width, 'data-height': height, 'data-weight': weight, role: 'img', 'aria-label': names[geometry],
      style: 'box-sizing:border-box;max-width:100%;print-color-adjust:exact;width:' + (number(width, 2400) ? width : 160) + 'px;height:' +
        (number(height, 2400) ? height : 90) + 'px;border-radius:' + radius + ';background-color:' + paint(fill) +
        ';border:' + (number(weight, 16) ? weight : 2) + 'px solid ' + paint(stroke)}];
  },
});
