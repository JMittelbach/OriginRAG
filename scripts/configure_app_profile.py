"""Read or persist the application profile without replacing private settings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile


PROFILES = {'full', 'rag', 'write'}
KEY = 'ORIGINRAG_APP_PROFILE'
ASSIGNMENT = re.compile(r'^[ \t]*(?:export[ \t]+)?' + KEY + r'[ \t]*=.*$', re.M)


def read_profile(root, explicit=None):
    if explicit:
        value = explicit
    elif os.environ.get(KEY):
        value = os.environ[KEY]
    else:
        path = Path(root) / '.env'
        source = path.read_text(encoding='utf-8') if path.exists() else ''
        matches = ASSIGNMENT.findall(source)
        raw = matches[-1].split('=', 1)[1].strip() if matches else 'full'
        if raw.startswith(('"', "'")):
            quote = raw[0]
            end = raw.find(quote, 1)
            value = raw[1:end] if end >= 1 else raw
        else:
            value = raw.split('#', 1)[0].strip()
    if value not in PROFILES:
        raise ValueError('Choose an application profile: full, rag or write.')
    return value


def configure(root, profile):
    root = Path(root).resolve()
    if profile not in PROFILES:
        raise ValueError('Choose an application profile: full, rag or write.')
    path = root / '.env'
    if path.is_symlink():
        raise ValueError('The .env file is a symbolic link. Set ORIGINRAG_APP_PROFILE in its target manually.')
    if path.exists():
        source = path.read_text(encoding='utf-8')
        permissions = stat.S_IMODE(path.stat().st_mode)
    else:
        source = (root / '.env.example').read_text(encoding='utf-8')
        source = re.sub(r'^ORIGINRAG_BIB_DIR=.*$', lambda _: 'ORIGINRAG_BIB_DIR=' + json.dumps(str(root / 'bib')), source, flags=re.M)
        permissions = 0o600
    assignment = KEY + '=' + profile
    if ASSIGNMENT.search(source):
        updated = ASSIGNMENT.sub(assignment, source)
    else:
        updated = source + ('\n' if source and not source.endswith('\n') else '') + assignment + '\n'
    if path.exists() and source == updated:
        return
    descriptor, name = tempfile.mkstemp(prefix='.env-profile-', dir=root)
    try:
        os.fchmod(descriptor, permissions)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--profile', choices=sorted(PROFILES))
    parser.add_argument('--read', action='store_true', help='Print the effective profile without changing files.')
    args = parser.parse_args()
    try:
        profile = read_profile(args.root, args.profile)
        if args.read:
            print(profile)
        else:
            configure(args.root, profile)
            print('Application profile: ' + profile + ' (saved in .env)')
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
