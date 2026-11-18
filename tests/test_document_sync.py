import shutil
import threading

import pytest

from originrag.app_profiles import app_profile
from originrag.document_sync import DocumentSync, SUBFOLDER, SyncConflict
from originrag.folio import FolioStore, paragraph
from originrag.writing import WritingStore
from test_folio_http import folio_server as folio_server
import test_web_chat as http


def app(owner):
    return DocumentSync(owner, app_profile('full'), lambda: ())


def linked_instances(tmp_path):
    devices = []
    for name in ('work', 'home'):
        owner = tmp_path / name
        folder = tmp_path / ('folder-' + name)
        folder.mkdir()
        service = app(owner)
        service.run({'action': 'connect', 'folder': str(folder)})
        devices.append((service, owner, folder / SUBFOLDER))
    return devices


def transfer(source, dest, unchanged=()):
    for f in source.glob('*.origin1'):
        if f.name not in unchanged:
            shutil.copyfile(f, dest / f.name)


def test_quill_round_trip_offline_conflict_and_unavailable_folder(tmp_path):
    (work, w, a), (home, h, b) = linked_instances(tmp_path)
    store = WritingStore(w)
    doc = store.change({'action':'create_project', 'name':'Manuscript'}, ())
    files = {'main.tex': 'First draft', 'refs.bib':'@article{lee2024}', 'plot.png':'aGVsbG8='}
    store.change({**doc, 'action':'save_project', 'files':files}, ())
    def sync(service, key, action='sync', **fields):
        return service.run(dict(action=action, kind='write', id=key, **fields))
    sync(work, doc['id'])
    transfer(a, b)
    office_files = {f.name for f in a.glob('*.origin1')}
    second = sync(home, doc['id'], 'open')['id']
    def change(owner, key, text):
        st = WritingStore(owner)
        d = st.get(key)
        st.change({**d, 'action':'save_project', 'files':{**d['files'], 'main.tex':text}}, ())
    assert WritingStore(h).get(second)['files'] == files
    change(h, second, 'Home text')
    sync(home, second, 'auto')
    transfer(b, a, office_files)
    with pytest.raises(SyncConflict):
        sync(work, doc['id'], 'auto')
    assert sync(work, doc['id'])['received']
    assert store.get(doc['id'])['files']['main.tex'] == 'Home text'
    change(w, doc['id'], 'Work offline')
    change(h, second, 'Home offline')
    sync(work, doc['id'])
    sync(home, second)
    transfer(b, a, office_files)
    with pytest.raises(SyncConflict):
        sync(work, doc['id'])
    heads = work.run({'action':'status'})['documents']
    assert len(heads) == 2 and all(row['conflict'] for row in heads)
    recovered = [store.get(sync(work, doc['id'], 'copy', device=row['device'])['id']) for row in heads]
    assert {d['files']['main.tex'] for d in recovered} == {'Work offline', 'Home offline'}
    assert store.get(doc['id'])['files']['main.tex'] == 'Work offline'
    a.rename(tmp_path / 'unmounted')
    assert not work.run({'action':'status'})['available']
    with pytest.raises(ValueError, match='unavailable'):
        sync(work, doc['id'])
    assert not a.exists()
    assert not work.run({'action':'disconnect'})['folder']


def test_folio_integrity_and_no_silent_overwrite(tmp_path):
    (work, w, a), (home, h, b) = linked_instances(tmp_path)
    st = FolioStore(w)
    d = st.change({'action':'create', 'title':'Research notes'})
    d = st.change({**d, 'action':'save', 'content':{'type':'doc', 'content':[paragraph('Results')]}})
    q = {'id':d['id'], 'kind':'folio'}
    work.run({**q, 'action':'sync'})
    transfer(a, b)
    key = home.run({**q, 'action':'open'})['id']
    assert FolioStore(h).get(key)['content'] == d['content']
    shared = next(b.glob('*.origin1'))
    header = shared.read_bytes().split(b'\n', 1)[0]
    shared.write_bytes(header + b'\n{}')
    with pytest.raises(ValueError, match='Incomplete'):
        home.run({**q, 'action':'copy', 'device':shared.name.split('.')[1]})
    assert FolioStore(h).get(key)['content'] == d['content']


@pytest.mark.parametrize('folio_server', [True], indirect=True)
def test_hosted_accounts_cannot_use_server_folders(folio_server):
    port, users, _, _ = folio_server
    assert http.http_request(port, 'GET', '/api/document-sync', headers=users['alice'])[0] == 403
    assert http.http_request(port, 'POST', '/api/document-sync', payload={'action':'status'}, headers=users['alice'])[0] == 403


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
def test_two_local_installations_sync_in_the_web_ui(folio_server, engine):
    pw = pytest.importorskip('playwright.sync_api')
    port, _, tmp, _ = folio_server
    folder = tmp / 'private shared folder'
    folder.mkdir()
    base = f'http://127.0.0.1:{port}'
    assert http.http_request(port, 'POST', '/api/document-sync', payload={'action':'connect','folder':str(folder)})[0] == 403
    assert not (folder / SUBFOLDER).exists()
    web = http.web_chat
    second = web.WebChatApp(web.WebChatSettings(backend='template'), tmp / 'second-installation')
    server = web.create_server('127.0.0.1', 0, web.build_handler(second, profile='full'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pw.sync_playwright() as p:
            browser = getattr(p, engine).launch()
            work, home = browser.new_page(), browser.new_page()
            work.goto(base + '/folio')
            home.goto(f'http://127.0.0.1:{server.server_port}/folio')
            work.get_by_role('button', name='New document', exact=True).click()
            editor = work.get_by_role('textbox', name='Document content')
            editor.fill('At the office')
            for page in (work, home):
                page.locator('#globalSettings').click()
                page.locator('#documentSync summary').click()
                page.locator('#docSyncFolder').fill(str(folder))
                page.locator('#docSyncLink').click()
                pw.expect(page.locator('#docSyncPath')).to_have_text(str(folder))
            work.locator('#docSyncNow').click()
            pw.expect(work.locator('#docSyncNotice')).to_contain_text('Saved to')
            home.locator('#docSyncRefresh').click()
            home.locator('#docSyncList').get_by_role('button', name='Open', exact=True).click()
            other = home.get_by_role('textbox', name='Document content')
            pw.expect(other).to_have_text('At the office')
            other.fill('Finished at home')
            home.wait_for_function("() => document.getElementById('docSyncNotice').textContent.includes('Saved to the sync folder')")
            work.locator('#docSyncNow').click()
            pw.expect(editor).to_have_text('Finished at home')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
