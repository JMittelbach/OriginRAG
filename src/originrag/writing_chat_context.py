from __future__ import annotations

import bisect
import json
from pathlib import PurePosixPath
import re


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def project_context(project, available, question, focus, selection, selected_range, budget):
    whole = [{'file': name, 'content': project['files'][name], 'complete': True} for name in sorted(available)]
    if encoded_size(whole) <= budget:
        return whole, {name: [(0, len(project['files'][name]))] for name in available}
    terms = sorted({word for word in re.findall(r'[^\W_]+', question.casefold()) if len(word) > 2}, key=len, reverse=True)[:32]
    named = {name for name in available if name.casefold() in question.casefold()}
    selected_file = selection['file'] if selection else None
    order = sorted(available, key=lambda name: (
        name != selected_file, name not in named, name != focus, name != project['main'],
        PurePosixPath(name).suffix.lower() not in {'.tex', '.bib'}, name))
    result, included = [], {}
    remaining = max(0, budget - 2)
    for name in order:
        if remaining < 300:
            break
        source = project['files'][name]
        full = {'file': name, 'content': source, 'complete': True}
        cost = encoded_size(full) + 1
        cap = min(remaining, 10000 if name in {project['main'], focus, selected_file} or name in named else 5000)
        if cost <= cap:
            result.append(full)
            included[name] = [(0, len(source))]
            remaining -= cost
            continue
        offsets, lines = [0], source.splitlines(keepends=True)
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        if not lines:
            continue
        targets = []
        if name == selected_file:
            targets.append(selected_range)
        preamble = re.search(r'\\begin\s*\{document\}', source) if name == project['main'] else None
        prefix_end = preamble.end() if preamble and preamble.end() <= 9000 else offsets[min(len(lines), 45)]
        targets.append((0, prefix_end))
        scored = []
        for index, line in enumerate(lines):
            folded = line.casefold()
            score = sum(1 for term in terms if term in folded)
            if score:
                scored.append((-score, index))
        for _, index in sorted(scored)[:6]:
            targets.append((offsets[max(0, index - 6)], offsets[min(len(lines), index + 15)]))
        entry = {'file': name, 'complete': False, 'excerpts': []}
        ranges = []
        for start, end in targets:
            if any(start >= lower and end <= upper for lower, upper in ranges):
                continue
            spare = cap - encoded_size(entry) - 180
            if spare < 100:
                break
            text = source[start:end].encode('utf-8')[:spare].decode('utf-8', errors='ignore')
            stop = start + len(text)
            if name == selected_file and not ranges and stop < selected_range[1]:
                raise ValueError('The selected passage is too large for this model context. Select a shorter passage.')
            if not text:
                continue
            excerpt = {'content': text, 'line_start': bisect.bisect_right(offsets, start),
                       'line_end': bisect.bisect_right(offsets, max(start, stop - 1)),
                       'complete_range': stop == end}
            while text and encoded_size({**entry, 'excerpts': [*entry['excerpts'], excerpt]}) + 1 > cap:
                if name == selected_file and not ranges:
                    raise ValueError('The selected passage is too large for this model context. Select a shorter passage.')
                text = text[:max(0, len(text) - max(32, len(text) // 8))]
                stop = start + len(text)
                excerpt.update(content=text, line_end=bisect.bisect_right(offsets, max(start, stop - 1)), complete_range=False)
            if text:
                entry['excerpts'].append(excerpt)
                ranges.append((start, stop))
        if ranges:
            result.append(entry)
            included[name] = ranges
            remaining -= encoded_size(entry) + 1
    if selection and not any(lower <= selected_range[0] and upper >= selected_range[1]
                             for lower, upper in included.get(selected_file, [])):
        raise ValueError('The selected passage is too large for this model context. Select a shorter passage.')
    if not result:
        raise ValueError('This request leaves no room for project context. Ask a shorter question.')
    return result, included
