import csv
import threading

import pytest

from originrag.library_pdf import byte_range, open_registered_pdf


PDF = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'


def metadata(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(['paper_id', 'local_pdf_relpath'])
        writer.writerows(rows)


def test_only_registered_pdf_within_bibliography_can_be_opened(tmp_path):
    bib = tmp_path / 'bib'
    bib.mkdir()
    (bib / 'paper.pdf').write_bytes(PDF)
    (tmp_path / 'outside.pdf').write_bytes(PDF)
    (bib / 'link.pdf').symlink_to(tmp_path / 'outside.pdf')
    (bib / 'fake.pdf').write_bytes(b'not a PDF')
    registry = tmp_path / 'registry.tsv'
    metadata(registry, [('P1', 'paper.pdf'), ('escape', '../outside.pdf'), ('symlink', 'link.pdf'),
                        ('absolute', str(tmp_path / 'outside.pdf')), ('fake', 'fake.pdf'), ('empty', '')])
    with open_registered_pdf('P1', metadata_files=[registry], bib_roots=[bib]) as handle:
        assert handle.read() == PDF
    assert handle.closed
    for paper_id in ('unknown', '../outside.pdf', 'escape', 'symlink', 'absolute', 'fake', 'empty'):
        with pytest.raises(FileNotFoundError):
            with open_registered_pdf(paper_id, metadata_files=[registry], bib_roots=[bib]):
                pytest.fail('Unregistered or unsafe file was opened')


def test_registry_wins_and_missing_files_do_not_use_stale_paths(tmp_path):
    (tmp_path / 'old.pdf').write_bytes(PDF)
    (tmp_path / 'new.pdf').write_bytes(PDF + b'new')
    papers, registry = tmp_path / 'papers.tsv', tmp_path / 'registry.tsv'
    metadata(papers, [('P1', 'old.pdf')])
    metadata(registry, [('P1', 'new.pdf')])
    with open_registered_pdf('P1', metadata_files=[papers, registry], bib_roots=[tmp_path]) as handle:
        assert handle.read().endswith(b'new')
    (tmp_path / 'new.pdf').unlink()
    with pytest.raises(FileNotFoundError):
        with open_registered_pdf('P1', metadata_files=[papers, registry], bib_roots=[tmp_path]):
            pytest.fail('An outdated PDF was substituted')


@pytest.mark.parametrize('header, expected', [(None, (0, 99)), ('bytes=0-4', (0, 4)),
    ('bytes=5-', (5, 99)), ('bytes=-10', (90, 99)), ('bytes=-500', (0, 99)), ('bytes=90-500', (90, 99))])
def test_pdf_ranges(header, expected):
    assert byte_range(header, 100) == expected


@pytest.mark.parametrize('header', ['bytes=100-', 'bytes=-0', 'bytes=9-4', 'bytes=-', 'bytes=0-1,3-4', 'items=0-3'])
def test_bad_pdf_ranges(header):
    with pytest.raises(ValueError):
        byte_range(header, 100)


@pytest.mark.parametrize('authenticated', [True, False])
def test_pdf_http_access_content_and_ranges(tmp_path, monkeypatch, authenticated):
    import test_web_chat as helpers
    web = helpers.web_chat
    bib = tmp_path / 'bib'
    bib.mkdir()
    (bib / 'paper.pdf').write_bytes(PDF)
    metadata(tmp_path / 'data/metadata/paper_registry.tsv', [('P1', 'paper.pdf')])
    monkeypatch.setattr(web, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(web, 'get_bib_dir', lambda: bib)
    monkeypatch.setattr(web, 'load_project_env', lambda *args: None)
    auth = helpers.AuthStore(tmp_path / 'auth.sqlite') if authenticated else None
    if auth:
        auth.create_user('alice', 'quiet-lantern-river-orbit-47')
    settings = web.WebChatSettings(backend='template')
    registry = web.AuthenticatedWebChatRegistry(settings, tmp_path / 'sessions') if auth else None
    app = None if auth else web.WebChatApp(settings, tmp_path / 'sessions')
    server = web.create_server('127.0.0.1', 0, web.build_handler(app, auth_store=auth, app_registry=registry, secure_cookies=False))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    route = '/api/library/pdf?paper_id=P1'
    try:
        headers = helpers.login_headers(port, 'alice', 'quiet-lantern-river-orbit-47')[0] if auth else {}
        status, response_headers, body, _ = helpers.http_request(port, 'GET', route, headers=headers)
        assert status == 200 and body == PDF
        assert response_headers['content-type'] == 'application/pdf'
        assert response_headers['content-disposition'].startswith('inline;')
        assert response_headers['cache-control'] == 'no-store'
        assert response_headers['x-content-type-options'] == 'nosniff'
        status, response_headers, body, _ = helpers.http_request(port, 'GET', route, headers={**headers, 'Range': 'bytes=0-4'})
        assert status == 206 and body == b'%PDF-'
        assert response_headers['content-range'] == f'bytes 0-4/{len(PDF)}'
        assert helpers.http_request(port, 'GET', route, headers={**headers, 'Range': 'bytes=999-1000'})[0] == 416
        assert helpers.http_request(port, 'GET', '/api/library/pdf?paper_id=unknown', headers=headers)[0] == 404
        assert helpers.http_request(port, 'GET', route + '&path=secret', headers=headers)[0] == 400
        assert helpers.http_request(port, 'GET', route + '&paper_id=P2', headers=headers)[0] == 400
        if auth:
            assert helpers.http_request(port, 'GET', route)[0] == 401
        else:
            assert helpers.http_request(port, 'GET', route, headers={'Origin': 'https://evil.example'})[0] == 403
            assert helpers.http_request(port, 'GET', route, headers={'Host': 'evil.example'})[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
