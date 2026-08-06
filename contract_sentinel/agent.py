"""ChangeGuard Autonomous Agent — the core intelligence.

This module implements a step-by-step agent that:
1. Receives a proposed schema change
2. Resolves the dataset URN in DataHub
3. Fetches column-level downstream lineage
4. Scores risk using transparent rules
5. Generates a human-readable impact report
6. Optionally writes the report back to DataHub (with explicit confirmation)

Each step emits structured events so the UI can show the agent's thinking
process in real-time.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .datahub_mcp import DataHubMCPAdapter, ToolCaller
from .fixtures import SHOWCASE_ASSETS
from .report import render_markdown
from .risk import Change, Impact, assess_change


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AgentStep:
    """A single step in the agent's execution pipeline."""

    name: str
    description: str
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class AgentResult:
    """Complete result of an agent run."""

    change: Change
    steps: list[AgentStep] = field(default_factory=list)
    impact: Impact | None = None
    report: str | None = None
    downstream_assets: list[dict] = field(default_factory=list)
    writeback_success: bool = False
    mode: str = "demo"  # "demo" or "live"


# Type for the callback that receives step updates in real-time
StepCallback = Callable[[AgentStep], None]


class ChangeGuardAgent:
    """Autonomous agent that evaluates schema change risk via DataHub.

    Supports two modes:
    - demo: Uses reproducible fixture data (no DataHub required)
    - live: Connects to DataHub MCP server for real metadata
    """

    def __init__(
        self,
        mcp_adapter: DataHubMCPAdapter | None = None,
        on_step_update: StepCallback | None = None,
    ):
        self._mcp = mcp_adapter
        self._on_step = on_step_update or (lambda _: None)

    @property
    def mode(self) -> str:
        return "live" if self._mcp else "demo"

    def _emit(self, step: AgentStep) -> None:
        """Notify listener of step status change."""
        self._on_step(step)

    def run(self, change: Change, confirm_writeback: bool = False) -> AgentResult:
        """Execute the full ChangeGuard pipeline synchronously.

        This is the main entry point. For async contexts, use run_async().
        """
        return asyncio.run(self.run_async(change, confirm_writeback))

    async def run_async(
        self, change: Change, confirm_writeback: bool = False
    ) -> AgentResult:
        """Execute the full ChangeGuard pipeline."""
        result = AgentResult(change=change, mode=self.mode)

        # Step 1: Parse and validate the proposed change
        step1 = AgentStep(
            name="parse_change",
            description=f"Parsing proposed change: {change.operation} on {change.dataset}.{change.column}",
        )
        result.steps.append(step1)
        step1.status = StepStatus.RUNNING
        self._emit(step1)
        t0 = time.perf_counter()

        try:
            # Validation
            valid_ops = {"drop", "rename", "type_change", "add"}
            if change.operation not in valid_ops:
                raise ValueError(
                    f"Unknown operation '{change.operation}'. Valid: {sorted(valid_ops)}"
                )
            step1.result = {
                "dataset": change.dataset,
                "column": change.column,
                "operation": change.operation,
                "new_type": change.new_type,
            }
            step1.status = StepStatus.SUCCESS
            step1.duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            step1.status = StepStatus.FAILED
            step1.error = str(e)
            step1.duration_ms = (time.perf_counter() - t0) * 1000
            self._emit(step1)
            return result
        self._emit(step1)

        # Step 2: Resolve dataset URN in DataHub
        step2 = AgentStep(
            name="resolve_urn",
            description=f"Resolving DataHub URN for dataset '{change.dataset}'",
        )
        result.steps.append(step2)
        step2.status = StepStatus.RUNNING
        self._emit(step2)
        t0 = time.perf_counter()

        dataset_urn: str | None = None
        try:
            if self._mcp:
                # Live mode: search DataHub for the dataset
                search_result = await self._mcp._call_tool(
                    "search",
                    {"query": change.dataset, "types": ["dataset"], "count": 1},
                )
                entities = search_result.get("results", [])
                if entities:
                    dataset_urn = entities[0].get("urn")
                    step2.result = {"urn": dataset_urn, "source": "datahub_search"}
                else:
                    # Construct a plausible URN
                    dataset_urn = f"urn:li:dataset:(urn:li:dataPlatform:snowflake,{change.dataset},PROD)"
                    step2.result = {"urn": dataset_urn, "source": "constructed"}
            else:
                # Demo mode: construct URN from naming convention
                dataset_urn = f"urn:li:dataset:(urn:li:dataPlatform:snowflake,{change.dataset},PROD)"
                step2.result = {"urn": dataset_urn, "source": "demo_convention"}

            step2.status = StepStatus.SUCCESS
            step2.duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            step2.status = StepStatus.FAILED
            step2.error = str(e)
            step2.duration_ms = (time.perf_counter() - t0) * 1000
            # Non-fatal: continue with constructed URN
            dataset_urn = f"urn:li:dataset:(urn:li:dataPlatform:snowflake,{change.dataset},PROD)"
            step2.result = {"urn": dataset_urn, "source": "fallback"}
            step2.status = StepStatus.SUCCESS
        self._emit(step2)

        # Step 3: Fetch downstream column-level lineage
        step3 = AgentStep(
            name="fetch_lineage",
            description=f"Fetching column-level downstream lineage for '{change.column}'",
        )
        result.steps.append(step3)
        step3.status = StepStatus.RUNNING
        self._emit(step3)
        t0 = time.perf_counter()

        downstream: list[dict] = []
        try:
            if self._mcp and dataset_urn:
                lineage_data = await self._mcp.downstream_lineage(
                    dataset_urn, change.column
                )
                # Transform MCP response to our asset format
                raw_assets = lineage_data.get("results", [])
                for asset in raw_assets:
                    downstream.append(
                        {
                            "name": asset.get("name", asset.get("urn", "Unknown")),
                            "urn": asset.get("urn", ""),
                            "kind": _classify_asset(asset),
                            "critical": asset.get("critical", False),
                            "owner": asset.get("owner", "Unknown"),
                            "path": asset.get("path", "lineage path from DataHub"),
                        }
                    )
                step3.result = {
                    "source": "datahub_mcp",
                    "assets_found": len(downstream),
                }
            else:
                # Demo mode: use fixtures
                downstream = SHOWCASE_ASSETS.copy()
                step3.result = {
                    "source": "demo_fixtures",
                    "assets_found": len(downstream),
                }

            step3.status = StepStatus.SUCCESS
            step3.duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            step3.status = StepStatus.FAILED
            step3.error = str(e)
            step3.duration_ms = (time.perf_counter() - t0) * 1000
            # Fallback to fixtures
            downstream = SHOWCASE_ASSETS.copy()
            step3.result = {"source": "fallback_fixtures", "assets_found": len(downstream)}
            step3.status = StepStatus.SUCCESS
        self._emit(step3)
        result.downstream_assets = downstream

        # Step 4: Assess risk
        step4 = AgentStep(
            name="assess_risk",
            description="Calculating risk score with transparent rules",
        )
        result.steps.append(step4)
        step4.status = StepStatus.RUNNING
        self._emit(step4)
        t0 = time.perf_counter()

        try:
            impact = assess_change(change, downstream)
            result.impact = impact
            step4.result = {
                "score": impact.score,
                "severity": impact.severity,
                "affected_count": len(impact.affected_assets),
                "reasons": list(impact.reasons),
            }
            step4.status = StepStatus.SUCCESS
            step4.duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            step4.status = StepStatus.FAILED
            step4.error = str(e)
            step4.duration_ms = (time.perf_counter() - t0) * 1000
            self._emit(step4)
            return result
        self._emit(step4)

        # Step 5: Generate impact report
        step5 = AgentStep(
            name="generate_report",
            description="Generating structured impact report with migration checklist",
        )
        result.steps.append(step5)
        step5.status = StepStatus.RUNNING
        self._emit(step5)
        t0 = time.perf_counter()

        try:
            report = render_markdown(change, impact, downstream)
            result.report = report
            step5.result = {"report_length": len(report), "has_checklist": True}
            step5.status = StepStatus.SUCCESS
            step5.duration_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            step5.status = StepStatus.FAILED
            step5.error = str(e)
            step5.duration_ms = (time.perf_counter() - t0) * 1000
            self._emit(step5)
            return result
        self._emit(step5)

        # Step 6: Write back to DataHub (optional, requires confirmation)
        step6 = AgentStep(
            name="writeback",
            description="Writing impact report back to DataHub for team visibility",
        )
        result.steps.append(step6)
        step6.status = StepStatus.RUNNING
        self._emit(step6)
        t0 = time.perf_counter()

        if not confirm_writeback:
            step6.status = StepStatus.SKIPPED
            step6.result = {"reason": "Writeback requires explicit user confirmation"}
            step6.duration_ms = (time.perf_counter() - t0) * 1000
        elif not self._mcp:
            step6.status = StepStatus.SKIPPED
            step6.result = {"reason": "Demo mode — no DataHub connection for writeback"}
            step6.duration_ms = (time.perf_counter() - t0) * 1000
        else:
            try:
                related_urns = [a.get("urn", "") for a in downstream if a.get("urn")]
                title = f"ChangeGuard: {change.operation} {change.dataset}.{change.column}"
                wb_result = await self._mcp.save_impact_report(
                    title=title,
                    content=report,
                    related_assets=related_urns,
                    confirmed=True,
                )
                result.writeback_success = True
                step6.result = {"written": True, "response": wb_result}
                step6.status = StepStatus.SUCCESS
                step6.duration_ms = (time.perf_counter() - t0) * 1000
            except Exception as e:
                step6.status = StepStatus.FAILED
                step6.error = str(e)
                step6.duration_ms = (time.perf_counter() - t0) * 1000
        self._emit(step6)

        # Step 7: Final decision
        step7 = AgentStep(
            name="decision",
            description="Rendering final deployment decision",
        )
        result.steps.append(step7)
        step7.status = StepStatus.RUNNING
        self._emit(step7)
        t0 = time.perf_counter()

        decision = "BLOCK" if impact.severity in {"critical", "high"} else "ALLOW"
        step7.result = {
            "decision": decision,
            "severity": impact.severity,
            "message": (
                f"Deployment BLOCKED — risk score {impact.score}/100 ({impact.severity}). "
                f"Complete the migration checklist before proceeding."
                if decision == "BLOCK"
                else f"Change may proceed — risk score {impact.score}/100 ({impact.severity})."
            ),
        }
        step7.status = StepStatus.SUCCESS
        step7.duration_ms = (time.perf_counter() - t0) * 1000
        self._emit(step7)

        return result


def _classify_asset(asset: dict) -> str:
    """Classify a DataHub entity into a simple kind string."""
    urn = asset.get("urn", "").lower()
    if "dashboard" in urn:
        return "dashboard"
    if "chart" in urn:
        return "chart"
    if "mlmodel" in urn:
        return "ml_model"
    if "dataflow" in urn or "datajob" in urn:
        return "pipeline"
    return "dataset"
