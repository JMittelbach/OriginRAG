from __future__ import annotations

import http.client
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlencode, urljoin, urlsplit

from originrag.provenance.library import normalize_doi


MAX_PDF_BYTES = 40 * 1024 * 1024
ACTIVE = {"queued", "finding", "downloading", "indexing"}


class ImportBusy(ValueError):
    pass


class ManualUploadRequired(ValueError):
    pass


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
                raise ValueError("Public download unavailable")
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


def oa_candidates(doi: str, deadline: float):
    email = os.environ.get("ORIGINRAG_UNPAYWALL_EMAIL", "").strip()
    if email:
        try:
            data = metadata("https://api.unpaywall.org/v2/" + quote(doi, safe="") + "?" + urlencode({"email": email}), deadline)
            if normalize_doi(data.get("doi")) == doi and data.get("is_oa") is True:
                locations = [data.get("best_oa_location")] + (data.get("oa_locations") or [])
                for item in locations:
                    if isinstance(item, dict) and item.get("url_for_pdf"):
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
            locations = [data.get("best_oa_location")] + (data.get("locations") or [])
            for item in locations:
                if isinstance(item, dict) and item.get("is_oa") is True and item.get("pdf_url"):
                    yield str(item["pdf_url"])
    except Exception:
        pass
    try:
        params = {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": "3"}
        data = metadata("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode(params), deadline)
        for item in (data.get("resultList") or {}).get("result", []):
            if normalize_doi(item.get("doi")) != doi or item.get("isOpenAccess") != "Y":
                continue
            for link in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []):
                if str(link.get("documentStyle", "")).lower() == "pdf" and link.get("availabilityCode") in {"OA", "F"}:
                    yield str(link.get("url") or "")
            pmcid = str(item.get("pmcid") or "")
            if re.fullmatch(r"PMC\d+", pmcid):
                yield f"https://europepmc.org/articles/{pmcid}?pdf=render"
    except Exception:
        pass


def download_open_pdf(doi: str, progress) -> bytes:
    import fitz

    deadline = time.monotonic() + 70
    seen = set()
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
                continue
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or not pdf.page_count:
                    continue
                first_page = pdf[0].get_text()[:12000].lower()
                pattern = re.escape(doi) + r"(?=$|[\s\).,;])"
                if not re.search(pattern, first_page):
                    continue
            return data
        except Exception:
            continue
    raise ManualUploadRequired("No accessible PDF with matching DOI could be verified. Open the publication and upload a legally accessible PDF instead.")


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
                self.update(job_id, "review_required", "Library processing needs attention. Check the library update report or upload the PDF manually")
        except ManualUploadRequired as exc:
            self.update(job_id, "manual_upload", str(exc))
        except Exception:
            self.update(job_id, "error", "Import could not complete. Check the library status and try again, or upload the PDF manually")

    def shutdown(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
