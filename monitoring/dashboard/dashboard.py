"""
Hybrid Network Monitoring Dashboard (Streamlit).

Reads the latest metrics JSON produced by the agent and renders a
professional, real-time monitoring UI with live charts and status cards.

Dependencies:
    pip install streamlit pandas

Run with:
    streamlit run monitoring/dashboard/dashboard.py
"""

import json
import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

METRICS_FILE = os.path.join(os.path.dirname(__file__), "data", "metrics.json")


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def load_metrics() -> dict | None:
    if not os.path.exists(METRICS_FILE):
        return None
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def history_to_df(history: list[dict]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    return df


# ---------------------------------------------------------------------------
# Sidebar (outside fragment — reruns only on full page rerun / widget change)
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### :material/monitoring: Monitor controls")
        st.toggle("Auto-refresh (3 s)", value=True, key="auto_refresh")

        data = load_metrics()
        if data:
            st.divider()
            st.markdown("### :material/info: System info")
            st.caption(f"**Host:** {data.get('hostname', 'unknown')}")
            st.caption(f"**Platform:** {data.get('platform', 'unknown')}")
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
# Auto-refreshing fragment
# ---------------------------------------------------------------------------

@st.fragment(run_every=timedelta(seconds=3))
def render_live_panel() -> None:
    data = load_metrics()

    if data is None:
        st.warning(
            "No metrics file found. Start the agent with "
            "`python monitoring/agent/agent.py`."
        )
        return

    gateway = data.get("gateway", {})
    cpu = data.get("cpu_usage_percent", 0.0)
    mem = data.get("memory", {})
    disk = data.get("disk", {})
    net = data.get("network", {})
    reachable = gateway.get("reachable", False)
    latency = gateway.get("latency_ms")

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

    # --- CPU & Memory history chart ---
    st.markdown("#### :material/show_chart: CPU & memory history")

    history = data.get("history", [])
    df = history_to_df(history)

    if len(df) >= 2:
        chart_df = df[["cpu", "memory"]].rename(
            columns={"cpu": "CPU %", "memory": "Memory %"}
        )
        st.line_chart(chart_df, height=280)
    else:
        st.caption("Collecting data\u2026 chart appears after a few readings.")

    # --- Network traffic chart ---
    st.markdown("#### :material/network_check: Network traffic")

    if len(df) >= 2:
        net_df = df[["bytes_sent", "bytes_recv"]].rename(
            columns={"bytes_sent": "Sent (B/s)", "bytes_recv": "Recv (B/s)"}
        )
        st.area_chart(net_df, height=240)
        st.caption(
            f"Throughput  \u2014  "
            f"sent: **{_format_bytes(int(net.get('bytes_sent_delta', 0)))}**/s"
            f"  |  received: **{_format_bytes(int(net.get('bytes_recv_delta', 0)))}**/s"
            f"  |  errors: **{net.get('errors', 0)}**"
            f"  |  drops: **{net.get('drops', 0)}**"
        )
    else:
        st.caption("Collecting data\u2026 chart appears after a few readings.")

    # --- Latency chart ---
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

    render_sidebar()
    render_live_panel()


if __name__ == "__main__":
    main()
