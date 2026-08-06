"""ChangeGuard CLI Demo — Run the full agent pipeline from the terminal.

This demonstrates the autonomous agent evaluating a schema change without
any external dependencies or paid APIs.

Usage:
    python demo.py
    python demo.py --operation drop --column order_date
"""

import argparse
import sys
import time

from contract_sentinel.agent import ChangeGuardAgent, StepStatus
from contract_sentinel.risk import Change


STEP_ICONS = {
    StepStatus.PENDING: "⏳",
    StepStatus.RUNNING: "🔄",
    StepStatus.SUCCESS: "✅",
    StepStatus.FAILED: "❌",
    StepStatus.SKIPPED: "⏭️",
}


def main():
    parser = argparse.ArgumentParser(description="ChangeGuard Agent — CLI Demo")
    parser.add_argument("--dataset", default="commerce.orders", help="Dataset name")
    parser.add_argument("--column", default="customer_id", help="Column name")
    parser.add_argument(
        "--operation",
        default="rename",
        choices=["rename", "drop", "type_change", "add"],
        help="Schema change operation",
    )
    parser.add_argument("--new-value", default="cust_key", help="New name/type")
    args = parser.parse_args()

    change = Change(
        dataset=args.dataset,
        column=args.column,
        operation=args.operation,
        new_type=args.new_value,
    )

    print()
    print("=" * 60)
    print("  🛡️  CHANGEGUARD — Pre-Deployment Data Contract Sentinel")
    print("=" * 60)
    print()
    print(f"  Proposed change: {change.operation.upper()} {change.dataset}.{change.column}")
    if change.new_type:
        print(f"  New value: {change.new_type}")
    print()
    print("-" * 60)
    print("  Agent Execution")
    print("-" * 60)
    print()

    def on_step(step):
        icon = STEP_ICONS.get(step.status, "?")
        if step.status == StepStatus.RUNNING:
            print(f"  {icon} {step.description}...", end="", flush=True)
        elif step.status in {StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED}:
            duration = f" ({step.duration_ms:.0f}ms)" if step.duration_ms else ""
            if step.status == StepStatus.SKIPPED:
                reason = ""
                if isinstance(step.result, dict):
                    reason = f" — {step.result.get('reason', '')}"
                print(f"\r  {icon} {step.description}{reason}")
            else:
                print(f"\r  {icon} {step.description}{duration}")
            time.sleep(0.15)

    agent = ChangeGuardAgent(on_step_update=on_step)
    result = agent.run(change)

    print()
    print("-" * 60)
    print("  Results")
    print("-" * 60)
    print()

    if result.impact:
        severity = result.impact.severity.upper()
        score = result.impact.score
        decision = "🚫 BLOCKED" if severity in {"CRITICAL", "HIGH"} else "✅ ALLOWED"

        print(f"  Risk Score:      {score}/100")
        print(f"  Severity:        {severity}")
        print(f"  Decision:        {decision}")
        print(f"  Affected Assets: {len(result.impact.affected_assets)}")
        print(f"  Mode:            {result.mode.upper()}")
        print()

        print("  Why flagged:")
        for reason in result.impact.reasons:
            print(f"    • {reason}")
        print()

        print("  Downstream blast radius:")
        for asset in result.downstream_assets:
            critical = " ⚠️ CRITICAL" if asset.get("critical") else ""
            print(f"    → {asset['name']} ({asset['kind']}){critical}")
            print(f"      Owner: {asset['owner']}")
            print(f"      Path:  {asset.get('path', 'N/A')}")
        print()

        print("  Migration checklist:")
        for item in result.impact.checklist:
            print(f"    [ ] {item}")
        print()

        total_ms = sum(s.duration_ms for s in result.steps)
        print(f"  Total execution time: {total_ms:.0f}ms")
    else:
        print("  ❌ Agent failed to produce a risk assessment.")
        sys.exit(1)

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
