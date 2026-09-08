from __future__ import annotations

import http.client
import io
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit

from originrag.provenance.library import normalize_doi


MAX_PDF_BYTES = 40 * 1024 * 1024
ACTIVE = {"queued", "finding", "downloading", "indexing"}


class ImportBusy(ValueError):
    pass


class ManualUploadRequired(ValueError):
    def __init__(self, message, *, reason="no_pdf", download_url=""):
        super().__init__(message)
        self.reason = reason
        self.download_url = download_url


class PublicDownloadError(ValueError):
    def __init__(self, status, url):
        super().__init__(f"Public download returned HTTP {status}")
        self.status = status
        self.url = url


def public_target(url: str) -> tuple[str, str, str]:
    if len(url) > 4096 or any(ord(char) < 33 for char in url):
        raise ValueError("Invalid download address")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Only public HTTPS downloads are supported")
    host = parsed.hostname.encode("idna").decode("ascii")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [item[4][0] for item in addresses]
    parsed_ips = [ipaddress.ip_address(ip) for ip in ips]
    if not ips or any(not ip.is_global or (isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo)) for ip in parsed_ips):
        raise ValueError("Non-public download address blocked")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return host, ips[0], path


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.resolved_ip = ip

    def connect(self):
        raw = socket.create_connection((self.resolved_ip, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_public(url: str, *, max_bytes: int, deadline: float) -> bytes:
    for _ in range(4):
        host, ip, path = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Download time limit reached")
        connection = PinnedHTTPSConnection(host, ip, min(10, remaining))
        try:
            connection.request("GET", path, headers={"User-Agent": "OriginRAG/0.1 OA import", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Missing redirect address")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise PublicDownloadError(response.status, url)
            length = response.getheader("Content-Length")
            if length and (int(length) < 0 or int(length) > max_bytes):
                raise ValueError("Download exceeds the size limit")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Download time limit reached")
                if connection.sock:
                    connection.sock.settimeout(min(10, remaining))
                block = response.read1(min(65536, max_bytes + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > max_bytes:
                    raise ValueError("Download exceeds the size limit")
            return bytes(data)
        finally:
            connection.close()
    raise ValueError("Too many download redirects")


def metadata(url: str, deadline: float) -> dict:
    value = json.loads(fetch_public(url, max_bytes=2_000_000, deadline=deadline))
    return value if isinstance(value, dict) else {}


def pmc_cloud_candidates(pmcid: str, doi: str, deadline: float):
    if not re.fullmatch(r"PMC[0-9]+", pmcid):
        return
    root = "https://pmc-oa-opendata.s3.amazonaws.com/"
    query = urlencode({"list-type": "2", "prefix": f"metadata/{pmcid}.", "max-keys": "100"})
    raw = fetch_public(root + "?" + query, max_bytes=500_000, deadline=deadline)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return
    listing = ET.fromstring(raw)
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    if listing.findtext("s:IsTruncated", namespaces=ns) != "false":
        return
    versions = []
    for entry in listing.findall("s:Contents/s:Key", ns):
        match = re.fullmatch(r"metadata/" + pmcid + r"\.([0-9]+)\.json", entry.text or "")
        if match:
            versions.append((int(match[1]), entry.text))
    if not versions:
        return
    version, key = max(versions)
    record = metadata(root + key, deadline)
    if (record.get("pmcid") != pmcid or record.get("version") != version
            or normalize_doi(record.get("doi")) != doi or record.get("is_pmc_openaccess") is not True
            or record.get("is_retracted") is not False):
        return
    url = urlsplit(str(record.get("pdf_url") or ""))
    if url.scheme != "s3" or url.netloc != "pmc-oa-opendata":
        return
    if url.path != f"/{pmcid}.{version}/{pmcid}.{version}.pdf":
        return
    yield root + url.path.lstrip("/") + ("?" + url.query if url.query else "")


def cc_licence(record: dict) -> bool:
    for item in record.get("license") or []:
        if not isinstance(item, dict):
            continue
        start = (item.get("start") or {}).get("timestamp")
        if start is not None:
            try:
                if not math.isfinite(float(start)) or float(start) > time.time() * 1000:
                    continue
            except (TypeError, ValueError):
                continue
        url = urlsplit(str(item.get("URL") or ""))
        if (url.scheme in {"http", "https"} and url.netloc == "creativecommons.org"
                and re.fullmatch(r"/(?:licenses/(?:by|by-sa|by-nd|by-nc|by-nc-sa|by-nc-nd)|publicdomain/(?:zero|mark))/[0-9.]+/?", url.path)):
            return True
    return False


class ArticleMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.dois = set()
        self.pdf_urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attrs = dict(attrs)
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        content = attrs.get("content") or ""
        if name in {"citation_doi", "dc.identifier", "dc.identifier.doi"}:
            doi = normalize_doi(content)
            if doi:
                self.dois.add(doi)
        if name == "citation_pdf_url" and content:
            self.pdf_urls.append(content)


def landing_pdf_candidates(url: str, doi: str, deadline: float):
    raw = fetch_public(url, max_bytes=2_000_000, deadline=deadline)
    parser = ArticleMetadata()
    parser.feed(raw.decode("utf-8", errors="replace"))
    if parser.dois != {doi}:
        return
    for link in parser.pdf_urls[:2]:
        yield urljoin(url, link)


def oa_candidates(doi: str, deadline: float):
    open_access = False
    checked_pmcids = set()
    landing_pages = []
    email = os.environ.get("ORIGINRAG_UNPAYWALL_EMAIL", "").strip()
    if email:
        try:
            data = metadata("https://api.unpaywall.org/v2/" + quote(doi, safe="") + "?" + urlencode({"email": email}), deadline)
            if normalize_doi(data.get("doi")) == doi and data.get("is_oa") is True:
                open_access = True
                locations = [data.get("best_oa_location")] + (data.get("oa_locations") or [])
                for item in locations:
                    if isinstance(item, dict):
                        if item.get("url_for_landing_page"):
                            landing_pages.append(str(item["url_for_landing_page"]))
                        if item.get("url_for_pdf"):
                            yield str(item["url_for_pdf"])
        except Exception:
            pass
    try:
        url = "https://api.openalex.org/works/" + quote("https://doi.org/" + doi, safe="")
        key = os.environ.get("ORIGINRAG_OPENALEX_API_KEY", "").strip()
        if key:
            url += "?" + urlencode({"api_key": key})
        data = metadata(url, deadline)
        if normalize_doi(data.get("doi")) == doi:
            open_access = open_access or (data.get("open_access") or {}).get("is_oa") is True
            locations = [data.get("best_oa_location")] + (data.get("locations") or [])
            for item in locations:
                if not isinstance(item, dict) or item.get("is_oa") is not True:
                    continue
                landing = urlsplit(str(item.get("landing_page_url") or ""))
                if landing.scheme == "https" and landing.hostname not in {"pmc.ncbi.nlm.nih.gov", "europepmc.org"}:
                    landing_pages.append(landing.geturl())
                match = re.fullmatch(r"/articles/(PMC[0-9]+)/?", landing.path)
                if landing.hostname == "pmc.ncbi.nlm.nih.gov" and match and len(checked_pmcids) < 2 and match[1] not in checked_pmcids:
                    checked_pmcids.add(match[1])
                    try:
                        yield from pmc_cloud_candidates(match[1], doi, deadline)
                    except Exception:
                        pass
            for item in locations:
                if isinstance(item, dict) and item.get("is_oa") is True and item.get("pdf_url"):
                    yield str(item["pdf_url"])
    except Exception:
        pass
    try:
        params = {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": "3"}
        data = metadata("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode(params), deadline)
        for item in (data.get("resultList") or {}).get("result", []):
            if normalize_doi(item.get("doi")) != doi:
                continue
            pmcid = str(item.get("pmcid") or "")
            if re.fullmatch(r"PMC\d+", pmcid) and pmcid not in checked_pmcids and len(checked_pmcids) < 2:
                checked_pmcids.add(pmcid)
                try:
                    yield from pmc_cloud_candidates(pmcid, doi, deadline)
                except Exception:
                    pass
            if item.get("isOpenAccess") != "Y":
                continue
            open_access = True
            for link in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []):
                if str(link.get("documentStyle", "")).lower() == "pdf" and link.get("availabilityCode") in {"OA", "F"}:
                    yield str(link.get("url") or "")
            if re.fullmatch(r"PMC\d+", pmcid):
                yield f"https://europepmc.org/articles/{pmcid}?pdf=render"
    except Exception:
        pass
    try:
        data = metadata("https://api.crossref.org/works/" + quote(doi, safe=""), deadline).get("message") or {}
        if normalize_doi(data.get("DOI")) == doi and (open_access or cc_licence(data)):
            if data.get("URL"):
                landing_pages.append(str(data["URL"]))
            links = [item for item in (data.get("link") or []) if isinstance(item, dict) and item.get("content-type") == "application/pdf"]
            links.sort(key=lambda item: item.get("content-version") != "vor")
            for item in links[:4]:
                if item.get("URL"):
                    yield str(item["URL"])
    except Exception:
        pass
    for url in list(dict.fromkeys(landing_pages))[:3]:
        try:
            yield from landing_pdf_candidates(url, doi, deadline)
        except Exception:
            continue


def download_open_pdf(doi: str, progress) -> bytes:
    import fitz

    deadline = time.monotonic() + 70
    seen = set()
    blocked_url = ""
    identity_failed = False
    unavailable = False
    for url in oa_candidates(doi, deadline):
        if url in seen:
            continue
        if len(seen) >= 4 or time.monotonic() >= deadline:
            break
        seen.add(url)
        progress("downloading")
        try:
            data = fetch_public(url, max_bytes=MAX_PDF_BYTES, deadline=deadline)
            if not data.startswith(b"%PDF-"):
                unavailable = True
                continue
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or not pdf.page_count:
                    continue
                first_page = pdf[0].get_text()[:12000].lower()
                pattern = re.escape(doi) + r"(?=$|[\s\).,;])"
                if not re.search(pattern, first_page):
                    identity_failed = True
                    continue
            return data
        except PublicDownloadError as exc:
            if exc.status in {401, 403}:
                blocked_url = blocked_url or exc.url
            else:
                unavailable = True
        except Exception:
            unavailable = True
            continue
    if blocked_url:
        raise ManualUploadRequired("Import failed: the source blocked automatic downloading (HTTP 401/403), and no accessible alternative PDF was verified. Not added to the RAG library.", reason="browser_required", download_url=blocked_url)
    if identity_failed:
        raise ManualUploadRequired("Import failed: the PDF's DOI did not match this paper. Not added to the RAG library.", reason="identity_unverified")
    if unavailable:
        raise ManualUploadRequired("Import failed: the PDF source is currently unavailable. Not added to the RAG library. Please try again later.", reason="source_unavailable")
    raise ManualUploadRequired("Import failed: no accessible PDF with matching DOI could be verified. Not added to the RAG library.")


class PaperImports:
    def __init__(self, library, downloader=download_open_pdf):
        self.library = library
        self.downloader = downloader
        self.lock = threading.RLock()
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="originrag-paper-import")
        self.closed = False

    def enqueue(self, owner: str, value: str) -> dict:
        doi = normalize_doi(value)
        if not doi or len(doi) > 250 or not re.fullmatch(r"10\.\d{4,9}/[^\s<>\"?#]+", doi):
            raise ValueError("A valid DOI is required")
        with self.lock:
            if self.closed:
                raise ImportBusy("Paper imports are stopping")
            for job in self.jobs.values():
                if job["doi"] == doi and job["state"] in ACTIVE:
                    if job["owner"] == owner:
                        return self.public(job)
                    raise ImportBusy("This paper is already being imported into the shared library")
            if sum(job["state"] in ACTIVE for job in self.jobs.values()) >= 8:
                raise ImportBusy("The paper import queue is full. Please try again later")
            while len(self.jobs) >= 128:
                expired = next(key for key, job in self.jobs.items() if job["state"] not in ACTIVE)
                del self.jobs[expired]
            job_id = secrets.token_hex(12)
            job = {"job_id": job_id, "owner": owner, "doi": doi, "state": "queued", "message": "Waiting to import"}
            self.jobs[job_id] = job
            self.executor.submit(self.run, job_id)
            return self.public(job)

    @staticmethod
    def public(job):
        return {key: value for key, value in job.items() if key != "owner"}

    def snapshot(self, owner):
        with self.lock:
            return [self.public(job) for job in self.jobs.values() if job["owner"] == owner]

    def update(self, job_id, state, message=""):
        with self.lock:
            self.jobs[job_id].update(state=state, message=message)

    def run(self, job_id):
        with self.lock:
            job = dict(self.jobs[job_id])
        try:
            self.update(job_id, "finding", "Looking for an open-access copy")
            data = self.downloader(job["doi"], lambda state: self.update(job_id, state, "Downloading an open-access PDF"))
            filename = f"discovered-{job_id}.pdf"
            self.update(job_id, "indexing", "Waiting for library processing")
            with self.library.corpus_lock:
                self.library.store_upload(io.BytesIO(data), content_length=len(data), filename=filename, doi=job["doi"], uploaded_by=job["owner"], schedule_import=False)
                summary = self.library.run_pending_now()
            result = next((row for row in summary.get("results", []) if row.get("source_file") == filename), None)
            if result and result.get("status") == "accepted":
                self.update(job_id, "imported", "Added to library and indexed")
            elif result and result.get("status") == "duplicate":
                self.update(job_id, "duplicate", "Already present or duplicate detected; check Library")
            else:
                self.update(job_id, "review_required", "PDF downloaded, but library processing needs attention. Not confirmed ready for RAG. Check the library update report.")
        except ManualUploadRequired as exc:
            with self.lock:
                self.jobs[job_id].update(reason=exc.reason, download_url=exc.download_url)
            self.update(job_id, "failed", str(exc))
        except Exception:
            self.update(job_id, "error", "Import could not complete. Not confirmed ready for RAG. Check the library status before retrying.")

    def shutdown(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
