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
from typing import Any

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


@dataclass(frozen=True)
class PersistedContext:
    """The last ChangeGuard decision read back from DataHub, if any.

    This is DataHub Memory / the last persisted ChangeGuard decision for a
    dataset — not a history or audit trail. ``changeguard_*`` custom
    properties are overwritten on every writeback (see
    ``build_writeback_properties`` above), so only the most recent
    decision is ever available this way.
    """

    decision: str
    risk_score: int | None
    severity: str
    operation: str
    column: str
    timestamp: str


def parse_persisted_context(get_entities_response: Any) -> PersistedContext | None:
    """Parse the ChangeGuard ``changeguard_*`` custom properties out of a
    real ``get_entities`` MCP response, as observed against a live
    ``mcp-server-datahub`` server::

        [
          {
            "urn": "...",
            "properties": {
              "customProperties": [
                {"key": "changeguard_decision", "value": "BLOCK"},
                {"key": "changeguard_risk_score", "value": "60"},
                ...
              ]
            },
            ...
          }
        ]

    Returns ``None`` if the response has no entity, no ``properties``, no
    ``customProperties``, or none of the expected ``changeguard_*`` keys —
    all of these mean "no previously persisted ChangeGuard decision for
    this dataset", which is an expected, non-error outcome, not something
    to raise on.

    This is a pure function so it is trivially testable without a live
    DataHub connection, mirroring ``build_writeback_properties`` above.
    """
    if not isinstance(get_entities_response, list) or not get_entities_response:
        return None

    entity = get_entities_response[0]
    if not isinstance(entity, dict):
        return None

    raw_props = (
        entity.get("properties", {}).get("customProperties", [])
        if isinstance(entity.get("properties"), dict)
        else []
    )
    if not isinstance(raw_props, list):
        return None

    props: dict[str, str] = {}
    for item in raw_props:
        if isinstance(item, dict) and "key" in item and "value" in item:
            props[item["key"]] = item["value"]

    decision = props.get("changeguard_decision")
    if not decision:
        # No ChangeGuard properties on this dataset at all - nothing was
        # ever persisted, not a malformed response.
        return None

    raw_score = props.get("changeguard_risk_score")
    try:
        risk_score = int(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        # An unparseable score must not crash context reading - the rest
        # of the persisted context (decision, severity, etc.) is still
        # useful even if the score field is malformed.
        risk_score = None

    return PersistedContext(
        decision=decision,
        risk_score=risk_score,
        severity=props.get("changeguard_severity", "Unknown"),
        operation=props.get("changeguard_operation", "Unknown"),
        column=props.get("changeguard_column", "Unknown"),
        timestamp=props.get("changeguard_timestamp", "Unknown"),
    )


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
