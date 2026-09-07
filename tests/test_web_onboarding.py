from html.parser import HTMLParser

from originrag.web_onboarding import MARKUP, SCRIPT, STYLE, with_onboarding


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_tour_and_discovery_are_accessible_native_dialogs():
    parser = Elements()
    parser.feed(with_onboarding("<head><style></style></head><body></body>"))
    dialogs = [attrs for tag, attrs in parser.elements if tag == "dialog"]
    assert {d["id"] for d in dialogs} == {"welcomeGuide", "discoveryDialog"}
    assert all(d.get("aria-labelledby") and d.get("aria-describedby") for d in dialogs)
    assert MARKUP and STYLE
    assert 'guide.addEventListener("cancel"' in SCRIPT
    assert 'returnFocus.focus()' in SCRIPT


def test_tour_is_per_browser_and_account_and_can_be_replayed():
    assert 'originrag.quick-tour.v1.${status.user?.user_id || "local"}' in SCRIPT
    assert 'localStorage.setItem(storageKey, "done")' in SCRIPT
    assert 'replay.textContent = "Quick tour"' in SCRIPT
    assert 'if (!seen &&' in SCRIPT
    assert 'catch {}' in SCRIPT


def test_empty_catalogue_and_metadata_only_states_are_distinct():
    assert 'total === 0' in SCRIPT
    assert 'Your library is empty.' in SCRIPT
    assert 'no full-text papers are ready' in SCRIPT
    assert 'banner.hidden = ready > 0' in SCRIPT
    assert 'originrag:library-stats' in SCRIPT
    assert 'Ask the host' in SCRIPT
    assert 'Indexed' not in MARKUP


def test_discovery_does_not_treat_results_as_downloads_or_render_untrusted_html():
    assert 'not downloaded PDFs' in MARKUP
    assert 'Coverage varies by field' in MARKUP
    assert 'sent to Crossref' in MARKUP
    assert 'innerHTML' not in SCRIPT
    assert 'title.textContent = paper.title' in SCRIPT
    assert 'url.hostname !== "doi.org"' in SCRIPT
    assert 'searchController?.abort()' in SCRIPT
    assert 'serial !== latestSearch' in SCRIPT


def test_find_papers_is_only_in_the_top_left_header():
    assert 'document.querySelector("header .header-left").append(findButton)' in SCRIPT
    assert 'banner.append(bannerText, add);' in SCRIPT
    assert 'const find = ' not in SCRIPT
