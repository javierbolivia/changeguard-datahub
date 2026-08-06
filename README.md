# ChangeGuard: Pre-Deployment Data Contract Sentinel

![ChangeGuard](assets/changeguard-thumbnail.png)

**ChangeGuard** is an autonomous agent that prevents breaking data contract changes before they reach production. It reads DataHub's lineage graph through the MCP Server, traces column-level downstream impact, scores risk with transparent rules, and writes the decision back so the next person or agent inherits the context.

Built for **[Build with DataHub: The Agent Hackathon](https://datahub.devpost.com/)** — Category: *Agents That Do Real Work*.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                  PROPOSED SCHEMA CHANGE                       │
│         rename commerce.orders.customer_id → cust_key        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    CHANGEGUARD AGENT                          │
│                                                              │
│  Step 1 → Parse & validate change                            │
│  Step 2 → Resolve dataset URN in DataHub (search)            │
│  Step 3 → Fetch column-level downstream lineage (get_lineage)│
│  Step 4 → Score risk (transparent rules, no LLM)             │
│  Step 5 → Generate impact report with migration checklist    │
│  Step 6 → Write report back to DataHub (save_document)       │
│  Step 7 → BLOCK or ALLOW deployment decision                 │
│                                                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    OUTPUT                                     │
│                                                              │
│  • Risk score: 90/100 (CRITICAL)                             │
│  • Decision: BLOCK deployment                                │
│  • 3 downstream assets at risk                               │
│  • Migration checklist generated                             │
│  • Report written to DataHub for team visibility             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## DataHub MCP Integration

ChangeGuard uses the official **`mcp-server-datahub`** over stdio. At startup, the agent validates the advertised tool list to fail fast if a required tool is missing.

| MCP Tool Used | Purpose |
|---|---|
| `search` | Find datasets by name → resolve URN |
| `get_entities` | Retrieve asset metadata and ownership |
| `list_schema_fields` | Understand column structure |
| `get_lineage` | Column-level downstream traversal (max 3 hops) |
| `get_lineage_paths_between` | Trace full dependency path |
| `save_document` | Write impact report back to DataHub |

The agent **reads** DataHub to understand what's connected to what, **takes action** (risk scoring + report generation), and **writes results back** so the next person or agent inherits the knowledge.

### Writeback Safety

All mutations are protected by explicit confirmation. The agent will never write to DataHub without the user explicitly enabling writeback. This is enforced at the code level with a `PermissionError` gate.

---

## Quick Start

### Prerequisites

- Python 3.11+
- No paid API keys required (deterministic agent, no LLM dependency)

### Run the Agent (CLI)

```bash
git clone https://github.com/javierbolivia/changeguard-datahub.git
cd changeguard-datahub
python demo.py
```

### Run the Visual Interface

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Run Tests

```bash
python -m unittest discover -s tests -v
```

### Connect to DataHub (Live Mode)

1. Start DataHub locally ([Quickstart Guide](https://datahubproject.io/docs/quickstart)):

```bash
python -m datahub docker quickstart
```

2. Configure the MCP server (see [`examples/datahub-mcp.example.json`](examples/datahub-mcp.example.json)):

```json
{
  "mcpServers": {
    "datahub": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-datahub@0.6.0"],
      "env": {
        "DATAHUB_GMS_URL": "http://localhost:8080",
        "DATAHUB_TOKEN": "your-token-here"
      }
    }
  }
}
```

3. Switch the app to "Live (DataHub MCP)" mode in the sidebar.

---

## Project Structure

```
changeguard-datahub/
├── app.py                          # Streamlit UI with real-time agent viz
├── demo.py                         # CLI demo (no dependencies)
├── contract_sentinel/
│   ├── __init__.py                 # Package exports
│   ├── agent.py                    # Autonomous 7-step agent pipeline
│   ├── risk.py                     # Transparent risk scoring engine
│   ├── report.py                   # Markdown report generator
│   ├── fixtures.py                 # Reproducible demo metadata
│   └── datahub_mcp.py             # MCP Server adapter (boundary-tested)
├── tests/
│   ├── test_agent.py               # Agent pipeline tests
│   ├── test_risk.py                # Risk scoring tests
│   ├── test_report.py              # Report generation tests
│   └── test_datahub_mcp.py         # MCP adapter tests
├── examples/
│   ├── changeguard-impact-report.md   # Sample agent output
│   └── datahub-mcp.example.json       # MCP server config template
├── requirements.txt
├── LICENSE                         # Apache 2.0
└── README.md
```

---

## Risk Scoring Transparency

ChangeGuard uses **deterministic, explainable rules** instead of opaque LLM judgments. Every factor contributing to the score is visible:

| Factor | Points |
|--------|--------|
| `drop` operation | 55 base |
| `rename` operation | 45 base |
| `type_change` operation | 35 base |
| `add` operation | 5 base |
| Per downstream asset (capped at 25) | +5 |
| Per critical asset (capped at 20) | +10 |
| Per business dashboard (capped at 10) | +5 |

**Decision thresholds:** Critical (≥80) → BLOCK | High (≥60) → BLOCK | Medium (≥30) → WARN | Low (<30) → ALLOW

---

## Example Output

The included demo evaluates a proposed `rename` of `commerce.orders.customer_id`. The agent discovers:

- **3 downstream consumers** via DataHub lineage
- **2 critical assets** (Revenue Dashboard, Customer LTV dataset)
- **Risk score: 90/100** → Deployment BLOCKED
- Generated migration checklist with 5 action items

See the full output: [`examples/changeguard-impact-report.md`](examples/changeguard-impact-report.md)

---

## Why ChangeGuard?

| Problem | ChangeGuard Solution |
|---------|---------------------|
| Schema changes break downstream silently | Agent traces full blast radius before deploy |
| No visibility into who is affected | Surfaces owners and critical assets |
| Risk is assessed subjectively | Transparent scoring with visible factors |
| Knowledge stays in one person's head | Writes report back to DataHub for team |
| Requires expensive LLM APIs | Deterministic rules — free and reproducible |

---

## Technologies

- **Python 3.11+** — Core implementation
- **DataHub MCP Server** — Metadata access and writeback
- **Streamlit** — Interactive demo interface
- **Pydantic** — Data validation
- **NetworkX** — Graph traversal utilities

---

## License

[Apache License 2.0](LICENSE)

---

## Team

Built by [@javierbolivia](https://github.com/javierbolivia) for the DataHub Agent Hackathon 2026.
