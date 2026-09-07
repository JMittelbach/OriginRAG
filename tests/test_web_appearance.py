from html.parser import HTMLParser
from pathlib import Path

from pfempire.web_appearance import APPEARANCE_SCRIPT, APPEARANCE_STYLE, with_appearance


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_appearance_is_available_before_first_paint():
    result = with_appearance('<!doctype html><html><head>  <style>body {}</style></head><body></body></html>')
    assert result.index('originrag.appearance.v1') < result.index('<style>')
    assert 'document.documentElement.dataset.theme' in result
    assert 'document.documentElement.dataset.background' in result
    assert '"system", "light", "dark", "black"' in result
    assert '"artwork", "slate", "ocean", "forest", "plum"' in result
    assert APPEARANCE_STYLE in result
    assert 'const defaults = {mode: "dark", background: "city-cat"}' in result


def test_appearance_dialog_uses_native_keyboard_and_label_semantics():
    parser = Elements()
    parser.feed(with_appearance('<head>  <style>  </style></head><body></body>'))
    dialogs = [attrs for tag, attrs in parser.elements if tag == 'dialog']
    assert dialogs == [{'id': 'appearanceDialog', 'class': 'appearance-panel',
                        'aria-labelledby': 'appearanceTitle', 'aria-describedby': 'appearanceDescription'}]
    labels = {attrs.get('for') for tag, attrs in parser.elements if tag == 'label'}
    assert labels == {'appearanceMode', 'appearanceBackground'}
    assert any(tag == 'form' and attrs.get('method') == 'dialog' for tag, attrs in parser.elements)
    assert 'dialog.showModal()' in APPEARANCE_SCRIPT
    assert 'button.focus()' in APPEARANCE_SCRIPT


def test_preferences_are_local_validated_and_storage_errors_are_handled():
    assert 'modes.includes(value?.mode)' in APPEARANCE_SCRIPT
    assert 'backgrounds.includes(value?.background)' in APPEARANCE_SCRIPT
    assert 'catch { return false; }' in APPEARANCE_SCRIPT
    assert 'media.addEventListener("change"' in APPEARANCE_SCRIPT
    assert 'window.addEventListener("storage"' in APPEARANCE_SCRIPT
    assert 'fetch(' not in APPEARANCE_SCRIPT
    assert 'sessionStorage' not in APPEARANCE_SCRIPT


def test_black_and_light_modes_cover_surfaces_and_native_controls():
    assert 'color-scheme: light' in APPEARANCE_STYLE
    assert '--bg: #000000' in APPEARANCE_STYLE
    assert ':not([data-theme="black"]) main' in APPEARANCE_STYLE
    for selector in ('.bubble', '.paper-table-wrap', '.assurance-panel', '.profile-panel',
                     '.library-browser-panel', '.queue-panel', '.composer-inner', 'textarea'):
        assert selector in APPEARANCE_STYLE


def test_artwork_has_distinct_shipped_light_and_dark_assets():
    root = Path(__file__).resolve().parents[1]
    light_style = APPEARANCE_STYLE.split(':root[data-theme="light"] {', 1)[1].split('}', 1)[0]
    dark_style = APPEARANCE_STYLE.split(':root {', 1)[1].split('}', 1)[0]
    for mode, style in [('light', light_style), ('dark', dark_style)]:
        filename = f'originrag-background-{mode}.png'
        assert f'/assets/web/{filename}' in style
        assert (root / 'assets' / 'web' / filename).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert 'var(--artwork) right center / cover no-repeat' in APPEARANCE_STYLE
    assert 'main { background: var(--bg); }' in APPEARANCE_STYLE
    assert 'originrag-background.png' not in APPEARANCE_STYLE


def test_all_wallpapers_are_selectable_and_shipped():
    root = Path(__file__).resolve().parents[1]
    parser = Elements()
    parser.feed(with_appearance('<head><style></style></head><body></body>'))
    options = {attrs['value'] for tag, attrs in parser.elements if tag == 'option'}
    for name in ('mountain-moon', 'lunar-horizon', 'city-cat', 'digital-dna', 'moonlit-garden'):
        assert name in options
        assert f'"{name}"' in APPEARANCE_SCRIPT
        asset_name = 'moonlit-forest' if name == 'moonlit-garden' else name
        filename = f'originrag-wallpaper-{asset_name}.png'
        assert f'/assets/web/{filename}' in APPEARANCE_STYLE
        assert (root / 'assets' / 'web' / filename).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')


def test_sidebar_brand_has_no_subtitle():
    root = Path(__file__).resolve().parents[1]
    assert '<p class="sub">source-grounded answers</p>' not in (root / 'scripts' / 'web_chat.py').read_text()
