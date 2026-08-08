# ChangeGuard — Verified Live Example: Safe Rename (ALLOW)

This example documents a real run of ChangeGuard against a live local
DataHub instance (MCP mode), not fixtures or simulated data.

## Proposed Change

| Field | Value |
|---|---|
| Dataset | `commerce.orders` |
| Column | `customer_id` |
| Operation | `rename` |
| New name | `cust_key` |
| Mode | **LIVE** |

## Result

| Field | Value |
|---|---|
| Risk Score | **50/100** |
| Severity | **MEDIUM** |
| Affected Assets | **1** |
| Decision | **ALLOW** |

> Not blocked, but not risk-free either — the change may proceed, but the
> reasons and checklist below should be reviewed before deploying.

## Affected Asset

- **`analytics.customer_orders`** (dataset)

## Verified Lineage

```
commerce.orders.customer_id -> analytics.customer_orders.customer_id
```

This downstream dependency was retrieved live from DataHub via the MCP
`get_lineage` tool — it reflects the real column-level lineage graph
seeded in DataHub for this verification, not a fixture.

## Why This Was Flagged

- Operation `'rename'` contributes 45 risk points.
- 1 downstream asset depends on the column.

## Migration Checklist

- [ ] Notify the owners of every affected asset.
- [ ] Create a backward-compatible column or view.
- [ ] Run downstream validation before deployment.
- [ ] Apply the change in a maintenance window.
- [ ] Verify dashboards and remove the compatibility layer later.
