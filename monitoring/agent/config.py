"""
Configuration for the Hybrid Network Monitoring Agent.

Centralizing these values keeps the agent easy to deploy across many
clients without touching the collection logic.
"""
import os

# Address of the network gateway the agent should ping.
# Set to None to auto-detect the default gateway from OS routing tables.
GATEWAY_IP = None

# How often (in seconds) the agent collects and reports metrics.
INTERVAL_SECONDS = 5

# How many historical data points to keep in the ring buffer.
# At 5 s intervals, 60 points = 5 minutes of history.
HISTORY_MAX_POINTS = 60

# How many seconds of staleness the dashboard tolerates before marking
# the agent as disconnected.
STALENESS_THRESHOLD_SECONDS = 30

# Human-friendly name for this host. Falls back to the system hostname.
HOSTNAME = None  # type: ignore  # set at runtime if left None

# Where the agent writes its latest metrics.  Overridable via the
# METRICS_FILE environment variable so the path works on both local
# dev and Streamlit Community Cloud.
_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dashboard", "data", "metrics.json",
)
METRICS_FILE = os.environ.get("METRICS_FILE", _DEFAULT)
