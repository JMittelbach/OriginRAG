import re

NAVIGATION = '''<header id="globalNavigation" class="origin-navigation">
<a class="origin-brand" href="/chat">Origin</a>
<nav aria-label="Your workspace"><a href="/chat" data-feature="rag">OriginRAG</a><a href="/bookshelf">Bookshelf</a><a href="/library">Library</a><a href="/write" data-feature="write">Quill</a><a href="/folio" data-feature="folio">Folio</a></nav>
<div class="origin-global-actions"><button id="appearanceButton" type="button" aria-haspopup="dialog" aria-controls="appearanceDialog" aria-expanded="false">Appearance</button><button id="globalSettings" type="button">Settings</button><button id="globalAccount" type="button">Account</button></div>
</header>
<dialog id="globalSettingsDialog" class="origin-global-dialog"><h2>Workspace settings</h2>
<label>Start page<select id="globalStartPage"><option value="/chat" data-feature="rag">OriginRAG</option><option value="/bookshelf">Bookshelf</option><option value="/library">Library</option><option value="/write" data-feature="write">Quill</option><option value="/folio" data-feature="folio">Folio</option></select></label>
<label><input type="checkbox" id="globalReduceMotion"> Reduce animations</label><p>Saved in this browser across your workspace. Answer settings remain in OriginRAG.</p><button id="globalModels" type="button" data-feature="rag">Models &amp; runtimes</button><form method="dialog"><button>Done</button></form></dialog>
<dialog id="modelDialog" class="origin-global-dialog model-dialog" aria-labelledby="modelTitle">
<div class="model-heading"><div><h2 id="modelTitle">Models</h2><p>Choose the model that powers your answers</p></div><button id="modelClose" type="button" aria-label="Close models">Close</button></div>
<p id="modelSelection" role="status"></p><p id="modelAccess"></p>
<div class="model-controls"><label>Runtime <select id="modelProvider"><option value="ollama">Ollama</option><option value="colibri">Colibri · experimental</option></select></label>
<label id="modelMemoryLabel" hidden>Colibri memory <select id="modelMemory"><option value="fast">Faster · larger cache</option><option value="low_memory">Low memory · smaller cache</option></select></label>
<button id="modelRefresh" type="button">Refresh</button></div>
<p id="modelWarning"></p><p id="modelError" role="alert"></p><div id="modelList"></div>
<section id="modelJob" hidden aria-live="polite"><p id="modelJobText"></p><progress id="modelProgress" max="1"></progress><button id="modelCancel" type="button">Cancel download</button></section>
<div class="model-footer"><button id="modelDefault" type="button">Use configured default</button><button id="modelStop" type="button">Stop idle Colibri</button></div>
<small>Downloads and model changes affect this host. Speed is measured on this machine, not a model ranking. Request speed includes prompt processing; decode speed does not.</small>
</dialog>
<dialog id="globalAccountDialog" class="origin-global-dialog"><h2>Your account</h2><p id="globalAccountDetails" role="status"></p><p id="globalAccountError" role="alert"></p><button id="globalAccountRetry" type="button" hidden>Retry account connection</button><button id="globalSignOut" hidden>Sign out</button><form method="dialog"><button>Close</button></form></dialog>'''


def with_navigation(html):
    html = re.sub(r'<button[^>]*id="appearanceButton"[^>]*>Appearance</button>', '', html, count=1)
    html = re.sub(r'<a[^>]*href="/workspace"[^>]*>Workspace</a>', '', html, count=1)
    html = html.replace('</head>', '<link rel="stylesheet" href="/assets/workspace/navigation.css">'
                        '<link rel="stylesheet" href="/assets/workspace/design-system.css">'
                        '<link rel="stylesheet" href="/assets/workspace/models.css"></head>', 1)
    return html.replace('<body>', '<body>' + NAVIGATION, 1).replace('</body>', '<script type="module" src="/assets/workspace/navigation.mjs"></script></body>', 1)


def navigation_for_profile(html, profile):
    if profile.name == 'full':
        return html
    name = 'Quill' if profile.write else 'OriginRAG'
    header = NAVIGATION.split('</header>', 1)[0] + '</header>'
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
