function uncomment(source) {
  return source.split('\n').map(line => {
    for (let i = 0; i < line.length; i++) {
      if (line[i] !== '%') continue;
      let escapes = 0;
      for (let j = i - 1; j >= 0 && line[j] === '\\'; j--) escapes++;
      if (!(escapes % 2)) return line.slice(0, i);
    }
    return line;
  }).join('\n');
}

export function bibliographyOptions(files) {
  const options = new Map();
  for (const [filename, content] of Object.entries(files || {})) {
    if (!/\.bib$/i.test(filename) || typeof content !== 'string') continue;
    const source = uncomment(content);
    // Scan whole entries so an @ in a title or comment cannot invent a key.
    const pattern = /@([A-Za-z]+)\s*([({])/g;
    let match;
    while ((match = pattern.exec(source))) {
      const start = pattern.lastIndex, close = match[2] === '{' ? '}' : ')';
      let depth = 1, brace = 0, quoted = false, end = start;
      for (; end < source.length && depth; end++) {
        const c = source[end];
        if (c === '\\') { end++; continue; }
        if (c === '"' && !brace) quoted = !quoted;
        if (quoted) continue;
        if (match[2] === '(' && c === '{') brace++;
        else if (match[2] === '(' && c === '}' && brace) brace--;
        else if (!brace && c === match[2]) depth++;
        else if (!brace && c === close) depth--;
      }
      pattern.lastIndex = end;
      if (depth || /^(comment|string|preamble)$/i.test(match[1])) continue;
      const body = source.slice(start, end - 1);
      const key = /^\s*([A-Za-z0-9_.:+/-]+)\s*,/.exec(body)?.[1];
      if (!key || options.has(key)) continue;
      const field = name => {
        const value = new RegExp('\\b' + name + '\\s*=\\s*(?:\\{((?:[^{}]|\\{[^{}]*\\})*)\\}|"([^"]*)"|([^,\\s]+))', 'i').exec(body);
        return (value?.[1] || value?.[2] || value?.[3] || '').replace(/[{}]/g, '').replace(/\s+/g, ' ').trim();
      };
      const title = field('title'), author = field('author'), year = field('year');
      options.set(key, {label: key, type: 'reference', detail: [author, year].filter(Boolean).join(' · '), info: [title, filename].filter(Boolean).join('\n'), boost: 1});
    }
  }
  return [...options.values()];
}

export function citationContext(prefix) {
  const active = uncomment(prefix);
  if (active.length !== prefix.length) return null;
  const match = /(?<!\\)\\(?:[Cc]ite(?:[pt]|alt|alp|author|yearpar|year|num|alias)?|autocite|parencite|textcite|footcite|nocite)[*]?(?:\s*\[[^\]\n]*\]){0,2}\s*\{([^{}\n]*)$/.exec(prefix);
  if (!match) return null;
  const fragment = match[1].split(',').at(-1);
  if (!/^\s*[A-Za-z0-9_.:+/-]*$/.test(fragment)) return null;
  const word = fragment.trimStart();
  return {from: prefix.length - word.length, used: match[1].split(',').slice(0, -1).map(key => key.trim())};
}

export function citationCompletion(getFiles, isTex = () => true) {
  let previous = [], options = [];
  return context => {
    if (!isTex()) return null;
    const line = context.state.doc.lineAt(context.pos);
    const match = citationContext(context.state.sliceDoc(line.from, context.pos));
    if (!match) return null;
    const files = Object.fromEntries(Object.entries(getFiles()).filter(([name]) => /\.bib$/i.test(name)));
    const entries = Object.entries(files);
    if (entries.length !== previous.length || entries.some(([name, text], index) => previous[index]?.[0] !== name || previous[index]?.[1] !== text)) {
      options = bibliographyOptions(files); previous = entries;
    }
    return {from: line.from + match.from, options: options.filter(option => !match.used.includes(option.label)), validFor: /^[A-Za-z0-9_.:+/-]*$/};
  };
}
