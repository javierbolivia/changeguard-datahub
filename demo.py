from contract_sentinel import Change, assess_change


change = Change(dataset="commerce.orders", column="customer_id", operation="rename")
downstream = [
    {"name": "Revenue Executive Dashboard", "kind": "dashboard", "critical": True},
    {"name": "customer_lifetime_value", "kind": "dataset", "critical": True},
    {"name": "Weekly Retention Report", "kind": "dashboard", "critical": False},
]
impact = assess_change(change, downstream)

print("CONTRACT SENTINEL — PRE-DEPLOYMENT REPORT")
print(f"Change: {change.operation} {change.dataset}.{change.column}")
print(f"Risk: {impact.score}/100 ({impact.severity.upper()})")
print("Affected assets:")
for asset in impact.affected_assets:
    print(f"  - {asset}")
print("Migration checklist:")
for item in impact.checklist:
    print(f"  [ ] {item}")

