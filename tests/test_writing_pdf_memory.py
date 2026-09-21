import base64

import fitz
import pytest

from test_writing import workspace_server as workspace_server


def pdf_bytes(pages):
    with fitz.open() as pdf:
        for number in range(1, pages + 1):
            page = pdf.new_page()
            page.insert_text((72, 72), f'Page {number}: a bounded preview')
        return base64.b64encode(pdf.tobytes()).decode()


def test_pdf_preview_keeps_four_canvases_and_releases_distant_pages(workspace_server):
    playwright = pytest.importorskip('playwright.sync_api')
    base, _ = workspace_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/library')
        playwright.expect(page.locator('#libraryCount')).to_have_text('1 paper')
        page.evaluate('''async encoded => {
          const {createPDFPreview} = await import('/assets/workspace/pdf-preview.mjs');
          document.querySelector('#content').replaceChildren();
          const container = document.createElement('div'); container.id = 'pdfPages';
          container.style.cssText = 'height:600px;width:900px;overflow:auto;';
          const count = document.createElement('span'); count.id = 'previewCount';
          document.querySelector('#content').append(container, count);
          window.previewErrors = []; window.preview = createPDFPreview(container, count, error => previewErrors.push(error.message));
          await preview.load(Uint8Array.from(atob(encoded), ch=>ch.charCodeAt(0)));
        }''', pdf_bytes(60))
        playwright.expect(page.locator('#previewCount')).to_have_text('60 pages')
        playwright.expect(page.locator('.write-pdf-page')).to_have_count(60)
        page.locator('canvas[aria-label="PDF page 1"]').wait_for()
        for number in [30, 60, 1, 55]:
            page.locator(f'.write-pdf-page[data-page="{number}"]').evaluate('element => element.scrollIntoView({block:"start"})')
            page.locator(f'canvas[aria-label="PDF page {number}"]').wait_for()
            assert page.locator('#pdfPages canvas').count() <= 4
            pixels = page.locator('#pdfPages canvas').evaluate_all('nodes => nodes.reduce((sum, canvas) => sum + canvas.width * canvas.height, 0)')
            assert pixels <= 8_000_000
        assert page.locator('canvas[aria-label="PDF page 1"]').count() == 0
        page.evaluate("preview.zoom('0.5')")
        width = page.locator('.write-pdf-page[data-page="55"]').bounding_box()['width']
        page.evaluate("preview.zoom('2')")
        assert page.locator('.write-pdf-page[data-page="55"]').bounding_box()['width'] == pytest.approx(width * 4, abs=2)
        page.evaluate('async encoded => preview.load(Uint8Array.from(atob(encoded), ch=>ch.charCodeAt(0)))', pdf_bytes(2))
        playwright.expect(page.locator('.write-pdf-page')).to_have_count(2)
        playwright.expect(page.locator('#previewCount')).to_have_text('2 pages')
        page.evaluate('async () => { await preview.destroy(); }')
        assert page.locator('#pdfPages canvas').count() == 0
        assert page.evaluate('previewErrors') == []
        assert not errors
        browser.close()
