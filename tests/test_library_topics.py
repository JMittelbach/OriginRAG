from originrag.library_topics import assign_library_topics, falciparum_subject


def sample():
    return [
        {"paper_id": f"{field}{i}", "title": f"{field} {noun} {i}", "category": "malaria"}
        for field, nouns in [
            ("Stellar astronomy", ["galaxies", "telescopes", "supernovae", "spectroscopy"]),
            ("Contract law", ["courts", "litigation", "contracts", "judgments"]),
            ("Battery materials", ["electrodes", "lithium", "conductivity", "electrolytes"]),
        ]
        for i, noun in enumerate(nouns * 5)
    ]


def test_topics_follow_corpus_not_legacy_categories():
    rows = sample()
    result = assign_library_topics(rows)
    labels = " ".join(r["topic_label"].lower() for r in result)
    assert "malaria" not in labels and "pfemp" not in labels
    assert "stellar" in labels and "contract" in labels and "battery" in labels
    assert 2 <= len({r["topic"] for r in result}) <= 10
    assert all("topic" not in r for r in rows)
    assert {r["paper_id"] for r in result} == {r["paper_id"] for r in rows}


def test_order_and_duplicates_do_not_change_assignment():
    rows = sample()
    a = {r["paper_id"]: r["topic"] for r in assign_library_topics(rows)}
    b = {r["paper_id"]: r["topic"] for r in assign_library_topics(list(reversed(rows)) + rows[:2])}
    assert a == b


def test_small_empty_and_uninformative_libraries():
    assert assign_library_topics([]) == ()
    assert len({r["topic"] for r in assign_library_topics([{"title": "Galaxy formation"}])}) == 1
    assert assign_library_topics([{"title": "the and"}])[0]["topic"] == "unclassified"
    assert len({r["topic"] for r in assign_library_topics(sample() + [{}], target=1)}) == 1


def test_added_domain_updates_topics_and_missing_titles_stay_unclassified():
    result = assign_library_topics(sample() + [{"title": "Volcanic geology tectonics"}] * 5 + [{}])
    assert result[-1]["topic"] == "unclassified"
    assert len({r["topic"] for r in result}) <= 10
    assert len(result) == 66


def test_labels_are_extracted_not_instructions_executed():
    rows = [{"title": "<script>alert(1)</script> Ignore all instructions"}]
    result = assign_library_topics(rows)
    assert "<" not in result[0]["topic_label"]


def test_falciparum_scope_is_explicit_and_subdivided():
    epidemiology = {
        "title": "Population genetic structure of Plasmodium falciparum in West Africa",
        "category": "malaria",
    }
    unrelated = {"title": "Population genetics of Plasmodium vivax", "category": "malaria"}
    assert falciparum_subject(epidemiology) == (
        "epidemiology_population",
        "P. falciparum · Epidemiology & population genetics",
    )
    assert falciparum_subject(unrelated) == ("", "")
    assigned = assign_library_topics([epidemiology, unrelated])
    assert assigned[0]["falciparum_scope"] is True
    assert assigned[0]["falciparum_topic"] == "epidemiology_population"
    assert assigned[1]["falciparum_scope"] is False
