import pytest

from originrag.analysis.chat_commands import controls, discussion_number, reading_selection, spelling_only
from originrag.analysis.question_understanding import protected_terms, preserves_protected_terms
from originrag.discovery.chat_search import web_search_request
from originrag.retrieval.paper_lookup import citation_lookup, citation_candidates
from originrag.retrieval.quick_list import more_list_request, paper_list_request


@pytest.mark.parametrize('message,topic,count', [
    ('Coudl you plase shwo me 6 papres about Thermal-X calibration', 'Thermal-X calibration', 6),
    ('couldyou showme 3 papers about BRCA1', 'BRCA1', 3),
    ('Kannstdu mir bitte drei Papre über Sensor-42 zeigen', 'Sensor-42', 3),
    ('Bittezeig mir drei Papre über PF3D7_0712100', 'PF3D7_0712100', 3),
    ('Zeigmir drei Paper übre Rate-X', 'Rate-X', 3),
    ('Plase shwo me 2 papres abotu XYZ-123', 'XYZ-123', 2),
    ('Please show me 2 papers about papres', 'papres', 2),
    ('Show me 3 papers about severe malaria', 'severe malaria', 3),
    ('Shwo\nme\t2 papres about Gene-A', 'Gene-A', 2),
    ('kannstdumir drei paper über AP2-G zeigen', 'AP2-G', 3),
    ('kannstdumirbitte drei paper über AP2-G zeiegn', 'AP2-G', 3),
    ('könntestdu mir drei Paper über ABC1XY finden bitte', 'ABC1XY', 3),
    ('kannstdumir drei paper über AP2-G zeigen und vergleichen', None, None),
    ('kannstdumir nicht drei paper über AP2-G zeigen', None, None),
    ('Please find papers in mylibary about microscopy', 'microscopy', 10),
])
def test_lists_repair_navigation_not_the_subject(message, topic, count):
    result = paper_list_request(message)
    if topic is None:
        assert result is None
    else:
        assert result is not None
        assert (result.topic, result.limit) == (topic, count)


@pytest.mark.parametrize('word', ['show', 'please', 'papers', 'library', 'summarize', 'online'])
def test_adjacent_transpositions_are_stable_where_unambiguous(word):
    from originrag.analysis.chat_commands import _CONTROLS, _near
    for i in range(len(word) - 1):
        typo = word[:i] + word[i+1] + word[i] + word[i+2:]
        candidates = [known for known in _CONTROLS if _near(typo, known)]
        if typo == word or candidates == [word]:
            assert controls(typo) == word


@pytest.mark.parametrize('literal', [
    'PF3D7_0712100', 'AP2-G', 'BRCA2', 'papres_27', 'SHWO', 'SUMMRAIZE',
    'https://example.org/papres/about', '10.1234/papres-ABC',
    '`shwo me papres`', '"sum marize"', '“showme papres”', '«papres»',
    '"pls can u"', '`plz could u`', 'https://example.org/pls',
    'not', 'without', 'nicht', 'keine', 'ohne', '0.05', '2026', 'third-party',
])
def test_literals_identifiers_numbers_and_exclusions_are_not_corrected(literal):
    assert controls(literal) == literal.casefold()


@pytest.mark.parametrize('message,number', [
    ('Couldyou disucss the secnod paper with me?', 2),
    ('Kannstdu das zwiete Paper einmal durchlseen?', 2),
    ('Can we discuss paper #2?', 2),
    ('Please read paper 12.', 12),
    ('Was steht im Paper 2?', 2),
    ('Could we discuss the second paper and the third?', None),
    ('Compare the second paper with the first paper', None),
    ('Please discuss paper 2 and 3', None),
    ('Please discuss paper 2, not paper 3', None),
    ('Bitte nicht das zweite Paper durchlesen', None),
    ('Does AP2-G affect the second phase?', None),
    ('Did they read 2 papers?', None),
    ('bitte lies paper2', 2),
    ('couldyou disucss thesecondpaper', 2),
    ('Could we discuss paper2 and paper3?', None),
    ('Read PAPER2 expression results', None),
    ('Read paper-2', None),
    ('Read paper2.5', None),
])
def test_numbered_discussion_keeps_target_and_exclusions(message, number):
    assert discussion_number(message) == number


def test_long_command_repairs_two_transpositions_without_a_model():
    result = reading_selection('Please summraiez the second paper briefly')
    assert result is not None and result.number == 2
    assert controls('sum marize') == 'summarize'


@pytest.mark.parametrize('message', [
    'kannstdumir bitte das zwietePaper zusammenfassen',
    'coudlyou please summraize the secnodpapre',
    'kannstdumirbitte das 2. paper zusammenfassen',
    'plase summmarise paper2',
    'sum ma rize the second paper',
    'bitte das zweite Paper zu sammen fassen',
])
def test_combined_typo_and_word_boundary_errors_keep_numbered_selection(message):
    selection = reading_selection(message)
    assert selection is not None and selection.number == 2


@pytest.mark.parametrize('message', [
    'Summarize the fifty paper', 'Summarize PAPER2', 'Summarize paper2.5',
    'Summarize paper-2', 'Summarize the second or the third paper',
    'Summarize paper2 and paper3', 'Do not summarize thesecondpaper',
])
def test_compound_controls_do_not_change_quantities_or_comparisons(message):
    assert reading_selection(message) is None


@pytest.mark.parametrize('original', [
    'fifty', 'thirteen', 'tausend', 'PAPER2', 'AP2-G', 'BRCA2',
    '"thesecondpaper"', '`sum ma rize`', 'https://example.org/paper2',
])
def test_additional_literals_are_not_split_or_renumbered(original):
    assert controls(original) == original.casefold()


@pytest.mark.parametrize('message,expected', [
    ('Shwo me 5 more papres', ('en', 5)),
    ('Zeigmir drei weitree Paper', ('de', 3)),
    ('Explain more about the second paper', None),
    ('Not five more papers', None),
])
def test_followup_pagination(message, expected):
    assert more_list_request(message) == expected


@pytest.mark.parametrize('message,external', [
    ('Plase searhc onlien for papers about sensor calibration', True),
    ('Couldyou find new papres on the internet about microsocpy', True),
    ('Shwo me papres in my libarry about sensors', False),
    ('Please do not searhc onlien for papers about sensors', False),
    ('Bitte nicht onlien nach Paper suchen', False),
    ('Bitte nicth onlien nach Paper suchen', False),
    ('Find papers wihtout internet search', False),
    ('Find papers about online learning', False),
    ('What is web scraping?', False),
    ('Find web scraping papers', False),
])
def test_typos_do_not_change_local_vs_web_permissions(message, external):
    assert bool(web_search_request(message)) == external


@pytest.mark.parametrize('message', [
    'Plase shwo me the latest abc1xy switching paper in Nature Comm',
    'Couldyou shwo me the latest abc1xy switching paper in Nature Comm',
    'Bittezeig mir das neuste abc1xy switchign paper aus natuer comm',
    'coudlyou show me the latest abc1xy switchign papre in natuercomm',
    'Show me the latest abc1xy switching paper in NatureCommunications',
])
def test_citation_typo_repair_is_grounded_in_catalogue(message):
    papers = [dict(paper_id='target', title='ABC1XY switching and thermal calibration',
                   journal='Nature Communications', year='2026'),
              dict(paper_id='other', title='ABC1XY switching and calibration',
                   journal='Nature Communications', year='2023')]
    request = citation_lookup(message)
    assert request is not None
    assert [p['paper_id'] for p in citation_candidates(request, papers)] == ['target']


def test_joined_journal_names_cannot_match_an_unrelated_venue():
    from originrag.retrieval.paper_lookup import venue_matches
    assert not venue_matches('natuercomm', 'Nature Medicine')
    assert not venue_matches('nature', 'Nature Communications')
    assert not venue_matches('biology', 'Nature Communications Biology')
    assert not venue_matches('PLOS', 'PLOS ONE')


@pytest.mark.parametrize('identifier', ['PF3D7_0712100', 'AP2-G', 'HLA-DRA2', 'Gene_17_A'])
def test_model_rewrites_must_preserve_complete_compound_identifiers(identifier):
    required = protected_terms('What does ' + identifier + ' do?')
    assert identifier.casefold() in required
    assert preserves_protected_terms(required, 'What does ' + identifier + ' do?')
    assert not preserves_protected_terms(required, 'What does ' + identifier[:-1] + ' do?')


@pytest.mark.parametrize('before,after', [
    ('not above 50', 'above 50'), ('below 50', 'below 500'),
    ('never seen', 'newer seen'), ('VAR2CSA', 'VAR2SCA'),
    ('AP2-G', 'AP2G'), ('p<0.05', 'p>0.05'), ('50%', '50'),
    ('"papres"', 'papres'), ('with RNA', 'without RNA'),
])
def test_spelling_only_fallback_cannot_change_constraints(before, after):
    assert not spelling_only(before, after)


@pytest.mark.parametrize('typed,correct', [('nicth', 'nicht'), ('wihtout', 'without'), ('exlcuding', 'excluding')])
def test_transposed_negations_remain_constraints(typed, correct):
    assert controls(typed) == correct
    assert controls('night') != 'nicht'


def test_bounded_edit_distance_matches_full_reference_calculation():
    from itertools import product
    from originrag.analysis.chat_commands import _two_edits

    def distance(a, b):
        grid = [[i + j if not i or not j else 0 for j in range(len(b) + 1)]
                for i in range(len(a) + 1)]
        for i in range(1, len(a) + 1):
            for j in range(1, len(b) + 1):
                grid[i][j] = min(grid[i-1][j] + 1, grid[i][j-1] + 1,
                                 grid[i-1][j-1] + (a[i-1] != b[j-1]))
                if i > 1 and j > 1 and a[i-1] == b[j-2] and a[i-2] == b[j-1]:
                    grid[i][j] = min(grid[i][j], grid[i-2][j-2] + 1)
        return grid[-1][-1]

    words = [''.join(chars) for n in range(4) for chars in product('abc', repeat=n)]
    words += ['summarize', 'summraiez', 'secondpaper', 'secnodpapre', 'zzzzzzzzzzzz']
    for a in words:
        for b in words:
            assert _two_edits(a, b) == (distance(a, b) <= 2), (a, b)


@pytest.mark.parametrize('topic,expected', [
    ('sptial transcritpomics', 'spatial transcriptomics'),
    ('sensor calibraiton', 'sensor calibration'),
    ('calibrations', None),
    ('BRCA2', None), ('SENO1', None), ('SENRO', None), ('sensor-A', None),
    ('"sensor calibraiton"', None), ('`calibraiton`', None),
    ('https://example.org/calibraiton', None), ('10.1234/calibraiton', None),
    ('unknownsubject', None),
])
def test_empty_list_spelling_uses_catalogue_without_rewriting_literals(topic, expected):
    from originrag.retrieval.quick_list import topic_spelling_candidate
    rows = [{'title': 'Spatial transcriptomics sensor calibration BRCA1 sensor-A'}]
    candidate = topic_spelling_candidate(topic, rows)
    assert (candidate[0] if candidate else None) == expected


def test_ambiguous_or_known_title_word_is_not_replaced():
    from originrag.retrieval.quick_list import topic_spelling_candidate
    rows = [{'title': 'Penguin migration'}, {'title': 'Penguin mitigation'},
            {'title': 'Dermal optics'}, {'title': 'Formal optics'}]
    assert topic_spelling_candidate('migration', rows) is None
    assert topic_spelling_candidate('fermal', rows) is None
