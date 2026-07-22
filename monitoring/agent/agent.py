"""
Hybrid Network Monitoring Agent – two-step local-first architecture.

Step 1 – LOCAL COLLECT
    Every cycle the agent gathers CPU, RAM, disk, network I/O and a
    gateway ping, then writes a complete JSON payload to a *local*
    scratch file (LOCAL_FILE).  This write is fast, atomic, and
    guaranteed to succeed even when the gateway is unreachable — the
    gateway status is simply recorded as ``reachable: false``.

Step 2 – SYNC / PUSH
    Immediately after the local write the agent copies (or runs a
    user-defined shell command) to move the data to OUTPUT_FILE — the
    file the Streamlit dashboard reads.  If the sync fails the local
    file is still intact and the agent keeps running.

Gateway failures NEVER crash the agent.  Detection and ping are each
wrapped in their own ``try/except`` blocks; on any error the gateway
is marked unreachable and collection continues.

Dependencies:
    pip install psutil
"""

import collections
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import psutil

import config

AGENT_VERSION = "3.0.0"


# ===================================================================
# Gateway helpers – all fault-tolerant, never raise
# ===================================================================

def _detect_gateway_windows() -> str | None:
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


def _detect_gateway() -> str | None:
    """Return the OS default gateway or ``None``.  Never raises."""
    if sys.platform == "win32":
        return _detect_gateway_windows()
    elif sys.platform == "darwin":
        return _detect_gateway_macos()
    return _detect_gateway_linux()


def _ping_gateway(gateway: str) -> dict:
    """Ping *gateway* once and return reachability + latency.

    Returns ``{"reachable": False, "latency_ms": None}`` on any error.
    """
    try:
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", "2000", gateway]
        else:
            cmd = ["ping", "-c", "1", "-W", "2", gateway]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        reachable = result.returncode == 0
        latency_ms = None
        if reachable:
            m = re.search(r"time[=<](\d+(?:\.\d+)?)\s*ms", result.stdout)
            if m:
                latency_ms = float(m.group(1))
        return {"reachable": reachable, "latency_ms": latency_ms}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {"reachable": False, "latency_ms": None}


# ===================================================================
# System-metric collectors (always succeed on any OS)
# ===================================================================

def _get_cpu() -> float:
    return round(psutil.cpu_percent(interval=None), 2)


def _get_memory() -> dict:
    m = psutil.virtual_memory()
    return {
        "percent": round(m.percent, 1),
        "used_gb": round(m.used / (1024 ** 3), 2),
        "total_gb": round(m.total / (1024 ** 3), 2),
        "available_gb": round(m.available / (1024 ** 3), 2),
    }


def _get_disk() -> dict:
    path = "C:\\" if sys.platform == "win32" else "/"
    u = psutil.disk_usage(path)
    return {
        "percent": round(u.percent, 1),
        "used_gb": round(u.used / (1024 ** 3), 2),
        "total_gb": round(u.total / (1024 ** 3), 2),
        "free_gb": round(u.free / (1024 ** 3), 2),
    }


def _get_network(prev: dict | None) -> tuple[dict, dict]:
    c = psutil.net_io_counters()
    cur = {
        "bytes_sent": c.bytes_sent, "bytes_recv": c.bytes_recv,
        "packets_sent": c.packets_sent, "packets_recv": c.packets_recv,
        "errin": c.errin, "errout": c.errout,
        "dropin": c.dropin, "dropout": c.dropout,
    }
    if prev is None:
        delta = {k: 0 for k in cur}
    else:
        delta = {k: cur[k] - prev.get(k, 0) for k in cur}
    return delta, cur


# ===================================================================
# Step 1 – LOCAL collect + write
# ===================================================================

def _collect_local() -> dict:
    """Gather every metric and return the complete payload dict.

    Gateway detection and ping are each isolated – a failure in
    either one produces a safe fallback value, never an exception.
    """
    # --- identity ---
    hostname = config.HOSTNAME or socket.gethostname()

    # --- gateway (double-fault-tolerant) ---
    try:
        gw_ip = _detect_gateway() or config.GATEWAY_IP or "0.0.0.0"
    except Exception:
        gw_ip = config.GATEWAY_IP or "0.0.0.0"
    try:
        ping = _ping_gateway(gw_ip)
    except Exception:
        ping = {"reachable": False, "latency_ms": None}

    # --- system metrics (always succeed) ---
    cpu = _get_cpu()
    mem = _get_memory()
    disk = _get_disk()
    net_delta, net_snap = _get_network(_prev_net)

    return {
        "hostname": hostname,
        "platform": platform.system(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_version": AGENT_VERSION,
        "cpu_usage_percent": cpu,
        "memory": mem,
        "disk": disk,
        "network": {
            "bytes_sent_delta": net_delta["bytes_sent"],
            "bytes_recv_delta": net_delta["bytes_recv"],
            "packets_sent_delta": net_delta["packets_sent"],
            "packets_recv_delta": net_delta["packets_recv"],
            "errors": net_delta["errin"] + net_delta["errout"],
            "drops": net_delta["dropin"] + net_delta["dropout"],
            "total_bytes_sent": net_snap["bytes_sent"],
            "total_bytes_recv": net_snap["bytes_recv"],
        },
        "gateway": {
            "ip": gw_ip,
            "reachable": ping["reachable"],
            "latency_ms": ping["latency_ms"],
        },
    }


def _atomic_write(payload: dict, path: str, retries: int = 3) -> None:
    """Write *payload* as JSON to *path* atomically with retry.

    Uses tempfile + os.replace.  Retries on PermissionError /
    OSError (Windows AV locks, concurrent readers).
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    target_dir = os.path.dirname(path) or "."
    for attempt in range(1, retries + 1):
        fd, tmp = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, path)
            return
        except (PermissionError, OSError):
            _safe_unlink(tmp)
            if attempt == retries:
                raise
            time.sleep(0.2 * attempt)
        except BaseException:
            _safe_unlink(tmp)
            raise


def _safe_unlink(p: str) -> None:
    try:
        os.unlink(p)
    except OSError:
        pass


def _save_local(payload: dict) -> bool:
    """Step 1: write payload to LOCAL_FILE.  Returns True on success."""
    try:
        _atomic_write(payload, config.LOCAL_FILE)
        return True
    except Exception as exc:
        print(f"[agent] LOCAL write failed: {exc}")
        return False


# ===================================================================
# Step 2 – SYNC / PUSH to output
# ===================================================================

def _sync_to_output() -> bool:
    """Step 2: copy LOCAL_FILE → OUTPUT_FILE (or run SYNC_CMD).

    Returns True on success.  Failures are logged but never crash
    the agent.
    """
    try:
        if config.SYNC_CMD:
            result = subprocess.run(
                config.SYNC_CMD, shell=True, capture_output=True,
                text=True, timeout=30,
            )
            if result.returncode != 0:
                print(f"[agent] SYNC_CMD failed (rc={result.returncode}): "
                      f"{result.stderr.strip()[:200]}")
                return False
        else:
            shutil.copy2(config.LOCAL_FILE, config.OUTPUT_FILE)
        return True
    except Exception as exc:
        print(f"[agent] sync failed: {exc}")
        return False


# ===================================================================
# Main loop
# ===================================================================

_prev_net: dict | None = None
_history: collections.deque


def main() -> None:
    global _prev_net, _history

    _history = collections.deque(maxlen=config.HISTORY_MAX_POINTS)

    # Seed cpu_percent so the first real reading is meaningful.
    psutil.cpu_percent(interval=None)

    gw_hint = config.GATEWAY_IP or "auto-detect"
    print(
        f"[agent] v{AGENT_VERSION} | "
        f"interval {config.INTERVAL_SECONDS}s | "
        f"gateway {gw_hint} | "
        f"local {config.LOCAL_FILE} | "
        f"output {config.OUTPUT_FILE}"
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            # ── Step 1: collect + save locally ────────────────────
            payload = _collect_local()

            # Update network counter delta baseline (must happen
            # after collection, before next cycle).
            _prev_net = {
                "bytes_sent": payload["network"]["total_bytes_sent"],
                "bytes_recv": payload["network"]["total_bytes_recv"],
                "packets_sent": 0, "packets_recv": 0,
                "errin": 0, "errout": 0, "dropin": 0, "dropout": 0,
            }

            # Append to history ring buffer.
            _history.append({
                "timestamp": payload["timestamp"],
                "cpu": payload["cpu_usage_percent"],
                "memory": payload["memory"]["percent"],
                "disk": payload["disk"]["percent"],
                "latency_ms": payload["gateway"]["latency_ms"],
                "bytes_sent": payload["network"]["bytes_sent_delta"],
                "bytes_recv": payload["network"]["bytes_recv_delta"],
            })

            full_payload = {**payload, "history": list(_history)}
            local_ok = _save_local(full_payload)

            # ── Step 2: sync to output ───────────────────────────
            if local_ok:
                sync_ok = _sync_to_output()
            else:
                sync_ok = False

            # ── log ──────────────────────────────────────────────
            gw = "OK" if payload["gateway"]["reachable"] else "DOWN"
            sync_tag = "synced" if sync_ok else "sync-FAIL"
            print(
                f"[agent] #{cycle} | "
                f"CPU {payload['cpu_usage_percent']}% | "
                f"RAM {payload['memory']['percent']}% | "
                f"Disk {payload['disk']['percent']}% | "
                f"GW {gw} | {sync_tag}"
            )

        except Exception as exc:
            print(f"[agent] #{cycle} ERROR: {exc}")

        time.sleep(config.INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
