"""
Hybrid Network Monitoring Dashboard (Streamlit).

Reads the latest metrics JSON produced by the agent and renders a
clean, visual overview of CPU usage and gateway reachability.

Dependencies:
    pip install streamlit pandas

Run with:
    streamlit run monitoring/dashboard/dashboard.py
"""

import json
import os
from datetime import timedelta

import pandas as pd
import streamlit as st

METRICS_FILE = os.path.join(os.path.dirname(__file__), "data", "metrics.json")


def load_metrics() -> dict | None:
    """Load the latest metrics payload, or None if unavailable."""
    if not os.path.exists(METRICS_FILE):
        return None
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


@st.fragment(run_every=timedelta(seconds=3))
def render_live_metrics():
    """Auto-refreshing fragment that re-reads metrics every 3 s."""
    data = load_metrics()

    if data is None:
        st.warning(
            "No metrics file found. Start the agent with "
            "`python monitoring/agent/agent.py` so it begins writing data."
        )
        return

    gateway = data.get("gateway", {})
    cpu = data.get("cpu_usage_percent", 0.0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Host", data.get("hostname", "unknown"))
    with col2:
        st.metric("CPU Usage", f"{cpu}%")
    with col3:
        reachable = gateway.get("reachable", False)
        latency = gateway.get("latency_ms")
        label = "Gateway" if reachable else "Gateway DOWN"
        value = f"{latency} ms" if latency is not None else "\u2014"
        st.metric(label, value)

    st.subheader("CPU Utilization")
    st.progress(min(float(cpu) / 100.0, 1.0))
    st.write(f"Current load: **{cpu}%**")

    st.subheader("Gateway Reachability")
    if reachable:
        st.success(
            f"Gateway {gateway.get('ip')} is reachable "
            f"(latency {latency} ms)"
        )
    else:
        st.error(f"Gateway {gateway.get('ip')} is unreachable")

    st.subheader("Raw Metrics")
    df = pd.json_normalize(data)
    st.dataframe(df, use_container_width=True)

    st.caption(f"Last updated: {data.get('timestamp', 'unknown')}")


def main() -> None:
    st.set_page_config(
        page_title="Hybrid Network Monitor",
        page_icon="\U0001F4E1",
        layout="wide",
    )

    st.title("Automated Hybrid Network & Monitoring Dashboard")
    st.caption("Live metrics collected from clients via the Python agent")

    render_live_metrics()


if __name__ == "__main__":
    main()
