"""Persistent writeback of ChangeGuard decisions to DataHub.

This module writes a ChangeGuard decision (ALLOW/BLOCK, risk score,
severity, operation, column, timestamp) as custom properties on the
analyzed dataset, using the official DataHub Python SDK's REST emitter
and ``DatasetPatchBuilder``.

Deliberately separate from ``datahub_mcp.py``: MCP remains the source for
read operations (search, lineage) in Live mode, while this module performs
a direct, official-SDK write to DataHub's GMS REST API. This avoids relying
on the MCP ``save_document`` tool, which is not guaranteed to be available
(it is a Document Tool that ``mcp-server-datahub`` hides when the target
DataHub instance has no documents yet).

Custom properties are additive: ``DatasetPatchBuilder`` issues a JSON patch
against the ``datasetProperties`` aspect, so existing properties (and the
dataset's description) are preserved rather than overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from datahub.emitter.mce_builder import make_dataset_urn
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.specific.dataset import DatasetPatchBuilder


@dataclass(frozen=True)
class WritebackRecord:
    """The persisted ChangeGuard decision, as written to DataHub."""

    dataset_urn: str
    decision: str
    risk_score: int
    severity: str
    operation: str
    column: str
    timestamp: str
    properties: dict[str, str]


def build_writeback_properties(
    decision: str,
    risk_score: int,
    severity: str,
    operation: str,
    column: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build the custom property dict for a ChangeGuard decision.

    Kept as a pure function so it is trivially testable without a live
    DataHub connection.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "changeguard_decision": decision,
        "changeguard_risk_score": str(risk_score),
        "changeguard_severity": severity,
        "changeguard_operation": operation,
        "changeguard_column": column,
        "changeguard_timestamp": ts,
    }


def write_decision_to_datahub(
    datahub_url: str,
    dataset_name: str,
    decision: str,
    risk_score: int,
    severity: str,
    operation: str,
    column: str,
    platform: str = "snowflake",
    env: str = "PROD",
    datahub_token: str | None = None,
) -> WritebackRecord:
    """Persist a ChangeGuard decision as custom properties on a dataset.

    Uses the official DataHub Python SDK (``acryl-datahub``) REST emitter
    directly against the GMS API — the same mechanism used by
    ``datahub docker quickstart`` ingestion. This is independent of the
    MCP server and its tool availability.

    Raises whatever the underlying emitter raises on failure (e.g.
    connection errors); callers are expected to require explicit user
    confirmation before invoking this function, since it mutates DataHub.
    """
    dataset_urn = make_dataset_urn(platform, dataset_name, env)
    properties = build_writeback_properties(
        decision, risk_score, severity, operation, column
    )

    emitter = DatahubRestEmitter(
        gms_server=datahub_url, token=datahub_token or None
    )
    emitter.test_connection()

    patch_builder = DatasetPatchBuilder(dataset_urn)
    for key, value in properties.items():
        patch_builder.add_custom_property(key, value)

    for mcp in patch_builder.build():
        emitter.emit(mcp)

    return WritebackRecord(
        dataset_urn=dataset_urn,
        decision=decision,
        risk_score=risk_score,
        severity=severity,
        operation=operation,
        column=column,
        timestamp=properties["changeguard_timestamp"],
        properties=properties,
    )
