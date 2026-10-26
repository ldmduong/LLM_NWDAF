"""Render charts from validated Prometheus results; no LLM generated plotting code."""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from prom_query import query_prometheus


def query_and_plot(metric: str):
    """Query an allowlisted metric and pass its verified result to the chart builder."""
    result = query_prometheus(metric)
    return plot_analytics(result)


def plot_analytics(result: dict[str, Any]):
    """Return a Matplotlib figure for one supported metric result envelope."""
    if result.get("status") != "ok":
        raise ValueError("Only successful Prometheus results can be visualized")

    metric = result.get("metric")
    frame = pd.DataFrame(result.get("data", []))
    if frame.empty:
        raise ValueError("No backend data is available to plot")

    fig, ax = plt.subplots(figsize=(10, 5))
    if metric == "active_UEs":
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp")
        ax.plot(frame["timestamp"], frame["active_UEs"], marker="o")
        ax.set(xlabel="Time (UTC)", ylabel="Active UE count", title="Active UEs over time")
    elif metric == "amf_ue_registration_state":
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame["state_desc"] = frame["state_desc"].fillna("unknown")
        counts = frame.groupby([pd.Grouper(key="timestamp", freq="5min"), "state_desc"]).size().unstack(fill_value=0)
        counts.plot(kind="line", marker="o", ax=ax)
        ax.set(xlabel="Time (UTC)", ylabel="UE state events", title="Registration state events")
    elif metric == "UE_location_report":
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        counts = frame.groupby([pd.Grouper(key="time", freq="5min"), "NrCellId"]).size().unstack(fill_value=0)
        counts.plot(kind="line", marker="o", ax=ax)
        ax.set(xlabel="Time (UTC)", ylabel="Handover reports", title="Handover reports by cell")
    elif metric == "ue_destination_visits_total":
        frame["location_type"] = frame["location_type"].fillna("unknown")
        frame.groupby("location_type").size().sort_values(ascending=False).plot(kind="bar", ax=ax)
        ax.set(xlabel="Simulated destination", ylabel="Visit records", title="Destination visit records")
    else:
        raise ValueError(f"Unsupported metric for visualization: {metric}")

    source = result.get("source", "unknown source")
    queried_at = result.get("queried_at", "unknown time")
    fig.suptitle(f"Source: {source} | Queried at: {queried_at}", fontsize=9)
    fig.tight_layout()
    return fig

