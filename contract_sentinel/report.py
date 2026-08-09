from __future__ import annotations

from datetime import datetime, timezone

from .risk import Change, Impact


def render_markdown(
    change: Change,
    impact: Impact,
    assets: list[dict],
    potential_assets: list[dict] | None = None,
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
    return "\n".join(lines)

