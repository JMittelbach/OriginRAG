import pytest

from originrag.collection_uploads import CollectionUploads
from originrag.writing import WritingStore


def test_uploaded_paper_links_only_to_its_owners_collection(tmp_path):
    catalogue = [{'paper_id': 'P1', 'title': 'A paper'}]
    alice, bob = WritingStore(tmp_path / 'alice'), WritingStore(tmp_path / 'bob')
    a = alice.change({'action': 'create_collection', 'name': 'Research'}, catalogue)
    b = bob.change({'action': 'create_collection', 'name': 'Research'}, catalogue)
    ledger = CollectionUploads(tmp_path / 'uploads.sqlite')
    ledger.add('upload-a', tmp_path / 'alice', a['id'])
    ledger.add('upload-b', tmp_path / 'bob', b['id'])
    results = [{'upload_id': 'upload-a', 'status': 'accepted', 'paper_id': 'P1'},
               {'upload_id': 'upload-b', 'status': 'review_required', 'paper_id': 'P1'}]
    ledger.finish(results, catalogue)
    assert alice.get(a['id'], 'collections')['papers'] == ['P1']
    assert bob.get(b['id'], 'collections')['papers'] == []
    assert [r['collection_link_state'] for r in results] == ['added', 'not_added']
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(KeyError):
        ledger.add('foreign', tmp_path / 'bob', a['id'])


def test_unknown_upload_does_not_link_by_filename(tmp_path):
    store = WritingStore(tmp_path / 'alice')
    c = store.change({'action': 'create_collection', 'name': 'Research'}, [])
    ledger = CollectionUploads(tmp_path / 'uploads.sqlite')
    ledger.add('correct-id', tmp_path / 'alice', c['id'])
    result = {'upload_id': 'other-id', 'status': 'accepted', 'paper_id': 'P1'}
    ledger.finish([result], [{'paper_id': 'P1'}])
    assert store.get(c['id'], 'collections')['papers'] == []
    assert 'collection_link_state' not in result


def test_pdf_import_indexes_and_links_without_browser_polling(tmp_path, monkeypatch):
    import io
    from test_web_chat import web_chat
    from test_metadata_fulltext_upgrade import setup_library
    from test_oa_import import pdf_bytes
    source, _ = setup_library(tmp_path, monkeypatch)
    source.unlink()
    owner = tmp_path / 'alice'
    store = WritingStore(owner)
    collection = store.change({'action': 'create_collection', 'name': 'Reading'}, [])
    monkeypatch.setattr(web_chat, 'library_catalog', lambda: [{'paper_id': 'EXISTING_1', 'title': 'A study'}])
    manager = web_chat.LibrarySyncManager(source.parent, auto_worker=False,
        refresh_callback=lambda: None, read_cache_refresh=lambda: None)
    pdf = pdf_bytes()
    uploaded = manager.store_upload(io.BytesIO(pdf), content_length=len(pdf), filename='article.pdf',
        collection_directory=owner, collection=collection['id'], schedule_import=False)
    result = manager.run_pending_now()
    assert result['imported'] == 1
    assert result['results'][0]['upload_id'] == uploaded['upload_id']
    assert result['results'][0]['collection_link_state'] == 'added'
    assert store.get(collection['id'], 'collections')['papers'] == ['EXISTING_1']
    assert (tmp_path / 'data/chunks/EXISTING_1.jsonl').exists()
