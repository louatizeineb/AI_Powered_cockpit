from __future__ import annotations


def compare_metric(metric_name: str, baseline_value: float, v2_value: float) -> dict[str, float | str]:
    """Compare one baseline metric with a v2 metric."""

    delta = v2_value - baseline_value
    return {"metric_name": metric_name, "baseline_value": baseline_value, "v2_value": v2_value, "delta_value": delta}
