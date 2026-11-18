export function findTextMatches(doc, query, {matchCase = false, wholeWord = false} = {}) {
  if (!query) return [];
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const boundary = '[\\p{L}\\p{N}\\p{M}_]';
  const pattern = new RegExp(wholeWord ? `(?<!${boundary})${escaped}(?!${boundary})` : escaped, matchCase ? 'gu' : 'giu');
  const found = [];
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return;
    const text = node.textBetween(0, node.content.size, '', '\ufffc');
    for (const match of text.matchAll(pattern)) found.push({from: pos + 1 + match.index, to: pos + 1 + match.index + match[0].length});
    return false;
  });
  return found;
}
