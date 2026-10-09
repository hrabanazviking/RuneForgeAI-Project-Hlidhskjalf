"""Slice 42 — Host state harvester (Phase G: Host Integration).

``harvest()`` collects the host machine's current state and wraps it in the
versioned envelope used across Hliðskjálf:

    {"v": 1, "type": "host.state", "ts": <unix epoch seconds>,
     "payload": {"host": {...}, "memory": {...}, "disks": [...],
                 "processes": [...]}}

Sources are stdlib-only:
- /proc (Linux) for memory and per-process CPU times, with a ``ps`` fallback
  for non-Linux hosts.
- :func:`shutil.disk_usage` for disks.
- :mod:`platform` / :mod:`socket` for host identity.

CPU ranking: per-process CPU times are sampled twice ``sample_interval``
seconds apart and ranked by delta (highest first); ``top_n`` entries are kept.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

ENVELOPE_VERSION = 1
ENVELOPE_TYPE = "host.state"


def _read_proc_stat_times() -> dict[int, float]:
    """Return {pid: total_cpu_seconds} from /proc/<pid>/stat."""
    times: dict[int, float] = {}
    try:
        hertz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (KeyError, AttributeError, ValueError):
        hertz = 100
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
                fields = handle.read().rsplit(")", 1)[-1].split()
            # utime is field 14, stime field 15 (after the comm field).
            utime = float(fields[11])
            stime = float(fields[12])
            times[pid] = (utime + stime) / hertz
        except (OSError, IndexError, ValueError):
            continue
    return times


def _proc_names(pids: list[int]) -> dict[int, str]:
    names: dict[int, str] = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as handle:
                names[pid] = handle.read().strip()
        except OSError:
            names[pid] = f"pid-{pid}"
    return names


def _top_processes_proc(top_n: int, sample_interval: float) -> list[dict[str, Any]]:
    before = _read_proc_stat_times()
    time.sleep(sample_interval)
    after = _read_proc_stat_times()
    deltas = [
        (pid, after[pid] - before.get(pid, after[pid]))
        for pid in after
    ]
    ranked = sorted(deltas, key=lambda item: item[1], reverse=True)[:top_n]
    pids = [pid for pid, _ in ranked]
    names = _proc_names(pids)
    interval = max(sample_interval, 1e-9)
    return [
        {
            "pid": pid,
            "name": names.get(pid, f"pid-{pid}"),
            # Fraction of one CPU core consumed during the sample window.
            "cpu": round(delta / interval, 4),
        }
        for pid, delta in ranked
    ]


def _top_processes_ps(top_n: int) -> list[dict[str, Any]]:
    """Fallback process ranking via the ``ps`` command."""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid,pcpu,comm", "--sort=-pcpu"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    processes: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
        except ValueError:
            continue
        processes.append({"pid": pid, "name": parts[2], "cpu": cpu / 100.0})
        if len(processes) >= top_n:
            break
    return processes


def top_processes(top_n: int = 10, sample_interval: float = 0.1) -> list[dict[str, Any]]:
    """Return the ``top_n`` processes ranked by CPU usage (highest first)."""
    if os.path.isdir("/proc"):
        try:
            return _top_processes_proc(top_n, sample_interval)
        except OSError:
            pass
    return _top_processes_ps(top_n)


def memory_info() -> dict[str, Any]:
    """Return memory stats in bytes: total/available/used/percent."""
    total = available = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            fields: dict[str, int] = {}
            for line in handle:
                key, _, rest = line.partition(":")
                value = rest.strip().split()
                if value:
                    try:
                        fields[key] = int(value[0]) * 1024  # kB -> bytes
                    except ValueError:
                        pass
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable", fields.get("MemFree"))
    except OSError:
        pass
    if total is None:
        # Best-effort fallback: page count from sysconf.
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total = pages * page_size
        except (ValueError, OSError, AttributeError):
            total = 0
    available = available if available is not None else 0
    used = max(total - available, 0)
    percent = round(used / total * 100.0, 2) if total else 0.0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "percent": percent,
    }


def disk_info() -> list[dict[str, Any]]:
    """Return usage for the root filesystem mount."""
    entries: list[dict[str, Any]] = []
    for path in ("/",):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        percent = (
            round(usage.used / usage.total * 100.0, 2) if usage.total else 0.0
        )
        entries.append(
            {
                "path": path,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent": percent,
            }
        )
    return entries


def host_info() -> dict[str, Any]:
    """Return host identity fields."""
    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def harvest(top_n: int = 10, sample_interval: float = 0.1) -> dict[str, Any]:
    """Collect host state and return the ``host.state`` envelope.

    Envelope format:
        {"v": 1, "type": "host.state", "ts": <float epoch>,
         "payload": {"host": {...}, "memory": {...}, "disks": [...],
                     "processes": [...]}}
    """
    now = time.time()
    return {
        "v": ENVELOPE_VERSION,
        "type": ENVELOPE_TYPE,
        "ts": now,
        "payload": {
            "host": host_info(),
            "timestamp": now,
            "memory": memory_info(),
            "disks": disk_info(),
            "processes": top_processes(top_n=top_n, sample_interval=sample_interval),
        },
    }
