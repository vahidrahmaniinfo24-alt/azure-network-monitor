"""
Configuration for the Hybrid Network Monitoring Agent.

Centralizing these values keeps the agent easy to deploy across many
Linux clients without touching the collection logic.
"""
import os

# Address of the network gateway the agent should ping.
GATEWAY_IP = "192.168.1.1"

# How often (in seconds) the agent collects and reports metrics.
INTERVAL_SECONDS = 5

# Human-friendly name for this host. Falls back to the system hostname.
HOSTNAME = None  # type: ignore  # set at runtime if left None

# Where the agent writes its latest metrics. The dashboard reads this file.
# Resolved relative to this config file's location (the project root is two
# levels up: monitoring/agent/config.py -> monitoring/), so the path is
# correct no matter which directory the agent is launched from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_FILE = os.path.join(_PROJECT_ROOT, "dashboard", "data", "metrics.json")
