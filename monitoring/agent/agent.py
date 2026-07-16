"""
Hybrid Network Monitoring Agent (Linux client side).

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
import socket
import subprocess
import time
from datetime import datetime, timezone

import psutil

import config


def get_cpu_usage() -> float:
    """Return current CPU utilization as a percentage (0-100).

    The interval blocks briefly to sample across a time window, which
    gives a more stable reading than an instantaneous value.
    """
    return round(psutil.cpu_percent(interval=1), 2)


def ping_gateway(gateway: str) -> dict:
    """Ping the gateway and report reachability + latency in ms.

    Uses the Linux `ping` binary with a single count. Returns a dict so
    the dashboard can show both status and a numeric latency.
    """
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", gateway],
            capture_output=True,
            text=True,
            timeout=5,
        )
        reachable = result.returncode == 0
        latency_ms = None
        if reachable:
            # Parse the round-trip time from ping output, e.g.
            # "round-trip min/avg/max/mdev = 0.045/0.045/0.045/0.000 ms"
            for line in result.stdout.splitlines():
                if "round-trip" in line or "rtt" in line:
                    parts = line.split("=")[-1].strip().split("/")
                    if len(parts) >= 2:
                        latency_ms = float(parts[1])
                    break
        return {
            "reachable": reachable,
            "latency_ms": latency_ms,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"reachable": False, "latency_ms": None}


def collect_metrics() -> dict:
    """Gather all metrics into a single serializable payload."""
    hostname = config.HOSTNAME or socket.gethostname()
    ping = ping_gateway(config.GATEWAY_IP)
    return {
        "hostname": hostname,
        "platform": platform.system(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_usage_percent": get_cpu_usage(),
        "gateway": {
            "ip": config.GATEWAY_IP,
            "reachable": ping["reachable"],
            "latency_ms": ping["latency_ms"],
        },
    }


def write_metrics(payload: dict, path: str) -> None:
    """Write metrics to disk, ensuring the target directory exists."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main() -> None:
    print(
        f"[agent] Starting monitoring -> gateway {config.GATEWAY_IP}, "
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
        except Exception as exc:  # keep the agent alive on transient errors
            print(f"[agent] collection error: {exc}")
        time.sleep(config.INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
