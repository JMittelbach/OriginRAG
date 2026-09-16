from itertools import product
import random

import pytest

from originrag.writing_merge import MergeConflict, ambiguity_span, merge_text


@pytest.mark.parametrize(('base', 'local', 'remote'), [
    ('aaa', 'aa', 'aba'),
    ('aaa', 'aaaa', 'aba'),
    ('abab', 'ab', 'Xbab'),
    ('ababab', 'abab', 'abXbab'),
    ('😀😀😀', '😀😀', '😀λ😀'),
    ('Intro\nsame\nsame\nEnd\n', 'Intro\nsame\nEnd\n', 'Intro\nREMOTE\nsame\nEnd\n'),
    ('aaa bbb', 'aa bb', 'aaa bXb'),
    ('aaab', 'aab', 'aabba'),
    ('bbabbbabaaa', 'bbabbabaaa', 'bbabbaaaaa'),
])
def test_ambiguous_repeated_edit_overlapping_other_draft_needs_review(base, local, remote):
    with pytest.raises(MergeConflict):
        merge_text(base, local, remote)
    with pytest.raises(MergeConflict):
        merge_text(base, remote, local)


@pytest.mark.parametrize(('base', 'local', 'remote', 'expected'), [
    ('aaa\nANCHOR\nend', 'aa\nANCHOR\nend', 'aaa\nANCHOR\nEND', 'aa\nANCHOR\nEND'),
    ('abab\nUNIQUE\nend', 'ab\nUNIQUE\nend', 'abab\nUNIQUE\nEND', 'ab\nUNIQUE\nEND'),
    ('first  second\nlast', 'first second\nlast', 'first  second\nLAST', 'first second\nLAST'),
    ('first  second\nlast', 'first   second\nlast', 'first  second\nLAST', 'first   second\nLAST'),
    ('aaa', 'aaaa', 'aaaa', 'aaaa'),
    ('aaa', 'aa', 'aa', 'aa'),
    ('😀😀😀\nend', '😀😀\nend', '😀😀😀\nEND', '😀😀\nEND'),
])
def test_repetition_does_not_block_distant_edits_or_identical_results(base, local, remote, expected):
    assert merge_text(base, local, remote) == expected
    assert merge_text(base, remote, local) == expected


def test_short_repeated_single_edit_pairs_never_silently_change_operation_result():
    for length in range(2, 7):
        for chars in product('ab', repeat=length):
            base = ''.join(chars)
            for left in range(length):
                for right in range(left + 1, length):
                    for left_insert, right_insert in product(['', 'a', 'b', 'X'], repeat=2):
                        local = base[:left] + left_insert + base[left + 1:]
                        remote = base[:right] + right_insert + base[right + 1:]
                        if local == base or remote == base or local == remote:
                            continue
                        expected = base[:left] + left_insert + base[left + 1:right] + right_insert + base[right + 1:]
                        try:
                            merged = merge_text(base, local, remote)
                        except MergeConflict:
                            continue
                        assert merged == expected, (base, local, remote, left, right, expected, merged)


def test_ambiguity_envelopes_match_all_equivalent_replacement_positions():
    for length in range(1, 7):
        for chars in product('ab', repeat=length):
            base = ''.join(chars)
            for start in range(length + 1):
                for end in range(start, length + 1):
                    for size in range(4):
                        for inserted_chars in product('ab', repeat=size):
                            insertion = ''.join(inserted_chars)
                            changed = base[:start] + insertion + base[end:]
                            width = end - start
                            possible = []
                            for candidate in range(length - width + 1):
                                replacement = changed[candidate:candidate + size]
                                if len(replacement) == size and base[:candidate] + replacement + base[candidate + width:] == changed:
                                    possible.append(candidate)
                            assert ambiguity_span(base, (start, end, insertion), [1000]) == (
                                min(possible), max(possible) + width)


def test_ambiguity_scan_budget_is_bounded():
    with pytest.raises(MergeConflict, match='budget'):
        ambiguity_span('a' * 1000, (999, 1000, ''), [10])


def test_seeded_disjoint_variable_length_replacements_do_not_lose_edits():
    rng = random.Random(91151)
    for _ in range(100_000):
        base = ''.join(rng.choice('ab') for _ in range(rng.randrange(3, 18)))
        left_start, left_end, right_start, right_end = sorted(rng.sample(range(len(base) + 1), 4))
        left_insert = ''.join(rng.choice('abX') for _ in range(rng.randrange(4)))
        right_insert = ''.join(rng.choice('abY') for _ in range(rng.randrange(4)))
        local = base[:left_start] + left_insert + base[left_end:]
        remote = base[:right_start] + right_insert + base[right_end:]
        if local == base or remote == base or local == remote:
            continue
        expected = base[:left_start] + left_insert + base[left_end:right_start] + right_insert + base[right_end:]
        try:
            result = merge_text(base, local, remote)
        except MergeConflict:
            continue
        assert result == expected, (base, local, remote, expected, result)
