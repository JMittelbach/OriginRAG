import hashlib
import math
import re
import random
from collections import Counter, defaultdict
from functools import lru_cache


STOPWORDS = frozenset(
    "a an the and or of in on for to from with by via as at into across using use used based study studies analysis analyses method methods approach approaches new novel comprehensive reveals reveal enables enable data dataset datasets framework tool tools research review systematic introduction advances towards toward through their its is are be this that these those we our de der die das und von zu mit des eine ein et al".split()
)


# A paper is included in the dedicated falciparum view only when its
# bibliographic text names the species explicitly.  This deliberately avoids
# pulling general malaria, vivax, host-immunity or methods papers into the
# species collection just because they happen to sit in a malaria directory.
FALCIPARUM_RE = re.compile(
    r"\b(?:plasmodium\s+falciparum|p\.?\s*falciparum|falciparum)\b",
    re.IGNORECASE,
)

FALCIPARUM_TOPIC_RULES = (
    (
        "epidemiology_population",
        "P. falciparum · Epidemiology & population genetics",
        (
            "epidemiolog",
            "population",
            "prevalence",
            "transmission",
            "haplot",
            "microsatellite",
            "multiplicity",
            "genetic diversity",
            "genetic structure",
            "genetic relatedness",
            "population structure",
            "population genetics",
            "identity by descent",
            "surveillance",
            "clonal",
            "demograph",
        ),
    ),
    (
        "genomics_variation",
        "P. falciparum · Genomics & variation",
        (
            "genom",
            "whole-genome",
            "whole genome",
            "sequenc",
            "assembly",
            "microarray",
            "polymorphism",
            "variant",
            "copy number",
            "heterozygom",
            "resequenc",
            "genetic cross",
            "low-complexity",
            "telomere",
        ),
    ),
    (
        "transcriptomics_regulation",
        "P. falciparum · Transcriptomics & gene regulation",
        (
            "transcript",
            "rna-seq",
            "rna seq",
            "gene expression",
            "transcription",
            "translational",
            "microarray",
            "single-cell",
            "single cell",
            "transcription factor",
            "regulatory",
        ),
    ),
    (
        "proteomics_metabolism",
        "P. falciparum · Proteomics & metabolism",
        (
            "proteom",
            "phospho",
            "kinom",
            "mass spect",
            "metabol",
            "metabolite",
            "metabolic",
            "protein expression",
            "protein interaction",
            "complexome",
        ),
    ),
    (
        "epigenetics_chromatin",
        "P. falciparum · Epigenetics & chromatin",
        (
            "epigen",
            "chromatin",
            "histone",
            "nucleosome",
            "methyl",
            "acetyl",
            "heterochromatin",
            "telomere",
        ),
    ),
    (
        "cell_biology_life_cycle",
        "P. falciparum · Cell biology & life cycle",
        (
            "erythrocy",
            "gametocyt",
            "life cycle",
            "life-cycle",
            "cell division",
            "organel",
            "apicoplast",
            "export",
            "transport",
            "trafficking",
            "host cell",
            "stage",
        ),
    ),
    (
        "methods_annotation",
        "P. falciparum · Methods & annotation",
        (
            "annotation",
            "bioinform",
            "pipeline",
            "database",
            "computational",
            "modeling",
            "modelling",
            "network analysis",
            "method",
            "tool",
        ),
    ),
)


def falciparum_subject(row):
    """Return the explicit species scope and a stable subtopic for a record."""
    text = " ".join(
        str(row.get(field) or "")
        for field in ("title", "abstract", "journal", "category", "collection", "topics")
    ).strip()
    if not FALCIPARUM_RE.search(text):
        return "", ""
    folded = text.casefold()
    for key, label, terms in FALCIPARUM_TOPIC_RULES:
        if any(term in folded for term in terms):
            return key, label
    return "other", "P. falciparum · Other parasite biology"


def _features(title):
    words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", title.casefold(), re.UNICODE)
    words = [w for w in words if len(w) > 2 and not w.isdigit()]
    features = Counter(w for w in words if w not in STOPWORDS)
    features.update(
        f"{a} {b}" for a, b in zip(words, words[1:]) if a not in STOPWORDS and b not in STOPWORDS
    )
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
    vectors = [
        _unit(
            {
                term: (1 + math.log(count)) * (1 + math.log((1 + len(titles)) / (1 + df[term])))
                for term, count in row.items()
                if term in vocabulary
            }
        )
        for row in features
    ]
    active = [i for i, row in enumerate(vectors) if row]
    if not active:
        return tuple(("unclassified", "Unclassified") for _ in titles)
    k = max(
        1,
        min(
            target - int(len(active) < len(titles)),
            len(active),
            max(1, round(math.sqrt(len(active) / 3))),
        ),
    )

    def center(members):
        result = defaultdict(float)
        for i in members:
            for term, weight in vectors[i].items():
                result[term] += weight
        return _unit(result)

    best_score = -1
    assignment = {}
    for restart in range(4):
        rng = random.Random(restart)
        seeds = [rng.choice(active)]
        nearest = {i: _dot(vectors[i], vectors[seeds[0]]) for i in active}
        while len(seeds) < k:
            weights = [max(0, 1 - nearest[i]) ** 2 for i in active]
            if sum(weights) < 1e-10:
                break
            seed = rng.choices(active, weights=weights, k=1)[0]
            seeds.append(seed)
            for i in active:
                nearest[i] = max(nearest[i], _dot(vectors[i], vectors[seed]))
        centers = [vectors[i] for i in seeds]
        previous = None
        for _ in range(20):
            current = {
                i: max(range(len(centers)), key=lambda j: (_dot(vectors[i], centers[j]), -j))
                for i in active
            }
            if current == previous:
                break
            previous = current
            parts = [[i for i in active if current[i] == j] for j in range(len(centers))]
            centers = [center(part) if part else centers[j] for j, part in enumerate(parts)]
        score = sum(_dot(vectors[i], centers[current[i]]) for i in active)
        if score > best_score:
            best_score = score
            assignment = current
    labels = {}
    used = set()
    for group in sorted(set(assignment.values())):
        members = [i for i in active if assignment[i] == group]
        counts = Counter(term for i in members for term in features[i])
        ranked = sorted(
            counts,
            key=lambda term: (
                -(counts[term] / len(members))
                * math.log(1 + len(titles) / df[term])
                * (1.5 if " " in term else 1),
                term,
            ),
        )
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
    return tuple(
        labels[assignment[i]] if i in assignment else ("unclassified", "Unclassified")
        for i in range(len(titles))
    )


def assign_library_topics(records, target=10):
    """Discover local browsing groups from titles, independently of legacy categories."""
    target = max(1, min(10, int(target)))
    source = list(records)
    if target == 1:
        assigned = []
        for row in source:
            falciparum_topic, falciparum_topic_label = falciparum_subject(row)
            assigned.append(
                dict(
                    row,
                    topic="all",
                    topic_label="All papers",
                    falciparum_topic=falciparum_topic,
                    falciparum_topic_label=falciparum_topic_label,
                    falciparum_scope=bool(falciparum_topic),
                )
            )
        return tuple(assigned)
    titles = tuple(sorted({str(row.get("title") or "").strip() for row in source}))
    mapping = dict(zip(titles, _discover(titles, target))) if titles else {}
    assigned = []
    for row in source:
        falciparum_topic, falciparum_topic_label = falciparum_subject(row)
        assigned.append(
            dict(
                row,
                topic=mapping[str(row.get("title") or "").strip()][0],
                topic_label=mapping[str(row.get("title") or "").strip()][1],
                falciparum_topic=falciparum_topic,
                falciparum_topic_label=falciparum_topic_label,
                falciparum_scope=bool(falciparum_topic),
            )
        )
    return tuple(assigned)
