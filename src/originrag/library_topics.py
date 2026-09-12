import hashlib
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache


STOPWORDS = frozenset("a an the and or of in on for to from with by via as at into across using use used based study studies analysis analyses method methods approach approaches new novel comprehensive reveals reveal enables enable data dataset datasets framework tool tools research review systematic introduction advances towards toward through their its is are be this that these those we our de der die das und von zu mit des eine ein et al".split())


def _features(title):
    words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", title.casefold(), re.UNICODE)
    words = [w for w in words if len(w) > 2 and not w.isdigit()]
    features = Counter(w for w in words if w not in STOPWORDS)
    features.update(f"{a} {b}" for a, b in zip(words, words[1:]) if a not in STOPWORDS and b not in STOPWORDS)
    return features


def _unit(vector):
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {k: v / norm for k, v in vector.items()} if norm else {}


def _dot(a, b):
    return sum(value * b.get(key, 0.0) for key, value in a.items())


@lru_cache(maxsize=4)
def _discover(titles, target):
    features = [_features(title) for title in titles]
    df = Counter(term for row in features for term in row)
    vocabulary = set(sorted(df, key=lambda term: (-df[term], term))[:6000])
    vectors = [_unit({term: (1 + math.log(count)) * (1 + math.log((1 + len(titles)) / (1 + df[term])))
                     for term, count in row.items() if term in vocabulary}) for row in features]
    active = [i for i, row in enumerate(vectors) if row]
    if not active:
        return tuple(("unclassified", "Unclassified") for _ in titles)
    k = max(1, min(target - int(len(active) < len(titles)), len(active), max(1, round(math.sqrt(len(active) / 3)))))
    density = {i: sum(df[term] for term in features[i]) / max(1, len(features[i])) for i in active}
    seeds = [max(active, key=lambda i: (density[i], -i))]
    nearest = {i: _dot(vectors[i], vectors[seeds[0]]) for i in active}
    while len(seeds) < k:
        seed = max(active, key=lambda i: ((1 - nearest[i]) * math.sqrt(density[i]), -i))
        if seed in seeds or nearest[seed] > .999999:
            break
        seeds.append(seed)
        for i in active:
            nearest[i] = max(nearest[i], _dot(vectors[i], vectors[seed]))
    centers = [vectors[i] for i in seeds]
    assignment = None
    for _ in range(16):
        current = {i: max(range(len(centers)), key=lambda j: (_dot(vectors[i], centers[j]), -j)) for i in active}
        if current == assignment:
            break
        assignment = current
        sums = [defaultdict(float) for _ in centers]
        for i, group in assignment.items():
            for term, weight in vectors[i].items():
                sums[group][term] += weight
        centers = [_unit(row) if row else centers[j] for j, row in enumerate(sums)]
    labels = {}
    used = set()
    for group in sorted(set(assignment.values())):
        members = [i for i in active if assignment[i] == group]
        counts = Counter(term for i in members for term in features[i])
        ranked = sorted(counts, key=lambda term: (-(counts[term] / len(members)) * math.log(1 + len(titles) / df[term]) * (1.5 if " " in term else 1), term))
        chosen = []
        for term in ranked:
            if not any(set(term.split()) & set(old.split()) for old in chosen):
                chosen.append(term)
            if len(chosen) == 2:
                break
        label = " / ".join(term[0].upper() + term[1:] for term in chosen) or "Unclassified"
        if label in used:
            label += f" ({group + 1})"
        used.add(label)
        identifier = "topic_" + hashlib.sha256(label.encode()).hexdigest()[:12]
        labels[group] = (identifier, label)
    return tuple(labels[assignment[i]] if i in assignment else ("unclassified", "Unclassified") for i in range(len(titles)))


def assign_library_topics(records, target=10):
    """Discover local browsing groups from titles, independently of legacy categories."""
    target = max(1, min(10, int(target)))
    source = list(records)
    titles = tuple(sorted({str(row.get("title") or "").strip() for row in source}))
    mapping = dict(zip(titles, _discover(titles, target))) if titles else {}
    return tuple(dict(row, topic=mapping[str(row.get("title") or "").strip()][0], topic_label=mapping[str(row.get("title") or "").strip()][1]) for row in source)
