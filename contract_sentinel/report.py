from __future__ import annotations

from datetime import datetime, timezone

from .risk import Change, Impact


def render_markdown(change: Change, impact: Impact, assets: list[dict]) -> str:
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
    lines.extend(["", "## Downstream blast radius"])
    for asset in assets:
        lines.append(
            f"- **{asset['name']}** ({asset['kind']}) — owner: {asset.get('owner', 'Unknown')}  "
            f"\n  `{asset.get('path', 'lineage path unavailable')}`"
        )
    lines.extend(["", "## Migration checklist"])
    lines.extend(f"- [ ] {item}" for item in impact.checklist)
    return "\n".join(lines)

