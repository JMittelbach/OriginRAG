from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

MAX_PAPERS = 3
_PAPER_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z')
_STOP = frozenset('the and with from this that these those what which about paper papers find show please die der das und mit von welche zum zur bitte'.split())


def paper_ids(payload):
    ids = payload.get('papers', [])
    if (not isinstance(ids, list) or len(ids) > MAX_PAPERS
            or any(not isinstance(key, str) or not _PAPER_ID.fullmatch(key) for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Choose up to three different papers from the Library.')
    return ids


def metadata(paper):
    return {**{key: str(paper.get(key) or '')[:800] for key in
               ('paper_id', 'title', 'author', 'year', 'journal', 'doi')},
            'local_pdf': bool(paper.get('local_pdf'))}


def search(catalogue, query, *, allowed=None):
    if not isinstance(query, str) or len(query) > 160 or any(ord(char) < 32 for char in query):
        raise ValueError('Use a Library search of up to 160 characters.')
    query = query.strip().casefold()
    if query and len(query) < 2:
        return {'items': [], 'total': 0}
    if not query and allowed is None:
        return {'items': [], 'total': 0}
    tokens = query.split()
    matches = []
    for paper in catalogue:
        if allowed is not None and paper.get('paper_id') not in allowed:
            continue
        fields = [str(paper.get(key) or '').casefold() for key in
                  ('title', 'author', 'journal', 'doi', 'year', 'paper_id', 'topic_label')]
        if not all(any(token in field for field in fields) for token in tokens):
            continue
        rank = (100 if query and query in fields[0] else 0) + sum(10 for token in tokens if token in fields[0])
        matches.append((-rank, fields[1], fields[0], metadata(paper)))
    matches.sort(key=lambda row: row[:3])
    return {'items': [row[3] for row in matches[:8]], 'total': len(matches)}


def _excerpts(paper_id, chunks_dir, query):
    if chunks_dir is None:
        return []
    root = Path(chunks_dir).resolve()
    path = root / (paper_id + '.jsonl')
    if not path.resolve().is_relative_to(root):
        return []
    terms = {word for word in re.findall(r'[^\W_]+', query.casefold()) if len(word) > 2 and word not in _STOP}
    ranked, consumed = [], 0
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return []
            for number in range(2048):
                line = handle.readline(100_001)
                if not line:
                    break
                consumed += len(line.encode('utf-8'))
                if consumed > 4_000_000 or len(line) > 100_000:
                    break
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or row.get('paper_id') not in (None, '', paper_id):
                    return []
                section = str(row.get('section') or 'Selected passage')[:120]
                if re.search(r'reference|bibliograph|acknowledg', section, re.I):
                    continue
                text = ' '.join(str(row.get('text') or '').split())
                if not text:
                    continue
                folded = text.casefold()
                score = sum(min(3, folded.count(term)) for term in terms)
                score += 1 if 'abstract' in section.casefold() else 0
                excerpt = {'text': text[:900], 'section': section,
                           'chunk_id': str(row.get('chunk_id') or f'line_{number + 1}')[:180],
                           'page_start': row.get('page_start') if type(row.get('page_start')) is int else None,
                           'page_end': row.get('page_end') if type(row.get('page_end')) is int else None,
                           'truncated': len(text) > 900}
                ranked.append((-score, number, excerpt))
                if len(ranked) > 8:
                    ranked.sort(key=lambda item: item[:2])
                    del ranked[8:]
    except (OSError, ValueError, UnicodeError):
        return []
    return [item[2] for item in sorted(ranked, key=lambda item: item[:2])[:2]]


def prepare_sources(catalogue, ids, query, *, chunks_dir=None):
    papers = {row['paper_id']: row for row in catalogue if row.get('paper_id') in ids}
    if set(ids) != papers.keys():
        raise ValueError('A selected paper is no longer available in the Library. Search again.')
    sources = []
    for number, key in enumerate(ids, 1):
        passages = _excerpts(key, chunks_dir, query)
        for index, passage in enumerate(passages, 1):
            passage['source'] = f'L{number}.{index}'
        sources.append({**metadata(papers[key]), 'source': f'L{number}',
                        'coverage': 'indexed_excerpts' if passages else 'metadata_only',
                        'excerpts': passages})
    return sources
