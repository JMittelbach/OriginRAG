import re

NAVIGATION = '''<header id="globalNavigation" class="origin-navigation">
<a class="origin-brand" href="/chat">Origin</a>
<nav aria-label="Your workspace"><a href="/chat" data-feature="rag">OriginRAG</a><a href="/bookshelf">Bookshelf</a><a href="/library">Library</a><a href="/write" data-feature="write">OriginWrite</a></nav>
<div class="origin-global-actions"><button id="appearanceButton" type="button" aria-haspopup="dialog" aria-controls="appearanceDialog" aria-expanded="false">Appearance</button><button id="globalSettings" type="button">Settings</button><button id="globalAccount" type="button">Account</button></div>
</header>
<dialog id="globalSettingsDialog" class="origin-global-dialog"><h2>Workspace settings</h2>
<label>Start page<select id="globalStartPage"><option value="/chat" data-feature="rag">OriginRAG</option><option value="/bookshelf">Bookshelf</option><option value="/library">Library</option><option value="/write" data-feature="write">OriginWrite</option></select></label>
<label><input type="checkbox" id="globalReduceMotion"> Reduce animations</label><p>Saved in this browser across all four areas. Answer settings remain in OriginRAG.</p><form method="dialog"><button>Done</button></form></dialog>
<dialog id="globalAccountDialog" class="origin-global-dialog"><h2>Your account</h2><p id="globalAccountDetails"></p><p id="globalAccountError" role="alert"></p><button id="globalSignOut" hidden>Sign out</button><form method="dialog"><button>Close</button></form></dialog>'''


def with_navigation(html):
    html = re.sub(r'<button[^>]*id="appearanceButton"[^>]*>Appearance</button>', '', html, count=1)
    html = re.sub(r'<a[^>]*href="/workspace"[^>]*>Workspace</a>', '', html, count=1)
    html = html.replace('</head>', '<link rel="stylesheet" href="/assets/workspace/navigation.css"></head>', 1)
    return html.replace('<body>', '<body>' + NAVIGATION, 1).replace('</body>', '<script type="module" src="/assets/workspace/navigation.mjs"></script></body>', 1)
