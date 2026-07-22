"""
Hybrid Network Monitoring Dashboard (Streamlit).

Reads the latest metrics JSON produced by the agent and renders a
professional, real-time monitoring UI with live charts and status cards.

The dashboard is resilient to missing files, stale data, and JSON
parse errors — it surfaces connection status and an error log in the
sidebar so users always know whether the agent is alive.

Dependencies:
    pip install streamlit pandas

Run with:
    streamlit run monitoring/dashboard/dashboard.py
"""

import json
import os
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Path configuration — override with METRICS_FILE env var for cloud deploy
# ---------------------------------------------------------------------------

METRICS_FILE = os.environ.get(
    "METRICS_FILE",
    os.path.join(os.path.dirname(__file__), "data", "metrics.json"),
)

STALENESS_THRESHOLD = int(os.environ.get("STALENESS_THRESHOLD", "30"))

MAX_ERROR_LOG = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _init_state() -> None:
    if "error_log" not in st.session_state:
        st.session_state.error_log = []
    if "last_ok_load" not in st.session_state:
        st.session_state.last_ok_load = None


def _log_error(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    log = st.session_state.error_log
    log.append(entry)
    if len(log) > MAX_ERROR_LOG:
        log.pop(0)


# ---------------------------------------------------------------------------
# Robust JSON loader
# ---------------------------------------------------------------------------

def load_metrics() -> dict | None:
    """Load and validate the metrics JSON with full error handling.

    Handles: missing file, permission denied, truncated / partial write,
    invalid JSON, missing keys, and stale timestamps.

    Returns the parsed dict on success, None on any failure.
    Updates ``st.session_state.conn_status`` and ``st.session_state.staleness``.
    """
    _init_state()
    ss = st.session_state

    # -- file existence (handles symbolic-link races too) --
    if not os.path.exists(METRICS_FILE):
        ss.conn_status = "no_file"
        ss.staleness = None
        _log_error(f"File not found: {METRICS_FILE}")
        return None

    # -- read + parse --
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
    except PermissionError:
        ss.conn_status = "permission_error"
        ss.staleness = None
        _log_error("Permission denied reading metrics file")
        return None
    except OSError as exc:
        ss.conn_status = "read_error"
        ss.staleness = None
        _log_error(f"OS error reading file: {exc}")
        return None

    if not raw.strip():
        ss.conn_status = "empty_file"
        ss.staleness = None
        _log_error("Metrics file is empty (agent may be mid-write)")
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        ss.conn_status = "parse_error"
        ss.staleness = None
        _log_error(f"JSON parse error: {exc}")
        return None

    # -- required keys --
    if "timestamp" not in data or "cpu_usage_percent" not in data:
        ss.conn_status = "invalid_schema"
        ss.staleness = None
        _log_error("Missing required fields (timestamp / cpu_usage_percent)")
        return None

    # -- staleness check --
    try:
        ts = datetime.fromisoformat(data["timestamp"])
        now = datetime.now(timezone.utc)
        age = (now - ts).total_seconds()
        ss.data_timestamp = ts
        ss.staleness = age

        if age <= STALENESS_THRESHOLD:
            ss.conn_status = "connected"
        elif age <= STALENESS_THRESHOLD * 4:
            ss.conn_status = "stale"
        else:
            ss.conn_status = "disconnected"
    except (ValueError, TypeError):
        ss.conn_status = "bad_timestamp"
        ss.staleness = None
        _log_error(f"Unparseable timestamp: {data.get('timestamp')}")

    ss.last_ok_load = datetime.now(timezone.utc)
    return data


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar(data: dict | None) -> None:
    with st.sidebar:
        st.markdown("### :material/monitoring: Monitor controls")
        st.toggle("Auto-refresh (3 s)", value=True, key="auto_refresh")

        # -- connection status --
        st.divider()
        st.markdown("### :material/link: Agent status")

        status = st.session_state.get("conn_status", "unknown")
        labels = {
            "connected":      (":material/check_circle:", "Connected",    "green"),
            "stale":          (":material/warning:",       "Stale",        "orange"),
            "disconnected":   (":material/error:",         "Disconnected", "red"),
            "no_file":        (":material/folder_off:",    "No file",      "red"),
            "empty_file":     (":material/broken_image:",  "Empty file",   "orange"),
            "parse_error":    (":material/data_alert:",    "Parse error",  "red"),
            "permission_error": (":material/lock:",        "Locked",       "red"),
            "read_error":     (":material/cloud_off:",     "Read error",   "red"),
            "invalid_schema": (":material/schema:",        "Bad schema",   "red"),
            "bad_timestamp":  (":material/schedule:",      "Bad timestamp","orange"),
            "unknown":        (":material/help:",          "Unknown",      "orange"),
        }
        icon, text, color = labels.get(status, labels["unknown"])
        st.badge(text, icon=icon, color=color)

        age = st.session_state.get("staleness")
        if age is not None:
            if age < 5:
                st.caption(f"Data age: **{age:.0f} s** (fresh)")
            elif age < STALENESS_THRESHOLD:
                st.caption(f"Data age: **{age:.0f} s**")
            else:
                st.caption(f"Data age: **{age:.0f} s** (stale)")
        elif status != "connected":
            st.caption("No fresh data available")

        # -- error log --
        log = st.session_state.get("error_log", [])
        if log:
            st.divider()
            with st.popover(
                f":material/error_outline: Errors ({len(log)})",
                type="tertiary",
            ):
                for entry in reversed(log):
                    st.caption(entry)

        # -- system info (only when data present) --
        if data:
            st.divider()
            st.markdown("### :material/info: System info")
            st.caption(f"**Host:** {data.get('hostname', 'unknown')}")
            st.caption(f"**Platform:** {data.get('platform', 'unknown')}")
            st.caption(f"**Agent:** v{data.get('agent_version', '?')}")
            st.caption(f"**Gateway:** {data.get('gateway', {}).get('ip', 'N/A')}")

            ts = data.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    st.caption(f"**Last update:** {dt.strftime('%H:%M:%S UTC')}")
                except ValueError:
                    st.caption(f"**Last update:** {ts}")

            mem = data.get("memory", {})
            disk = data.get("disk", {})
            net = data.get("network", {})
            st.divider()
            st.markdown("### :material/storage: Resources")
            st.caption(
                f"**RAM:** {mem.get('used_gb', '?')}/{mem.get('total_gb', '?')} GB"
            )
            st.caption(
                f"**Disk:** {disk.get('used_gb', '?')}/{disk.get('total_gb', '?')} GB"
            )
            st.divider()
            st.markdown("### :material/network_check: Totals")
            st.caption(f"**Sent:** {_format_bytes(net.get('total_bytes_sent', 0))}")
            st.caption(f"**Recv:** {_format_bytes(net.get('total_bytes_recv', 0))}")


# ---------------------------------------------------------------------------
# Auto-refreshing live panel
# ---------------------------------------------------------------------------

@st.fragment(run_every=timedelta(seconds=3))
def render_live_panel() -> None:
    data = load_metrics()

    if data is None:
        status = st.session_state.get("conn_status", "unknown")
        if status == "no_file":
            st.warning(
                "**Agent not writing data.**  \n"
                "Start the agent with  `python monitoring/agent/agent.py`  \n"
                "or set the `METRICS_FILE` environment variable to an existing path."
            )
        elif status == "empty_file":
            st.warning("Metrics file exists but is empty. The agent may be starting up.")
        elif status in ("parse_error", "invalid_schema"):
            st.error("Metrics file is corrupted. The agent may be mid-write or crashed.")
        else:
            st.warning("Unable to load metrics. Check the sidebar error log for details.")
        return

    gateway = data.get("gateway", {})
    cpu = data.get("cpu_usage_percent", 0.0)
    mem = data.get("memory", {})
    disk = data.get("disk", {})
    net = data.get("network", {})
    reachable = gateway.get("reachable", False)
    latency = gateway.get("latency_ms")

    # --- stale-data banner ---
    age = st.session_state.get("staleness")
    if age is not None and age > STALENESS_THRESHOLD:
        st.error(
            f":material/warning: **Data is {age:.0f}s old** "
            f"(threshold {STALENESS_THRESHOLD}s). The agent may have stopped."
        )

    # --- KPI row ---
    st.markdown("#### :material/dashboard: Live metrics")

    with st.container(horizontal=True):
        with st.container(border=True):
            st.metric(":material/memory: CPU", f"{cpu}%")
        with st.container(border=True):
            st.metric(
                ":material/ram: Memory",
                f"{mem.get('percent', 0)}%",
                f"{mem.get('used_gb', 0)} / {mem.get('total_gb', 0)} GB",
            )
        with st.container(border=True):
            st.metric(
                ":material/hard_drive: Disk",
                f"{disk.get('percent', 0)}%",
                f"{disk.get('used_gb', 0)} / {disk.get('total_gb', 0)} GB",
            )
        with st.container(border=True):
            gw_value = f"{latency} ms" if latency is not None else "\u2014"
            gw_delta = "reachable" if reachable else "unreachable"
            st.metric(
                ":material/router_network: Gateway",
                gw_value,
                gw_delta,
                delta_color="normal" if reachable else "inverse",
            )

    # --- Status badges ---
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if cpu < 50:
            st.badge("CPU nominal", icon=":material/check_circle:", color="green")
        elif cpu < 80:
            st.badge("CPU elevated", icon=":material/warning:", color="orange")
        else:
            st.badge("CPU high", icon=":material/error:", color="red")
    with b2:
        mp = mem.get("percent", 0)
        if mp < 60:
            st.badge("RAM nominal", icon=":material/check_circle:", color="green")
        elif mp < 85:
            st.badge("RAM elevated", icon=":material/warning:", color="orange")
        else:
            st.badge("RAM high", icon=":material/error:", color="red")
    with b3:
        dp = disk.get("percent", 0)
        if dp < 75:
            st.badge("Disk nominal", icon=":material/check_circle:", color="green")
        elif dp < 90:
            st.badge("Disk elevated", icon=":material/warning:", color="orange")
        else:
            st.badge("Disk critical", icon=":material/error:", color="red")
    with b4:
        if reachable:
            st.badge("Gateway UP", icon=":material/check_circle:", color="green")
        else:
            st.badge("Gateway DOWN", icon=":material/error:", color="red")

    # --- History charts ---
    history = data.get("history", [])
    df = _history_to_df(history)

    st.markdown("#### :material/show_chart: CPU & memory history")
    if len(df) >= 2:
        st.line_chart(
            df[["cpu", "memory"]].rename(columns={"cpu": "CPU %", "memory": "Memory %"}),
            height=280,
        )
    else:
        st.caption("Collecting data\u2026 chart appears after a few readings.")

    st.markdown("#### :material/network_check: Network traffic")
    if len(df) >= 2:
        st.area_chart(
            df[["bytes_sent", "bytes_recv"]].rename(
                columns={"bytes_sent": "Sent (B/s)", "bytes_recv": "Recv (B/s)"}
            ),
            height=240,
        )
        st.caption(
            f"Throughput  \u2014  "
            f"sent: **{_format_bytes(int(net.get('bytes_sent_delta', 0)))}**/s"
            f"  |  received: **{_format_bytes(int(net.get('bytes_recv_delta', 0)))}**/s"
            f"  |  errors: **{net.get('errors', 0)}**"
            f"  |  drops: **{net.get('drops', 0)}**"
        )
    else:
        st.caption("Collecting data\u2026 chart appears after a few readings.")

    st.markdown("#### :material/speed: Gateway latency history")
    if not df.empty and "latency_ms" in df.columns:
        lat = df["latency_ms"].dropna()
        if len(lat) >= 2:
            st.line_chart(lat.to_frame("Latency (ms)"), height=220)
        else:
            st.caption("Waiting for latency data\u2026")
    else:
        st.caption("No latency data yet\u2026")

    # --- Raw metrics ---
    with st.expander(":material/table_chart: Raw metrics"):
        flat = {
            "hostname": data.get("hostname"),
            "platform": data.get("platform"),
            "agent_version": data.get("agent_version"),
            "timestamp": data.get("timestamp"),
            "cpu_%": cpu,
            "ram_%": mem.get("percent"),
            "ram_used_gb": mem.get("used_gb"),
            "disk_%": disk.get("percent"),
            "disk_used_gb": disk.get("used_gb"),
            "gw_ip": gateway.get("ip"),
            "gw_reachable": reachable,
            "gw_latency_ms": latency,
            "net_sent": net.get("bytes_sent_delta"),
            "net_recv": net.get("bytes_recv_delta"),
            "net_errors": net.get("errors"),
            "net_drops": net.get("drops"),
        }
        st.dataframe(pd.json_normalize(flat), hide_index=True, width="stretch")

    st.caption(f"Last updated: {data.get('timestamp', 'unknown')}")


def _history_to_df(history: list[dict]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    return df


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Hybrid Network Monitor",
        page_icon=":material/monitoring:",
        layout="wide",
    )

    st.title(":material/monitoring: Hybrid network monitor")
    st.caption("Real-time system metrics collected by the Python agent")

    # Load once for the sidebar (outside fragment so it doesn't flicker)
    data = load_metrics()
    _render_sidebar(data)
    render_live_panel()


if __name__ == "__main__":
    main()
