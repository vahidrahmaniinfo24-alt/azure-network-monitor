"""
Configuration for the Hybrid Network Monitoring Agent.

Two-path architecture:
  LOCAL_FILE  – where the agent writes raw metrics every cycle (fast, local).
  OUTPUT_FILE – the file the dashboard reads (synced from LOCAL_FILE after
                every successful local write).

Set the SYNC_CMD env-var to a shell command that copies / pushes OUTPUT_FILE
(e.g. ``git add … && git commit -m … && git push``).  When unset the agent
simply copies LOCAL_FILE → OUTPUT_FILE on the same filesystem.
"""
import os

# -- gateway ---------------------------------------------------------------
# Auto-detect from OS routing tables.  Set to a literal IP only as fallback.
GATEWAY_IP = None

# -- timing ----------------------------------------------------------------
INTERVAL_SECONDS = 5
HISTORY_MAX_POINTS = 60        # 60 × 5 s = 5 min of chart data
STALENESS_THRESHOLD_SECONDS = 30

# -- paths -----------------------------------------------------------------
# config.py lives at <repo>/monitoring/agent/config.py  →  3 levels up = repo root.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Step 1 target: agent writes here first (always local, always fast).
LOCAL_FILE = os.path.join(_REPO_ROOT, "monitoring", "agent", "local_metrics.json")

# Step 2 target: dashboard reads here.  Overridable via env-var for cloud.
DEFAULT_OUTPUT = os.path.join(_REPO_ROOT, "monitoring", "dashboard", "data", "metrics.json")
OUTPUT_FILE = os.environ.get("METRICS_FILE", DEFAULT_OUTPUT)

# Optional shell command executed after the local write succeeds.
# When None the agent falls back to a plain shutil.copy2().
SYNC_CMD = os.environ.get("SYNC_CMD", None)

# -- identity --------------------------------------------------------------
HOSTNAME = None  # type: ignore  # falls back to socket.gethostname()
