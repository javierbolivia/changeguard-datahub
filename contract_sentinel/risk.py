from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Change:
    dataset: str
    column: str
    operation: str
    new_type: str | None = None


@dataclass(frozen=True)
class Impact:
    score: int
    severity: str
    affected_assets: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    checklist: tuple[str, ...] = field(default_factory=tuple)


def assess_change(change: Change, downstream_assets: list[dict]) -> Impact:
    """Score a schema change using transparent, reproducible rules."""
    operation_weight = {"drop": 55, "rename": 45, "type_change": 35, "add": 5}
    score = operation_weight.get(change.operation, 25)
    reasons = [f"Operation '{change.operation}' contributes {score} risk points."]

    critical = sum(1 for asset in downstream_assets if asset.get("critical"))
    dashboards = sum(1 for asset in downstream_assets if asset.get("kind") == "dashboard")
    score += min(len(downstream_assets) * 5, 25)
    score += min(critical * 10, 20)
    score += min(dashboards * 5, 10)
    score = min(score, 100)

    if downstream_assets:
        reasons.append(f"{len(downstream_assets)} downstream assets depend on the column.")
    if critical:
        reasons.append(f"{critical} affected assets are marked critical.")
    if dashboards:
        reasons.append(f"{dashboards} business dashboards may show incorrect results.")

    severity = "critical" if score >= 80 else "high" if score >= 60 else "medium" if score >= 30 else "low"
    assets = tuple(asset["name"] for asset in downstream_assets)
    checklist = (
        "Notify the owners of every affected asset.",
        "Create a backward-compatible column or view.",
        "Run downstream validation before deployment.",
        "Apply the change in a maintenance window.",
        "Verify dashboards and remove the compatibility layer later.",
    )
    return Impact(score, severity, assets, tuple(reasons), checklist)

