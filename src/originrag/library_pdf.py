from contextlib import contextmanager
import csv
import os
from pathlib import Path
import re
import stat


@contextmanager
def open_registered_pdf(paper_id, *, metadata_files, bib_roots):
    relative_path = ''
    for metadata in metadata_files:
        try:
            with Path(metadata).open(encoding='utf-8', newline='') as handle:
                for row in csv.DictReader(handle, delimiter='\t'):
                    if str(row.get('paper_id') or '').strip() == paper_id:
                        relative_path = str(row.get('local_pdf_relpath') or '').strip() or relative_path
        except FileNotFoundError:
            continue
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute() or relative.suffix.lower() != '.pdf':
        raise FileNotFoundError('No local PDF is available for this paper.')
    for root in bib_roots:
        root = Path(root).resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            continue
        try:
            fd = os.open(candidate, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        except FileNotFoundError:
            continue
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode) or b'%PDF-' not in handle.read(1024):
                continue
            handle.seek(0)
            yield handle
            return
    raise FileNotFoundError('No local PDF is available for this paper.')


def byte_range(header, size):
    if header is None:
        return 0, size - 1
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or size <= 0 or not any(match.groups()):
        raise ValueError('Invalid byte range')
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError('Invalid byte range')
        return max(0, size - count), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError('Invalid byte range')
    return start, end
