"""
Hybrid Network Monitoring Agent.

Collects basic system metrics:
  - CPU usage percentage
  - Ping status / latency to the network gateway

The agent writes the latest reading to a JSON file that the Streamlit
dashboard consumes. This keeps the agent fully decoupled from the
dashboard transport (easy to later swap for an API/DB push).

Dependencies:
    pip install psutil
"""

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
# CPU metrics (non-blocking)
# ---------------------------------------------------------------------------

def get_cpu_usage() -> float:
    """Return current CPU utilization as a percentage (0-100).

    Uses ``interval=None`` so the call returns instantly with the
    utilization measured since the last call (the main-loop sleep
    provides the measurement window).
    """
    return round(psutil.cpu_percent(interval=None), 2)


# ---------------------------------------------------------------------------
# Collect + write
# ---------------------------------------------------------------------------

def collect_metrics() -> dict:
    """Gather all metrics into a single serializable payload."""
    hostname = config.HOSTNAME or socket.gethostname()
    ping = ping_gateway(_GATEWAY_IP)
    return {
        "hostname": hostname,
        "platform": platform.system(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_usage_percent": get_cpu_usage(),
        "gateway": {
            "ip": _GATEWAY_IP,
            "reachable": ping["reachable"],
            "latency_ms": ping["latency_ms"],
        },
    }


def write_metrics(payload: dict, path: str) -> None:
    """Write metrics atomically so the dashboard never reads half-written JSON.

    Writes to a temp file in the same directory, then os.replace() to
    swap it into place — an atomic operation on both POSIX and Windows.
    """
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

_GATEWAY_IP: str  # resolved once at startup


def main() -> None:
    global _GATEWAY_IP

    # Resolve gateway once at startup
    _GATEWAY_IP = resolve_gateway()

    # Seed cpu_percent so the first real call returns a meaningful value
    psutil.cpu_percent(interval=None)

    print(
        f"[agent] Starting monitoring -> gateway {_GATEWAY_IP}, "
        f"interval {config.INTERVAL_SECONDS}s"
    )
    while True:
        try:
            payload = collect_metrics()
            write_metrics(payload, config.METRICS_FILE)
            status = "OK" if payload["gateway"]["reachable"] else "UNREACHABLE"
            print(
                f"[agent] {payload['hostname']} | CPU "
                f"{payload['cpu_usage_percent']}% | Gateway {status}"
            )
        except Exception as exc:
            print(f"[agent] collection error: {exc}")
        time.sleep(config.INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
