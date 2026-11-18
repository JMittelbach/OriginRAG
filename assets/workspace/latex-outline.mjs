const heading = /^[ \t]*\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?[ \t]*(?:\[[^\]\n]*\][ \t]*)?\{/;
const literalStart = /^[ \t]*\\begin\{(verbatim\*?|Verbatim|lstlisting|minted|comment)\}/;
const formatting = /\\(?:emph|text(?:bf|it|tt|sc|sf|rm|md|up|normal))(?=\{)/y;

function titleAt(source, pos) {
  let depth = 1, title = '';
  // A half-typed opening brace must not consume the rest of a manuscript.
  const limit = Math.min(source.length, pos + 2048);
  while (pos < limit) {
    const c = source[pos++];
    if (c === '\\') {
      if (/[{}%\\]/.test(source[pos] || ' ')) title += source[pos++];
      else {
        formatting.lastIndex = pos - 1;
        if (formatting.test(source)) pos = formatting.lastIndex;
        else title += c;
      }
    } else if (c === '%') {
      while (pos < limit && source[pos] !== '\n') pos++;
      pos++;
    } else if (c === '{') depth++;
    else if (c === '}') {
      if (!--depth) return title.replace(/\s+/g, ' ').trim();
    } else title += c;
  }
  return '';
}

export function latexOutline(source) {
  const entries = [];
  let literal = '';
  for (let start = 0, line = 1; start < source.length && entries.length < 200; line++) {
    const newline = source.indexOf('\n', start);
    const end = newline === -1 ? source.length : newline;
    const text = source.slice(start, end);
    if (literal) {
      if (text.trimStart().startsWith('\\end{' + literal + '}')) literal = '';
    } else {
      literal = literalStart.exec(text)?.[1] || '';
      const match = !literal && heading.exec(text);
      if (match) {
        const title = titleAt(source, start + match[0].length);
        if (title) entries.push({kind: match[1], title, line});
      }
    }
    start = end + 1;
  }
  return entries;
}
