"""
Hybrid Network Monitoring Agent.

Collects system metrics:
  - CPU usage percentage
  - Memory (RAM) usage
  - Disk usage
  - Network traffic (bytes sent/received per interval)
  - Ping status / latency to the network gateway

The agent writes the latest reading plus a rolling history window to a
JSON file that the Streamlit dashboard consumes.

Dependencies:
    pip install psutil
"""

import collections
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import psutil

import config


# ---------------------------------------------------------------------------
# Gateway auto-detection (cross-platform)
# ---------------------------------------------------------------------------

def _detect_gateway_windows() -> str | None:
    """Parse ``route print 0.0.0.0`` to find the default gateway."""
    try:
        result = subprocess.run(
            ["route", "print", "0.0.0.0"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split()
            if (len(parts) >= 4
                    and parts[0] == "0.0.0.0"
                    and parts[1] == "0.0.0.0"):
                gw = parts[2]
                if gw != "0.0.0.0":
                    return gw
    except Exception:
        pass
    return None


def _detect_gateway_linux() -> str | None:
    """Parse ``ip route show default`` for the gateway address."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        match = re.search(r"via\s+(\d+\.\d+\.\d+\.\d+)", result.stdout)
        return match.group(1) if match else None
    except Exception:
        return None


def _detect_gateway_macos() -> str | None:
    """Parse ``netstat -nr`` for the default route."""
    try:
        result = subprocess.run(
            ["netstat", "-nr"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("default"):
                parts = stripped.split()
                if len(parts) >= 2:
                    gw = parts[1]
                    if gw != "---" and re.match(r"\d+\.\d+\.\d+\.\d+", gw):
                        return gw
    except Exception:
        pass
    return None


def detect_default_gateway() -> str | None:
    """Auto-detect the default gateway from OS routing tables."""
    if sys.platform == "win32":
        return _detect_gateway_windows()
    elif sys.platform == "darwin":
        return _detect_gateway_macos()
    else:
        return _detect_gateway_linux()


def resolve_gateway() -> str:
    """Return the gateway IP: auto-detect first, fall back to config."""
    gw = detect_default_gateway()
    if gw:
        return gw
    if config.GATEWAY_IP:
        return config.GATEWAY_IP
    raise RuntimeError(
        "Could not auto-detect the default gateway and "
        "GATEWAY_IP is not set in config.py"
    )


# ---------------------------------------------------------------------------
# Cross-platform ping
# ---------------------------------------------------------------------------

def ping_gateway(gateway: str) -> dict:
    """Ping the gateway and report reachability + latency in ms."""
    try:
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", "2000", gateway]
        else:
            cmd = ["ping", "-c", "1", "-W", "2", gateway]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        reachable = result.returncode == 0
        latency_ms = None
        if reachable:
            match = re.search(r"time[=<](\d+(?:\.\d+)?)\s*ms", result.stdout)
            if match:
                latency_ms = float(match.group(1))
        return {"reachable": reachable, "latency_ms": latency_ms}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {"reachable": False, "latency_ms": None}


# ---------------------------------------------------------------------------
# System metrics
# ---------------------------------------------------------------------------

def get_cpu_usage() -> float:
    """Return current CPU utilization as a percentage (0-100)."""
    return round(psutil.cpu_percent(interval=None), 2)


def get_memory_usage() -> dict:
    """Return RAM usage stats."""
    mem = psutil.virtual_memory()
    return {
        "percent": round(mem.percent, 1),
        "used_gb": round(mem.used / (1024 ** 3), 2),
        "total_gb": round(mem.total / (1024 ** 3), 2),
        "available_gb": round(mem.available / (1024 ** 3), 2),
    }


def get_disk_usage() -> dict:
    """Return disk usage for the system drive."""
    if sys.platform == "win32":
        path = "C:\\"
    else:
        path = "/"
    usage = psutil.disk_usage(path)
    return {
        "percent": round(usage.percent, 1),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
    }


def get_network_io(prev_counters: dict | None) -> tuple[dict, dict]:
    """Return network I/O stats and the new counter snapshot.

    Returns (io_stats, new_counters) where io_stats contains
    bytes_sent/recv deltas since the previous reading.
    """
    counters = psutil.net_io_counters()
    current = {
        "bytes_sent": counters.bytes_sent,
        "bytes_recv": counters.bytes_recv,
        "packets_sent": counters.packets_sent,
        "packets_recv": counters.packets_recv,
        "errin": counters.errin,
        "errout": counters.errout,
        "dropin": counters.dropin,
        "dropout": counters.dropout,
    }
    if prev_counters is None:
        delta = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "errin": 0,
            "errout": 0,
            "dropin": 0,
            "dropout": 0,
        }
    else:
        delta = {k: current[k] - prev_counters.get(k, 0) for k in current}
    return delta, current


def format_bytes(n: int) -> str:
    """Human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Collect + write
# ---------------------------------------------------------------------------

def collect_metrics() -> dict:
    """Gather all metrics into a single serializable payload."""
    hostname = config.HOSTNAME or socket.gethostname()
    ping = ping_gateway(_GATEWAY_IP)
    mem = get_memory_usage()
    disk = get_disk_usage()
    net_delta, net_snapshot = get_network_io(_PREV_NET_COUNTERS)

    return {
        "hostname": hostname,
        "platform": platform.system(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_usage_percent": get_cpu_usage(),
        "memory": mem,
        "disk": disk,
        "network": {
            "bytes_sent_delta": net_delta["bytes_sent"],
            "bytes_recv_delta": net_delta["bytes_recv"],
            "packets_sent_delta": net_delta["packets_sent"],
            "packets_recv_delta": net_delta["packets_recv"],
            "errors": net_delta["errin"] + net_delta["errout"],
            "drops": net_delta["dropin"] + net_delta["dropout"],
            "total_bytes_sent": net_snapshot["bytes_sent"],
            "total_bytes_recv": net_snapshot["bytes_recv"],
        },
        "gateway": {
            "ip": _GATEWAY_IP,
            "reachable": ping["reachable"],
            "latency_ms": ping["latency_ms"],
        },
    }


def write_metrics(payload: dict, path: str) -> None:
    """Write metrics atomically so the dashboard never reads half-written JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_GATEWAY_IP: str
_PREV_NET_COUNTERS: dict | None = None
_HISTORY: collections.deque


def main() -> None:
    global _GATEWAY_IP, _PREV_NET_COUNTERS, _HISTORY

    _HISTORY = collections.deque(maxlen=config.HISTORY_MAX_POINTS)

    _GATEWAY_IP = resolve_gateway()
    psutil.cpu_percent(interval=None)

    print(
        f"[agent] Starting monitoring -> gateway {_GATEWAY_IP}, "
        f"interval {config.INTERVAL_SECONDS}s, "
        f"history {config.HISTORY_MAX_POINTS} points"
    )

    while True:
        try:
            payload = collect_metrics()
            _PREV_NET_COUNTERS = {
                "bytes_sent": payload["network"]["total_bytes_sent"],
                "bytes_recv": payload["network"]["total_bytes_recv"],
                "packets_sent": 0,
                "packets_recv": 0,
                "errin": 0,
                "errout": 0,
                "dropin": 0,
                "dropout": 0,
            }

            history_point = {
                "timestamp": payload["timestamp"],
                "cpu": payload["cpu_usage_percent"],
                "memory": payload["memory"]["percent"],
                "disk": payload["disk"]["percent"],
                "latency_ms": payload["gateway"]["latency_ms"],
                "bytes_sent": payload["network"]["bytes_sent_delta"],
                "bytes_recv": payload["network"]["bytes_recv_delta"],
            }
            _HISTORY.append(history_point)

            output = {**payload, "history": list(_HISTORY)}
            write_metrics(output, config.METRICS_FILE)

            gw_status = "OK" if payload["gateway"]["reachable"] else "DOWN"
            print(
                f"[agent] {payload['hostname']} | "
                f"CPU {payload['cpu_usage_percent']}% | "
                f"RAM {payload['memory']['percent']}% | "
                f"Disk {payload['disk']['percent']}% | "
                f"GW {gw_status}"
            )
        except Exception as exc:
            print(f"[agent] collection error: {exc}")
        time.sleep(config.INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
