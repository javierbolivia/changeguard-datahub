# ChangeGuard — Verified Live Example: Dangerous Drop (BLOCK)

This example documents a real run of ChangeGuard against a live local
DataHub instance (MCP mode), not fixtures or simulated data.

## Proposed Change

| Field | Value |
|---|---|
| Dataset | `commerce.orders` |
| Column | `customer_id` |
| Operation | `drop` |
| Mode | **LIVE** |

## Result

| Field | Value |
|---|---|
| Risk Score | **60/100** |
| Severity | **HIGH** |
| Affected Assets | **1** |
| Decision | **BLOCK** |

> Deployment blocked — the migration checklist must be completed before
> this change can proceed.

## Affected Asset

- **`analytics.customer_orders`** (dataset)

## Verified Lineage

```
commerce.orders.customer_id -> analytics.customer_orders.customer_id
```

This downstream dependency was retrieved live from DataHub via the MCP
`get_lineage` tool — it reflects the real column-level lineage graph
seeded in DataHub for this verification, not a fixture.

## Why `drop` Is Riskier Than `rename`

Both scenarios hit the same real downstream asset (`analytics.customer_orders`),
yet `drop` scores higher and crosses the BLOCK threshold while `rename` does
not. The difference comes from the base risk assigned to the operation
itself:

- `rename` contributes 45 base points — the column still exists with its
  data, so a backward-compatible view can bridge the transition.
- `drop` contributes 55 base points — the column and its data are removed
  outright. There is nothing to fall back on: any downstream consumer
  reading `customer_id`, including `analytics.customer_orders`, breaks
  immediately and irreversibly without a backup or migration in place.

That 10-point difference in the operation's base score is enough to push
this scenario from 50/100 (MEDIUM, ALLOW) to 60/100 (HIGH, BLOCK).
