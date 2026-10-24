from __future__ import annotations

import csv
import os
import subprocess


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
