import re

NAVIGATION = '''<header id="globalNavigation" class="origin-navigation">
<a class="origin-brand" href="/chat">Origin</a>
<nav aria-label="Your workspace"><a href="/chat" data-feature="rag">OriginRAG</a><a href="/bookshelf">Bookshelf</a><a href="/library">Library</a><a href="/write" data-feature="write">Quill</a><a href="/folio" data-feature="folio">Folio</a></nav>
<div class="origin-global-actions"><button id="appearanceButton" type="button" aria-haspopup="dialog" aria-controls="appearanceDialog" aria-expanded="false">Appearance</button><button id="globalSettings" type="button">Settings</button><button id="globalAccount" type="button">Account</button></div>
</header>
<dialog id="globalSettingsDialog" class="origin-global-dialog"><form method="dialog" class="dialog-dismiss-form"><button class="dialog-close" aria-label="Close settings" title="Close settings">×</button></form><h2>Workspace settings</h2>
<label>Start page<select id="globalStartPage"><option value="/chat" data-feature="rag">OriginRAG</option><option value="/bookshelf">Bookshelf</option><option value="/library">Library</option><option value="/write" data-feature="write">Quill</option><option value="/folio" data-feature="folio">Folio</option></select></label>
<label><input type="checkbox" id="globalReduceMotion"> Reduce animations</label><p>Saved in this browser across your workspace. Answer settings remain in OriginRAG.</p><button id="globalModels" type="button">Models &amp; runtimes</button></dialog>
<dialog id="modelDialog" class="origin-global-dialog model-dialog" aria-labelledby="modelTitle">
<button id="modelClose" class="dialog-close" type="button" aria-label="Close models" title="Close models">×</button><div class="model-heading"><div><h2 id="modelTitle">Models</h2><p id="modelSelection" role="status"></p></div><button id="modelRefresh" type="button" aria-label="Refresh model list" title="Refresh">↻</button></div>
<div class="model-controls model-browser-controls"><label class="model-search-label"><span class="sr-only">Find models</span><input id="modelSearch" type="search" maxlength="80" placeholder="Search models…" autocomplete="off"></label>
<label><span class="sr-only">Show models</span><select id="modelFilter"><option value="all">Overview</option><option value="installed">Installed</option><option value="recommended">Suggested</option><option value="available">Downloads</option><option value="search">Catalogue</option></select></label></div>
<p id="modelSearchStatus" class="model-hint" role="status"></p>
<p id="modelWarning"></p><p id="modelError" role="alert"></p>
<section id="modelJob" hidden aria-live="polite"><p id="modelJobText"></p><progress id="modelProgress" max="1"></progress><button id="modelCancel" type="button">Cancel download</button></section>
<div id="modelList" aria-label="Models"></div>
<details class="model-advanced"><summary>Advanced</summary><div id="modelHardware" class="model-hardware"></div><p id="modelConfigured"></p><div class="model-controls"><label>Runtime <select id="modelProvider"><option value="ollama">Ollama</option><option value="colibri">Colibri · experimental</option></select></label>
<label id="modelMemoryLabel" hidden>Colibri memory <select id="modelMemory"><option value="fast">Faster · larger cache</option><option value="low_memory">Low memory · smaller cache</option></select></label>
<p id="modelAccess"></p></div>
<div class="model-footer"><button id="modelDefault" type="button">Use configured default</button><button id="modelStop" type="button">Stop idle Colibri</button></div>
<small class="model-hint">Memory fit is estimated for one 16K-context request. Longer contexts and simultaneous requests need more memory. Suggestions are not quality rankings. Catalogue searches send only your query to Ollama. Downloads always need confirmation.</small></details>
</dialog>
<dialog id="globalAccountDialog" class="origin-global-dialog"><form method="dialog" class="dialog-dismiss-form"><button class="dialog-close" aria-label="Close account" title="Close account">×</button></form><h2>Your account</h2><p id="globalAccountDetails" role="status"></p><p id="globalAccountError" role="alert"></p><button id="globalAccountRetry" type="button" hidden>Retry account connection</button><button id="globalSignOut" hidden>Sign out</button></dialog>'''


def with_navigation(html):
    html = re.sub(r'<button[^>]*id="appearanceButton"[^>]*>Appearance</button>', '', html, count=1)
    html = re.sub(r'<a[^>]*href="/workspace"[^>]*>Workspace</a>', '', html, count=1)
    html = html.replace('</head>', '<link rel="stylesheet" href="/assets/workspace/navigation.css">'
                        '<link rel="stylesheet" href="/assets/workspace/design-system.css">'
                        '<link rel="stylesheet" href="/assets/workspace/models.css"></head>', 1)
    return html.replace('<body>', '<body>' + NAVIGATION, 1).replace('</body>', '<script type="module" src="/assets/workspace/navigation.mjs"></script></body>', 1)


def navigation_for_profile(html, profile, *, accounts_enabled=True):
    if not accounts_enabled:
        html = html.replace('id="globalAccount" type="button"', 'id="globalAccount" type="button" hidden', 1)
    if profile.name == 'full':
        return html
    name = 'Quill' if profile.write else 'OriginRAG'
    header = NAVIGATION.split('</header>', 1)[0] + '</header>'
    if not accounts_enabled:
        header = header.replace('id="globalAccount" type="button"', 'id="globalAccount" type="button" hidden', 1)
    local = re.sub(r'<nav.*?</nav>',
                   '<nav aria-label="Library tools"><a href="/bookshelf">Bookshelf</a><a href="/library">Library</a></nav>',
                   header, flags=re.S)
    local = local.replace('id="globalNavigation"', 'id="applicationHeader"')
    local = local.replace('href="/chat">Origin</a>', f'href="{profile.home}">{name}</a>')
    html = html.replace(header, local)
    disabled = 'rag' if profile.write else 'write'
    html = re.sub(r'<option[^>]*data-feature="' + disabled + r'"[^>]*>.*?</option>', '', html)
    html = re.sub(r'<option[^>]*data-feature="folio"[^>]*>.*?</option>', '', html)
    return html.replace('Workspace settings', f'{name} settings').replace(
        'Saved in this browser across your workspace. Answer settings remain in OriginRAG.',
        'Saved in this browser for this application.')
