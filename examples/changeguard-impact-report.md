# ChangeGuard Impact Report

Change: `rename` on `commerce.orders.customer_id`  
Proposed replacement: `customer_key`  
Risk: **90/100 — CRITICAL**

## Decision

Deployment is blocked until the migration checklist is completed.

## Why this was flagged

- The rename operation contributes 45 risk points.
- Three downstream assets depend on the column.
- Two affected assets are marked critical.
- Two business dashboards may show incorrect results.

## Downstream blast radius

- **Revenue Executive Dashboard** — owner: Finance Analytics  
  `orders.customer_id → revenue_model → executive_dashboard`
- **customer_lifetime_value** — owner: Growth Data  
  `orders.customer_id → customer_lifetime_value`
- **Weekly Retention Report** — owner: Lifecycle Analytics  
  `orders.customer_id → retention_model → weekly_retention`

## Safe migration checklist

- [ ] Notify the owners of every affected asset.
- [ ] Create a backward-compatible column or view.
- [ ] Run downstream validation before deployment.
- [ ] Apply the change in a maintenance window.
- [ ] Verify dashboards and remove the compatibility layer later.

This deterministic example corresponds to the seeded DataHub showcase scenario
included with the repository. In connected mode the same report is created from
the asset and column lineage returned by DataHub MCP.
