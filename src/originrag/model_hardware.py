from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
import time


_HARDWARE_LOCK = threading.Lock()
_HARDWARE_CACHE = None
GIB = 1024**3


def _command(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=4).stdout


def _linux_memory():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in {'MemTotal', 'MemAvailable'}:
            values[key] = int(value.split()[0]) * 1024
    total, available = values.get('MemTotal'), values.get('MemAvailable')
    try:
        limit = int(Path('/sys/fs/cgroup/memory.max').read_text())
        used = int(Path('/sys/fs/cgroup/memory.current').read_text())
        if total and 0 < limit < total:
            total, available = limit, min(available if available is not None else limit, max(0, limit - used))
    except (OSError, ValueError):
        pass
    return total, available


def detect_hardware():
    system = platform.system()
    result = {'system': system, 'ram_total': None, 'ram_available': None,
              'unified_memory': False, 'gpus': [], 'checked_at': time.time()}
    try:
        if system == 'Darwin':
            result['ram_total'] = int(_command(['sysctl', '-n', 'hw.memsize']).strip())
            result['unified_memory'] = platform.machine() == 'arm64'
            stats = _command(['vm_stat'])
            page_size = int(re.search(r'page size of (\d+) bytes', stats).group(1))
            pages = sum(int(match.group(1)) for match in re.finditer(
                r'^Pages (?:free|inactive|speculative):\s+(\d+)', stats, re.M))
            result['ram_available'] = min(result['ram_total'], pages * page_size)
        elif system == 'Linux':
            result['ram_total'], result['ram_available'] = _linux_memory()
        elif system == 'Windows':
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            *[(name, ctypes.c_ulonglong) for name in
                              ('total', 'available', 'page_total', 'page_available',
                               'virtual_total', 'virtual_available', 'extended')]]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                result.update(ram_total=status.total, ram_available=status.available)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    try:
        cards = csv.reader(_command([
            'nvidia-smi', '--query-gpu=name,memory.total,memory.free',
            '--format=csv,noheader,nounits',
        ]).splitlines(), skipinitialspace=True)
        result['gpus'] = [{'name': name.strip(), 'total': int(total) * 1024**2,
                           'available': int(free) * 1024**2, 'shared': False}
                          for name, total, free in list(cards)[:8]]
    except (OSError, ValueError, subprocess.SubprocessError):
        if system == 'Darwin':
            try:
                data = json.loads(_command(['system_profiler', 'SPDisplaysDataType', '-json']))
                result['gpus'] = [{'name': row.get('sppci_model') or row.get('_name') or 'Apple GPU',
                                   'total': None, 'available': None,
                                   'shared': result['unified_memory']}
                                  for row in data.get('SPDisplaysDataType', [])[:8]]
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return result


def hardware_snapshot():
    global _HARDWARE_CACHE
    with _HARDWARE_LOCK:
        now = time.monotonic()
        if _HARDWARE_CACHE is None or now - _HARDWARE_CACHE[0] > 30:
            _HARDWARE_CACHE = (now, detect_hardware())
        return _HARDWARE_CACHE[1]


def estimate_fit(model, hardware, *, local_runtime=True):
    """Planning estimate for one 16K-context request, never an admission guarantee."""
    unknown = {'status': 'unknown', 'label': 'Memory fit unknown', 'device': None,
               'context': 16384, 'estimated_min': None, 'estimated_max': None}
    if not local_runtime:
        return {**unknown, 'label': 'Remote runtime · memory unknown'}
    size = model.get('bytes')
    if not isinstance(size, (int, float)) or size <= 0 or model.get('provider') == 'colibri':
        return unknown
    low, high = int(size * 1.05 + 2 * GIB), int(size * 1.20 + 6 * GIB)
    result = {**unknown, 'estimated_min': low, 'estimated_max': high}
    if model.get('loaded'):
        return {**result, 'status': 'loaded', 'label': 'Already running',
                'device': model.get('device') or 'runtime'}
    total, available = hardware.get('ram_total'), hardware.get('ram_available')
    pools = []
    if hardware.get('unified_memory') and total:
        pools.append(('Apple GPU · shared memory', int(total * .75),
                      min(available, int(total * .75)) if available is not None else None))
    else:
        for gpu in hardware.get('gpus', []):
            if gpu.get('total') and not gpu.get('shared'):
                reserve = max(GIB, int(gpu['total'] * .05))
                pools.append(('GPU', max(0, gpu['total'] - reserve),
                              max(0, gpu['available'] - reserve) if gpu.get('available') is not None else None))
    if total:
        reserve = max(2 * GIB, int(total * .12))
        pools.append(('RAM / CPU', max(0, total - reserve),
                      max(0, available - reserve) if available is not None else None))
    for device, capacity, free in pools:
        if free is not None and high <= free:
            return {**result, 'status': 'fits', 'label': 'Likely fits now', 'device': device}
    for device, capacity, free in pools:
        if high <= capacity:
            return {**result, 'status': 'tight', 'label': 'Fits capacity · check free memory', 'device': device}
    if not hardware.get('unified_memory') and total:
        gpu_capacity = max((capacity for device, capacity, _ in pools if device == 'GPU'), default=0)
        ram_capacity = next((capacity for device, capacity, _ in pools if device == 'RAM / CPU'), 0)
        if gpu_capacity and high <= gpu_capacity + ram_capacity:
            return {**result, 'status': 'hybrid', 'label': 'May fit split GPU / RAM · slower', 'device': 'GPU + RAM'}
    if pools:
        return {**result, 'status': 'too_large', 'label': 'Full model likely too large', 'device': None}
    return result


def choose_gpu(*, budget_gb=32, reserve_gb=4, owned_group=None):
    """Conservative admission check, not a reservation or a cluster scheduler."""
    try:
        def query(fields, kind="gpu"):
            result = subprocess.run(
                ["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            return list(csv.reader(result.stdout.strip().splitlines(), skipinitialspace=True))
        cards = query("index,uuid,memory.free,utilization.gpu")
        jobs = query("gpu_uuid,pid", "compute-apps")
        occupied = set()
        for uuid, pid in jobs:
            try:
                ours = owned_group is not None and os.getpgid(int(pid)) == owned_group
            except (OSError, ValueError):
                ours = False
            if not ours:
                occupied.add(uuid.strip())
        for index, uuid, free, load in cards:
            if (uuid not in occupied and float(load) <= 5
                    and float(free) * 1024**2 >= (budget_gb + reserve_gb) * 1e9):
                return index.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def foreign_gpu_jobs(owned_group):
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.strip() and os.getpgid(int(line.strip())) != owned_group:
                return True
        return False
    except (OSError, ValueError, subprocess.SubprocessError):
        return True
