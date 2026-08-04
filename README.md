# ChangeGuard: Data Contract Sentinel

ChangeGuard is a DataHub-powered pre-deployment agent that prevents breaking
data contract changes. It examines a proposed schema change, traces its
downstream blast radius, calculates an explainable risk score, and creates a
safe migration checklist before deployment.

Built for **Build with DataHub: The Agent Hackathon**.

## Current capabilities

- Detects risky drop, rename, type-change, and add operations.
- Scores impact using downstream dependencies, criticality, and dashboards.
- Explains every factor contributing to the risk score.
- Produces a concrete migration checklist.
- Runs deterministically without a paid LLM API.
- Includes automated tests and a reproducible sample scenario.

## Run the prototype

Python 3.11 or newer is recommended. The current core has no external
dependencies.

```bash
python demo.py
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## Example

The included demo evaluates a proposed rename of
`commerce.orders.customer_id`. It discovers three downstream consumers,
including critical datasets and executive dashboards, and reports a critical
risk with an actionable migration plan.

## Architecture roadmap

The next milestone connects the tested risk engine to DataHub's MCP Server and
lineage graph, adds a visual interface, and writes the resulting risk decision
back to DataHub so future developers and agents inherit the context.

## License

Apache License 2.0. See [LICENSE](LICENSE).
