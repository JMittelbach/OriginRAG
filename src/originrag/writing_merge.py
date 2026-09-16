from __future__ import annotations

import hashlib
from pathlib import PurePosixPath


class MergeConflict(ValueError):
    pass


def text_changes(before, after, budget=None):
    budget = [2_000_000] if budget is None else budget
    if before == after:
        return []
    start, old_end, new_end = 0, len(before), len(after)
    while start < old_end and start < new_end and before[start] == after[start]:
        start += 1
    while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
        old_end -= 1
        new_end -= 1
    if start == old_end or start == new_end:
        return [(start, old_end, after[start:new_end])]
    a, b = before[start:old_end], after[start:new_end]
    if len(a) + len(b) > 2_000_000:
        raise MergeConflict('This edit is too large to merge automatically.')
    frontier, trace, work, stored = {1: 0}, [], 0, 0
    for depth in range(min(len(a) + len(b), 512) + 1):
        stored += len(frontier)
        if stored > 100_000:
            break
        trace.append(frontier.copy())
        for diagonal in range(-depth, depth + 1, 2):
            work += 1
            budget[0] -= 1
            if work > 1_000_000 or budget[0] < 0:
                raise MergeConflict('This edit exceeds the automatic merge budget.')
            if diagonal == -depth or (diagonal != depth and frontier[diagonal - 1] < frontier[diagonal + 1]):
                x = frontier[diagonal + 1]
            else:
                x = frontier[diagonal - 1] + 1
            y = x - diagonal
            while x < len(a) and y < len(b) and a[x] == b[y]:
                work += 1
                budget[0] -= 1
                if work > 1_000_000 or budget[0] < 0:
                    raise MergeConflict('This edit exceeds the automatic merge budget.')
                x, y = x + 1, y + 1
            frontier[diagonal] = x
            if x < len(a) or y < len(b):
                continue
            edits = []
            for distance in range(depth, 0, -1):
                previous, k = trace[distance], x - y
                previous_k = k + 1 if k == -distance or (k != distance and previous[k - 1] < previous[k + 1]) else k - 1
                previous_x = previous[previous_k]
                previous_y = previous_x - previous_k
                edits.append((previous_x, b[previous_y] if previous_k == k + 1 else None))
                x, y = previous_x, previous_y
            changes = []
            for at, insert in reversed(edits):
                at += start
                if not changes or changes[-1][1] != at:
                    changes.append([at, at, ''])
                if insert is None:
                    changes[-1][1] = at + 1
                else:
                    changes[-1][2] += insert
            return [tuple(change) for change in changes]
    raise MergeConflict('This edit exceeds the automatic merge budget.')


def merge_value(base, local, remote):
    if local == remote or remote == base:
        return local
    if local == base:
        return remote
    raise MergeConflict('The same setting was changed in both drafts.')


def ambiguity_span(base, change, budget):
    start, end, insertion = change
    size = len(insertion)
    left = right = 0
    while start - left > 0:
        last = (insertion[size - left - 1] if left < size else base[start - left + size - 1]) if size else base[start - left - 1]
        if last != base[end - left - 1]:
            break
        left += 1
        budget[0] -= 1
        if budget[0] < 0:
            raise MergeConflict('Ambiguous repeated text exceeds the automatic merge budget.')
    while end + right < len(base):
        first = (insertion[right] if right < size else base[end + right - size]) if size else base[end + right]
        if first != base[start + right]:
            break
        right += 1
        budget[0] -= 1
        if budget[0] < 0:
            raise MergeConflict('Ambiguous repeated text exceeds the automatic merge budget.')
    return start - left, end + right


def merge_text(base, local, remote, budget=None):
    if local == remote or remote == base:
        return local
    if local == base:
        return remote
    budget = [2_000_000] if budget is None else budget
    left, right = text_changes(base, local, budget), text_changes(base, remote, budget)
    spans = {change: ambiguity_span(base, change, budget) for change in (*left, *right)}
    edits = list(left)
    for other in right:
        duplicate = False
        for own in left:
            if own == other:
                if spans[own] != own[:2]:
                    raise MergeConflict('Repeated text makes the shared edit position ambiguous.')
                duplicate = True
                break
            a, b = spans[own]
            c, d = spans[other]
            if ((own[0] == own[1] and c <= b and a <= d)
                    or (other[0] == other[1] and a <= d and c <= b)
                    or max(a, c) < min(b, d)):
                raise MergeConflict('The same passage was changed in both drafts.')
        if not duplicate:
            edits.append(other)
    pieces, position = [], 0
    for start, end, insertion in sorted(edits):
        pieces.extend((base[position:start], insertion))
        position = end
    pieces.append(base[position:])
    return ''.join(pieces)


def merge_files(base, local, remote, text_types):
    missing = object()
    result = {}
    budget = [2_000_000]
    for name in sorted(base.keys() | local.keys() | remote.keys()):
        before, ours, theirs = base.get(name, missing), local.get(name, missing), remote.get(name, missing)
        try:
            if ours == theirs or theirs == before:
                value = ours
            elif ours == before:
                value = theirs
            elif all(value is not missing for value in (before, ours, theirs)) and PurePosixPath(name).suffix.lower() in text_types:
                value = merge_text(before, ours, theirs, budget)
            else:
                raise MergeConflict('Conflicting file addition, deletion or binary asset change.')
        except MergeConflict as exc:
            raise MergeConflict(f'{name}: {exc}') from exc
        if value is not missing:
            result[name] = value
    return result


def project_basis_digest(data):
    digest = hashlib.sha256(b'originwrite-basis-v1\0')
    digest.update(data['main'].encode('utf-8') + b'\0')
    for name, content in sorted(data['files'].items()):
        digest.update(name.encode('utf-8') + b'\0')
        for start in range(0, len(content), 65536):
            digest.update(content[start:start + 65536].encode('utf-8'))
        digest.update(b'\0')
    return digest.hexdigest()
