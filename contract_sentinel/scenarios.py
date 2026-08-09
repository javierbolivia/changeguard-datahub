"""Truthful, reusable quick-scenario presets for the Streamlit demo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuickScenario:
    dataset: str
    column: str
    operation: str
    new_value: str
    mode: str


SAFE_RENAME = QuickScenario(
    dataset="commerce.orders",
    column="customer_id",
    operation="rename",
    new_value="cust_key",
    mode="live",
)

DANGEROUS_DROP = QuickScenario(
    dataset="commerce.orders",
    column="customer_id",
    operation="drop",
    new_value="",
    mode="live",
)


def streamlit_state_for(scenario: QuickScenario) -> dict[str, str]:
    """Return the exact form state a quick-scenario button should apply."""
    return {
        "cg_dataset": scenario.dataset,
        "cg_column": scenario.column,
        "cg_operation": scenario.operation,
        "cg_new_value": scenario.new_value,
        "cg_mode": (
            "Live (DataHub MCP)" if scenario.mode == "live" else "Demo (fixtures)"
        ),
    }
