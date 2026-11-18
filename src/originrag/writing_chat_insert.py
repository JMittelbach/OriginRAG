"""Resolve simple before/after requests against the manuscript, not model-quoted text."""
import re


_ACTION = re.compile(r'\b(?:insert|add|put|place|füge|fügen|einfügen|ergänz\w*|setz\w*)\b', re.I)
_POSITION = re.compile(
    r'\b(?P<side>after|before|nach|vor|hinter)\s+(?:(?:the|der|dem|den|das|die)\s+)?'
    r'(?:(?:line|command|zeile|befehl)\s+)?'
    r'(?P<target>`[^`\r\n]{1,240}`|\\[A-Za-z@]+\*?(?:\{[^\r\n{}]*\})*|'
    r'(?:title\s*page|titelblatt|titelseite)\b)'
    r'(?:\s*\(\s*`?(?P<clarifier>\\[A-Za-z@]+\*?(?:\{[^\r\n{}]*\})*)`?\s*\))?', re.I)
_NAMED_TEX = re.compile(r'(?<![\w./-])[\w./-]+\.tex\b', re.I)
_HEADING = re.compile(r'\\(?:part|chapter|(?:sub){0,2}section|(?:sub)?paragraph)\*?\{[^{}\r\n]{1,200}\}')
_NAMED_HEADING = re.compile(r'\b(chapter|section|subsection|subsubsection)\s+(?:titled|called|named|with the title|mit dem titel|namens)\s+', re.I)
_EXTRA = re.compile(r'\b(?:and|with|und|mit|sowie|include|including|also|plus|body|text|paragraphs?|abs[aä]tz\w*)\b', re.I)
_LITERAL_ENV = re.compile(r'\\(begin|end)\{(verbatim\*?|Verbatim|lstlisting|minted|comment)\}')
_DIRECT = re.compile(r'^\s*(?:(?:please|bitte)\s+)?(?:füge|insert|add)\b', re.I)
_QUALIFIED = re.compile(r"\b(?:if|unless|instead|not|don't|explain|wenn|falls|nicht|statt|erklär\w*)\b", re.I)


def insertion_point(question, project, chosen, focus):
    if not _ACTION.search(question) or _QUALIFIED.search(question):
        return None
    matches = list(_POSITION.finditer(question))
    if len(matches) != 1:
        return None
    match = matches[0]
    names = set(_NAMED_TEX.findall(question))
    if len(names) > 1:
        return None
    side = 'after' if match['side'].lower() in {'nach', 'after', 'hinter'} else 'before'
    target = match['clarifier'] or match['target']
    literal = target.startswith(('`', '\\'))
    name = next(iter(names), focus if literal else project['main'])
    if name not in chosen or not name.lower().endswith('.tex'):
        if names:
            raise ValueError('The requested file is not in this chat’s source context. Open it and select the insertion point.')
        return None
    tokens = {target.strip('`')} if literal else {
        r'\maketitle', r'\makethesistitle', r'\end{titlepage}' if side == 'after' else r'\begin{titlepage}'}
    source = project['files'][name]
    found = []
    literal_env = None
    for line in re.finditer(r'(?m)^[^\r\n]+', source):
        original = line[0]
        env = _LITERAL_ENV.match(original.lstrip())
        if literal_env or env:
            if env and env[1] == 'begin' and literal_env is None:
                literal_env = env[2]
            elif env and env[1] == 'end' and literal_env == env[2]:
                literal_env = None
            continue
        if original.strip() not in tokens and re.split(r'(?<!\\)%', original, maxsplit=1)[0].strip() not in tokens:
            continue
        found.append((original, line.start(), line.end()))
        if len(found) > 1:
            raise ValueError(f'The insertion point matches more than one line in {name}. Select the intended line and ask again.')
    if not found:
        if not literal or re.fullmatch(r'\\(?:begin|end)\{[^{}]*\}|\\(?:maketitle|makethesistitle)', next(iter(tokens))):
            raise ValueError(f'I couldn’t find the requested insertion point on its own line in {name}. Select the intended line and ask again.')
        return None
    original, start, end = found[0]
    newline = '\r\n' if source.startswith('\r\n', end) or source[max(0, start-2):start] == '\r\n' else '\n'
    rest = question[:match.start()] + ' ' + question[match.end():]
    headings = _HEADING.findall(rest)
    exact = headings[0] if (len(headings) == 1 and rest.count('\\') == 1
                            and not _EXTRA.search(rest.replace(headings[0], ''))) else None
    named = _NAMED_HEADING.search(rest)
    heading = named[1].lower() if named and not _EXTRA.search(_NAMED_HEADING.sub('', rest)) else None
    if heading and re.search(r'\b(?:unnumbered|unnummeriert\w*|ohne nummer\w*)\b', rest, re.I):
        heading += '*'
    return {'file': name, 'original': original, 'side': side, 'newline': newline, 'exact': exact, 'heading': heading,
            'direct': bool((exact or heading) and _DIRECT.match(question))}


def insertion_field(point):
    return 'heading_title' if point['heading'] else 'insertion'


def insertion_replacement(point, text):
    if point['heading'] and text.strip():
        escaped = re.sub(r'[#$%&_{}~^]', lambda m: {'~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}.get(
            m[0], '\\' + m[0]), text.strip())
        text = '\\' + point['heading'] + '{' + escaped + '}'
    text = text.replace('\r\n', '\n').strip('\n').replace('\n', point['newline'])
    if not text.strip():
        return point['original']
    anchor, nl = point['original'], point['newline']
    if point['side'] == 'after' and (text == anchor or text.startswith(anchor + nl)):
        return text
    if point['side'] == 'before' and (text == anchor or text.endswith(nl + anchor)):
        return text
    parts = (point['original'], text) if point['side'] == 'after' else (text, point['original'])
    return point['newline'].join(parts)
