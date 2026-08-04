from contract_sentinel import Change, assess_change
from contract_sentinel.fixtures import SHOWCASE_ASSETS
from contract_sentinel.report import render_markdown


change = Change(dataset="commerce.orders", column="customer_id", operation="rename")
downstream = SHOWCASE_ASSETS
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
print("\n" + render_markdown(change, impact, downstream))

