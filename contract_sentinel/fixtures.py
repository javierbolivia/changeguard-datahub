"""Reproducible metadata used by the demo and offline judging mode."""

SHOWCASE_ASSETS = [
    {
        "name": "Revenue Executive Dashboard",
        "urn": "urn:li:dashboard:(looker,revenue-executive)",
        "kind": "dashboard",
        "critical": True,
        "owner": "Finance Analytics",
        "path": "orders.customer_id → revenue_model → executive_dashboard",
    },
    {
        "name": "customer_lifetime_value",
        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.customer_lifetime_value,PROD)",
        "kind": "dataset",
        "critical": True,
        "owner": "Growth Data",
        "path": "orders.customer_id → customer_lifetime_value",
    },
    {
        "name": "Weekly Retention Report",
        "urn": "urn:li:dashboard:(looker,weekly-retention)",
        "kind": "dashboard",
        "critical": False,
        "owner": "Lifecycle Analytics",
        "path": "orders.customer_id → retention_model → weekly_retention",
    },
]

