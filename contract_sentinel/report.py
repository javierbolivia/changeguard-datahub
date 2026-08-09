from __future__ import annotations

from datetime import datetime, timezone

from .datahub_writeback import PersistedContext
from .risk import Change, Impact


def render_markdown(
    change: Change,
    impact: Impact,
    assets: list[dict],
    potential_assets: list[dict] | None = None,
    previous_context: PersistedContext | None = None,
) -> str:
    potential_assets = potential_assets or []
    lines = [
        "# ChangeGuard Impact Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Change: `{change.operation}` on `{change.dataset}.{change.column}`",
        f"Risk: **{impact.score}/100 — {impact.severity.upper()}**",
        "",
        "## Why this was flagged",
    ]
    lines.extend(f"- {reason}" for reason in impact.reasons)
    lines.extend(
        [
            "",
            f"Confirmed affected assets: {len(assets)}",
            f"Potential downstream assets: {len(potential_assets)}",
            "",
            "## Downstream blast radius (confirmed column-level impact)",
        ]
    )
    for asset in assets:
        lines.append(
            f"- **{asset['name']}** ({asset['kind']}) — owner: {asset.get('owner', 'Unknown')}  "
            f"\n  `{asset.get('path', 'lineage path unavailable')}`"
        )
    if potential_assets:
        lines.extend(["", "## Potential downstream propagation (table-level only)"])
        lines.append(
            "These datasets are downstream of the table, but DataHub has no "
            "confirmed column-level lineage for this specific column. They "
            "are not counted in the risk score."
        )
        for asset in potential_assets:
            lines.append(
                f"- **{asset['name']}** ({asset['kind']})  "
                f"\n  `{asset.get('path', 'lineage path unavailable')}`"
            )
    lines.extend(["", "## Migration checklist"])
    lines.extend(f"- [ ] {item}" for item in impact.checklist)
    if potential_assets:
        lines.append(
            "- [ ] Review potential downstream datasets where column-level "
            "lineage is incomplete."
        )

    if previous_context is not None:
        lines.extend(["", "## Previous ChangeGuard Context"])
        lines.append(
            "This is the last ChangeGuard decision persisted in DataHub "
            "for this dataset — not a history or audit trail; it reflects "
            "only the most recent writeback."
        )
        score_display = (
            previous_context.risk_score
            if previous_context.risk_score is not None
            else "Unknown"
        )
        lines.append(f"- Decision: {previous_context.decision}")
        lines.append(f"- Risk: {score_display} / {previous_context.severity}")
        lines.append(f"- Operation: {previous_context.operation}")
        lines.append(f"- Column: {previous_context.column}")
        lines.append(f"- Evaluated: {previous_context.timestamp}")
    else:
        lines.extend(["", "## Previous ChangeGuard Context"])
        lines.append("No previously persisted ChangeGuard decision found for this dataset.")

    return "\n".join(lines)

