"""Deterministic remediation guidance derived from a current analysis.

This module is intentionally separate from risk scoring. It consumes the
already-computed decision and the confirmed/potential lineage sets, performs
no external calls, and never changes the score or classification of an asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .risk import Change


@dataclass(frozen=True)
class RemediationAction:
    """One ordered, presentation-neutral remediation recommendation."""

    kind: str
    title: str
    detail: str
    assets: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RemediationPlan:
    """Structured guidance for the next review or re-evaluation cycle."""

    required: bool
    summary: str
    steps: tuple[RemediationAction, ...] = field(default_factory=tuple)


def build_remediation_plan(
    change: Change,
    severity: str,
    decision: str,
    confirmed_assets: list[dict],
    potential_assets: list[dict],
) -> RemediationPlan:
    """Build deterministic advice from the current ChangeGuard result.

    Confirmed assets retain their column-level status. Potential assets are
    always described as an informational review set and never as confirmed
    impact. Previous DataHub context is deliberately not an input.
    """
    if change.operation not in {"drop", "rename", "type_change", "add"}:
        raise ValueError(f"Unsupported remediation operation: {change.operation}")
    if decision not in {"ALLOW", "BLOCK", "REVIEW"}:
        raise ValueError(f"Unsupported ChangeGuard decision: {decision}")

    confirmed = _asset_names(confirmed_assets)
    potential = _asset_names(potential_assets)
    required = decision in {"BLOCK", "REVIEW"}

    if decision == "REVIEW":
        potential_detail = (
            "Confirm whether these table-level potential dependencies use the "
            f"changed column; they remain unconfirmed: {_join_names(potential)}."
            if potential
            else (
                "Confirm whether any table-level or uncataloged dependencies use "
                "the changed column; no potential assets were returned."
            )
        )
        return RemediationPlan(
            required=True,
            summary=(
                "Evidence verification is required before ChangeGuard can issue "
                "ALLOW. REVIEW does not mean the change is confirmed dangerous."
            ),
            steps=(
                RemediationAction(
                    kind="coverage_verification",
                    title="Verify lineage and catalog coverage",
                    detail=(
                        "Verify the available lineage and catalog coverage for "
                        f"{change.dataset}.{change.column}."
                    ),
                ),
                RemediationAction(
                    kind="external_consumer_review",
                    title="Check for consumers outside the catalog",
                    detail=(
                        "Check application, pipeline, and reporting consumers that "
                        "may not be represented in DataHub."
                    ),
                ),
                RemediationAction(
                    kind="potential_review",
                    title="Confirm potential dependency usage",
                    detail=potential_detail,
                    assets=potential,
                ),
                RemediationAction(
                    kind="re_evaluate",
                    title="Re-run ChangeGuard",
                    detail=(
                        "Re-run ChangeGuard after the evidence and coverage have "
                        "been verified."
                    ),
                ),
            ),
        )

    if required:
        summary = (
            "Recommended path to re-evaluation. Complete the required migration "
            "and review steps, then run ChangeGuard again."
        )
    elif severity == "low" and not confirmed and not potential:
        summary = "No blocking remediation required."
    else:
        summary = (
            "No blocking remediation is required, but the following review "
            "steps are recommended."
        )

    steps: list[RemediationAction] = []

    if change.operation == "drop":
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="confirmed_migration",
                    title="Migrate or update confirmed consumers",
                    detail=(
                        "Address these confirmed column-level consumers before "
                        f"removing {change.dataset}.{change.column}: "
                        f"{_join_names(confirmed)}."
                    ),
                    assets=confirmed,
                )
            )
        else:
            steps.append(
                RemediationAction(
                    kind="confirmed_validation",
                    title="Validate the destructive change",
                    detail=(
                        "No confirmed column-level consumers were found. Review "
                        "the current lineage and deployment context before "
                        f"removing {change.dataset}.{change.column}."
                    ),
                )
            )
        _append_potential_review(steps, potential)
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="compatibility_transition",
                    title="Plan a compatibility window",
                    detail=(
                        "Consider preserving backward compatibility during the "
                        "migration until confirmed consumers have been updated."
                    ),
                )
            )
        steps.append(
            RemediationAction(
                kind="re_evaluate",
                title="Re-run ChangeGuard",
                detail=(
                    "Re-run ChangeGuard after the downstream migration is "
                    "complete and before removing the column."
                ),
            )
        )

    elif change.operation == "rename":
        target = change.new_type or "the proposed new column name"
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="confirmed_reference_update",
                    title="Update references in confirmed consumers",
                    detail=(
                        f"Update references from {change.dataset}.{change.column} "
                        f"to {target} in these confirmed column-level consumers: "
                        f"{_join_names(confirmed)}."
                    ),
                    assets=confirmed,
                )
            )
        else:
            steps.append(
                RemediationAction(
                    kind="confirmed_validation",
                    title="Validate rename references",
                    detail=(
                        "No confirmed column-level consumers were found. Review "
                        "application references not represented in current "
                        f"lineage before renaming {change.column} to {target}."
                    ),
                )
            )
        _append_potential_review(steps, potential)
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="compatibility_transition",
                    title="Consider a compatibility transition",
                    detail=(
                        "Consider a compatibility transition or alias while "
                        f"confirmed consumers still depend on {change.column}."
                    ),
                )
            )
        steps.append(
            RemediationAction(
                kind="re_evaluate",
                title="Re-run ChangeGuard if the proposal changes",
                detail=(
                    "If the migration or proposed schema change is modified, "
                    "re-run ChangeGuard before deployment."
                ),
            )
        )

    elif change.operation == "type_change":
        target = change.new_type or "the proposed target type"
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="confirmed_compatibility_review",
                    title="Validate and migrate incompatible consumers",
                    detail=(
                        f"Check compatibility with {target} and update any "
                        "incompatible confirmed column-level consumers: "
                        f"{_join_names(confirmed)}."
                    ),
                    assets=confirmed,
                )
            )
        else:
            steps.append(
                RemediationAction(
                    kind="confirmed_validation",
                    title="Validate type compatibility",
                    detail=(
                        "No confirmed column-level consumers were found. Validate "
                        f"the change to {target} against application contracts "
                        "not represented in current lineage."
                    ),
                )
            )
        _append_potential_review(steps, potential)
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="compatibility_transition",
                    title="Plan a staged type transition",
                    detail=(
                        "Consider a compatibility window until incompatible "
                        "confirmed consumers have been updated and validated."
                    ),
                )
            )
        steps.append(
            RemediationAction(
                kind="re_evaluate",
                title="Re-run ChangeGuard",
                detail=(
                    "Re-run ChangeGuard after consumer compatibility work or if "
                    "the proposed target type changes."
                ),
            )
        )

    else:  # add
        steps.append(
            RemediationAction(
                kind="additive_validation",
                title="Validate the additive column contract",
                detail=(
                    f"Review the intended type and null/default behavior for "
                    f"{change.dataset}.{change.column} before deployment."
                ),
            )
        )
        if confirmed:
            steps.append(
                RemediationAction(
                    kind="confirmed_context_review",
                    title="Review confirmed lineage context",
                    detail=(
                        "Review these confirmed column-level assets in the "
                        "additive-change context; no destructive consumer change "
                        f"is implied: {_join_names(confirmed)}."
                    ),
                    assets=confirmed,
                )
            )
        _append_potential_review(steps, potential)
        steps.append(
            RemediationAction(
                kind="re_evaluate",
                title="Re-run ChangeGuard if the proposal changes",
                detail=(
                    "Re-run ChangeGuard if the additive proposal or downstream "
                    "context changes."
                ),
            )
        )

    return RemediationPlan(required=required, summary=summary, steps=tuple(steps))


def _append_potential_review(
    steps: list[RemediationAction], potential: tuple[str, ...]
) -> None:
    if not potential:
        return
    steps.append(
        RemediationAction(
            kind="potential_review",
            title="Review potential downstream propagation",
            detail=(
                "Table-level lineage suggests these assets may require review; "
                "column-level impact is not confirmed: "
                f"{_join_names(potential)}."
            ),
            assets=potential,
        )
    )


def _asset_names(assets: list[dict]) -> tuple[str, ...]:
    names = {
        str(asset.get("name") or asset.get("urn") or "Unknown") for asset in assets
    }
    return tuple(sorted(names, key=lambda name: (name.casefold(), name)))


def _join_names(names: tuple[str, ...]) -> str:
    return ", ".join(names)
