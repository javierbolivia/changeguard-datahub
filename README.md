# ChangeGuard: Data Contract Sentinel

![ChangeGuard project artwork](assets/changeguard-thumbnail.png)

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
- Provides a validated boundary for the official DataHub MCP read and write
  tools, with every mutation protected by explicit confirmation.

## Run the prototype

Python 3.11 or newer is recommended. The current core has no external
dependencies.

```bash
python demo.py
```

Launch the visual interface:

```bash
pip install -r requirements.txt
streamlit run app.py
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
lineage graph and writes the resulting risk decision back to DataHub so future
developers and agents inherit the context.

## DataHub MCP integration

The production path uses the official `mcp-server-datahub@0.6.0` over stdio.
At startup ChangeGuard validates the advertised tool list instead of assuming
an API is present. It reads column-level downstream lineage with `get_lineage`
and writes a confirmed impact report back with `save_document`.

See [`examples/datahub-mcp.example.json`](examples/datahub-mcp.example.json).
Keep the DataHub token in local environment variables or a secret manager;
never commit it to Git.

## License

Apache License 2.0. See [LICENSE](LICENSE).
