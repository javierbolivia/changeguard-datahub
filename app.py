"""ChangeGuard — DataHub-Aware Schema Change Protection Agent.

A Streamlit application that demonstrates the ChangeGuard autonomous agent
evaluating schema change risk by tracing downstream lineage in DataHub.
"""

import asyncio
import functools
import time

import streamlit as st

from contract_sentinel.agent import (
    AgentResult,
    AgentStep,
    ChangeGuardAgent,
    StepStatus,
)
from contract_sentinel.datahub_writeback import write_decision_to_datahub
from contract_sentinel.mcp_connection import create_live_adapter
from contract_sentinel.risk import Change


# ─── Page Configuration ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="ChangeGuard Agent",
    page_icon="\U0001f6e1\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom Styling ───────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .stApp { max-width: 1200px; margin: 0 auto; }
    .step-box {
        border: 1px solid #333;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
        background: #1a1a2e;
    }
    .step-running { border-left: 4px solid #f39c12; }
    .step-success { border-left: 4px solid #27ae60; }
    .step-failed { border-left: 4px solid #e74c3c; }
    .step-skipped { border-left: 4px solid #95a5a6; }
    .agent-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 20px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .metric-card {
        background: #16213e;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Header ───────────────────────────────────────────────────────────────────

col_logo, col_title = st.columns([1, 5])
with col_logo:
    st.markdown("# \U0001f6e1\ufe0f")
with col_title:
    st.title("ChangeGuard")
    st.caption(
        "Autonomous agent that protects data contracts by tracing downstream "
        "impact through DataHub lineage before deployment."
    )

st.divider()

# ─── Sidebar: Change Configuration ───────────────────────────────────────────

# Session-state defaults for the form fields, so the example buttons below
# can pre-fill them without triggering Run Agent.
if "cg_dataset" not in st.session_state:
    st.session_state.cg_dataset = "commerce.orders"
if "cg_column" not in st.session_state:
    st.session_state.cg_column = "customer_id"
if "cg_operation" not in st.session_state:
    st.session_state.cg_operation = "rename"
if "cg_new_value" not in st.session_state:
    st.session_state.cg_new_value = "cust_key"


def _load_safe_rename_example() -> None:
    st.session_state.cg_dataset = "commerce.orders"
    st.session_state.cg_column = "customer_id"
    st.session_state.cg_operation = "rename"
    st.session_state.cg_new_value = "cust_key"


def _load_dangerous_drop_example() -> None:
    st.session_state.cg_dataset = "commerce.orders"
    st.session_state.cg_column = "customer_id"
    st.session_state.cg_operation = "drop"
    st.session_state.cg_new_value = ""


with st.sidebar:
    st.header("\U0001f527 Proposed Schema Change")

    st.markdown("**Quick examples:**")
    ex_col1, ex_col2 = st.columns(2)
    with ex_col1:
        st.button(
            "\u25b6 Safe Rename",
            use_container_width=True,
            on_click=_load_safe_rename_example,
            help="Fills the form with a low-risk rename example. Does not run the agent.",
        )
    with ex_col2:
        st.button(
            "\U0001f6d1 Dangerous Drop",
            use_container_width=True,
            on_click=_load_dangerous_drop_example,
            help="Fills the form with a high-risk drop example. Does not run the agent.",
        )

    st.markdown("Configure the change you want to evaluate:")

    dataset = st.text_input(
        "Dataset",
        key="cg_dataset",
        help="Fully qualified dataset name (schema.table)",
    )
    column = st.text_input(
        "Column",
        key="cg_column",
        help="The column being modified",
    )
    operation = st.selectbox(
        "Operation",
        ["rename", "drop", "type_change", "add"],
        key="cg_operation",
        help="Type of schema change",
    )
    new_value = st.text_input(
        "New name / type (optional)",
        key="cg_new_value",
        help="For rename: new column name. For type_change: new data type.",
    )

    st.divider()

    st.header("\u2699\ufe0f Agent Settings")
    mode = st.radio(
        "Data Source",
        ["Demo (fixtures)", "Live (DataHub MCP)"],
        index=0,
        help="Demo uses reproducible sample data. Live connects to DataHub.",
    )
    confirm_wb = st.checkbox(
        "Persist decision to DataHub",
        value=False,
        help=(
            "If checked and in Live mode, writes the ChangeGuard decision "
            "(ALLOW/BLOCK, score, severity, operation, column, timestamp) "
            "as custom properties on the dataset in DataHub."
        ),
    )

    datahub_url = "http://localhost:8080"
    datahub_token = ""
    if mode == "Live (DataHub MCP)":
        st.caption("Live mode connects to a real DataHub instance via MCP.")
        datahub_url = st.text_input("DataHub GMS URL", value="http://localhost:8080")
        datahub_token = st.text_input(
            "DataHub Access Token (optional)", value="", type="password"
        )

    st.divider()
    analyze = st.button(
        "\U0001f680 Run Agent",
        type="primary",
        use_container_width=True,
    )

    st.divider()
    st.markdown(
        "**Built for** [Build with DataHub: The Agent Hackathon]"
        "(https://datahub.devpost.com/)  \n"
        "**Category:** Agents That Do Real Work"
    )


# ─── Main Content ────────────────────────────────────────────────────────────

if not analyze:
    # Landing state
    st.info(
        "\U0001f449 Configure a proposed schema change in the sidebar "
        "and click **Run Agent** to see ChangeGuard in action."
    )

    # Show architecture overview
    with st.expander("\U0001f3d7\ufe0f How ChangeGuard Works", expanded=True):
        st.markdown(
            """
**ChangeGuard is an autonomous pre-deployment agent.** When a data engineer
proposes a schema change (rename, drop, type change), the agent:

1. **Parses** the proposed change and validates it
2. **Resolves** the dataset URN in DataHub's metadata catalog
3. **Traces** column-level downstream lineage (dashboards, datasets, ML models)
4. **Scores** risk using transparent, reproducible rules (no LLM black box)
5. **Generates** a structured impact report with migration checklist
6. **Writes back** the decision to DataHub so the team inherits context
7. **Decides** to BLOCK or ALLOW the deployment

The agent uses DataHub's MCP Server tools:
- `search` — find datasets by name
- `get_lineage` — column-level downstream traversal
- `get_entities` — asset metadata and ownership
- `save_document` — write impact reports back to DataHub
"""
        )

    with st.expander("\U0001f4ca Risk Scoring Transparency"):
        st.markdown(
            """
| Factor | Points |
|--------|--------|
| `drop` operation | 55 |
| `rename` operation | 45 |
| `type_change` operation | 35 |
| `add` operation | 5 |
| Per downstream asset (max 25) | +5 each |
| Per critical asset (max 20) | +10 each |
| Per dashboard (max 10) | +5 each |

**Severity thresholds:** Critical ≥80, High ≥60, Medium ≥30, Low <30

**Deployment decision:** Critical/High → BLOCK. Medium/Low → ALLOW
(change may proceed, but review the reasons and checklist — Medium
severity is not risk-free, it is simply not blocked).
"""
        )
    st.stop()


# ─── Agent Execution ─────────────────────────────────────────────────────────

change = Change(
    dataset=dataset,
    column=column,
    operation=operation,
    new_type=new_value or None,
)

# Create placeholders for real-time step updates
st.subheader("\U0001f916 Agent Execution")

step_container = st.container()
steps_placeholders: list = []

STEP_ICONS = {
    StepStatus.PENDING: "\u23f3",
    StepStatus.RUNNING: "\U0001f504",
    StepStatus.SUCCESS: "\u2705",
    StepStatus.FAILED: "\u274c",
    StepStatus.SKIPPED: "\u23ed\ufe0f",
}

STEP_NAMES = {
    "parse_change": "Parse & Validate Change",
    "resolve_urn": "Resolve Dataset in DataHub",
    "fetch_lineage": "Fetch Downstream Lineage",
    "assess_risk": "Calculate Risk Score",
    "generate_report": "Generate Impact Report",
    "writeback": "Write Back to DataHub",
    "decision": "Deployment Decision",
    "persist_decision": "Persist Decision to DataHub",
}


def render_step(step: AgentStep, placeholder) -> None:
    """Render a single agent step in its placeholder."""
    icon = STEP_ICONS.get(step.status, "\u2753")
    label = STEP_NAMES.get(step.name, step.name)
    duration = f" ({step.duration_ms:.0f}ms)" if step.duration_ms > 0 else ""

    if step.status == StepStatus.SUCCESS:
        placeholder.success(f"{icon} **{label}**{duration} — {step.description}")
    elif step.status == StepStatus.FAILED:
        placeholder.error(f"{icon} **{label}**{duration} — {step.error}")
    elif step.status == StepStatus.SKIPPED:
        reason = step.result.get("reason", "") if isinstance(step.result, dict) else ""
        placeholder.warning(f"{icon} **{label}** — Skipped: {reason}")
    elif step.status == StepStatus.RUNNING:
        placeholder.info(f"{icon} **{label}** — {step.description}...")
    else:
        placeholder.markdown(f"{icon} **{label}** — Pending")


# Pre-create 8 placeholders
with step_container:
    for _ in range(8):
        steps_placeholders.append(st.empty())

# Track step index
step_idx = [0]


def on_step_update(step: AgentStep) -> None:
    """Callback to update the UI in real-time as the agent progresses."""
    idx = step_idx[0]
    if step.status == StepStatus.RUNNING:
        # New step started
        if idx < len(steps_placeholders):
            render_step(step, steps_placeholders[idx])
    elif step.status in {StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED}:
        # Step completed
        if idx < len(steps_placeholders):
            render_step(step, steps_placeholders[idx])
            step_idx[0] += 1
        time.sleep(0.3)  # Brief pause for visual effect


# Run the agent
use_live = mode == "Live (DataHub MCP)"


async def execute_agent() -> tuple[AgentResult | None, str | None]:
    """Single async flow: build the adapter (if Live) and run the agent.

    Returns (result, connection_error). If connection_error is set, the
    agent was never run and no fixtures were substituted.
    """
    mcp_adapter = None
    if use_live:
        try:
            mcp_adapter = await create_live_adapter(
                datahub_url=datahub_url or "http://localhost:8080",
                datahub_token=datahub_token or None,
            )
        except Exception as e:
            return None, str(e)

    writeback_fn = None
    if use_live:
        writeback_fn = functools.partial(
            write_decision_to_datahub,
            datahub_url or "http://localhost:8080",
        )

    try:
        agent = ChangeGuardAgent(
            mcp_adapter=mcp_adapter,
            on_step_update=on_step_update,
            writeback_fn=writeback_fn,
        )
        result = await agent.run_async(change, confirm_writeback=confirm_wb)
        return result, None
    finally:
        if mcp_adapter is not None:
            try:
                await mcp_adapter.close()
            except Exception:
                # Do not let a cleanup failure hide the agent's result or
                # any error already being propagated from this block.
                pass


result, connection_error = asyncio.run(execute_agent())

if connection_error:
    st.error(
        f"\u274c **Failed to connect to DataHub MCP server** at "
        f"`{datahub_url}`.\n\n**Error:** {connection_error}\n\n"
        f"Live mode requires a running DataHub instance and the "
        f"`mcp-server-datahub` package reachable via `uvx`. This run was "
        f"**not** completed and no demo data was substituted."
    )
    st.stop()

# ─── Results ─────────────────────────────────────────────────────────────────

st.divider()

if result.impact:
    st.subheader("\U0001f4ca Results")

    # Key metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Risk Score", f"{result.impact.score}/100")
    col2.metric("Severity", result.impact.severity.upper())
    col3.metric("Affected Assets", len(result.impact.affected_assets))
    col4.metric("Mode", result.mode.upper())

    # Decision banner
    if result.impact.severity in {"critical", "high"}:
        st.error(
            f"\U0001f6d1 **DEPLOYMENT BLOCKED** — Risk score {result.impact.score}/100. "
            f"Complete the migration checklist before proceeding."
        )
    else:
        st.success(
            f"\u2705 **CHANGE ALLOWED** — Risk score {result.impact.score}/100 "
            f"({result.impact.severity}). Not blocked, but review the reasons "
            f"and checklist below before deploying."
        )

    # Detailed sections in tabs
    tab_reasons, tab_blast, tab_checklist, tab_report = st.tabs(
        ["\U0001f50d Why Flagged", "\U0001f4a5 Blast Radius", "\u2705 Checklist", "\U0001f4c4 Full Report"]
    )

    with tab_reasons:
        for reason in result.impact.reasons:
            st.markdown(f"- {reason}")

    with tab_blast:
        for asset in result.downstream_assets:
            with st.expander(
                f"**{asset['name']}** · {asset['kind']} · Owner: {asset['owner']}",
                expanded=False,
            ):
                st.code(asset.get("path", ""), language=None)
                cols = st.columns(3)
                cols[0].write(f"**Type:** {asset['kind']}")
                cols[1].write(f"**Critical:** {'Yes' if asset.get('critical') else 'No'}")
                cols[2].write(f"**URN:** `{asset.get('urn', 'N/A')}`")

    with tab_checklist:
        st.markdown("Complete these steps before deploying:")
        for i, item in enumerate(result.impact.checklist, 1):
            st.checkbox(item, key=f"check_{i}")

    with tab_report:
        if result.report:
            st.markdown(result.report)
            st.download_button(
                "\U0001f4e5 Download Report (.md)",
                result.report,
                file_name="changeguard-impact-report.md",
                mime="text/markdown",
                use_container_width=True,
            )

    # Execution summary
    with st.expander("\u23f1\ufe0f Execution Summary"):
        total_ms = sum(s.duration_ms for s in result.steps)
        st.write(f"**Total execution time:** {total_ms:.0f}ms")
        st.write(f"**Steps completed:** {sum(1 for s in result.steps if s.status == StepStatus.SUCCESS)}/8")
        st.write(f"**Data source:** {result.mode}")
        for step in result.steps:
            icon = STEP_ICONS.get(step.status, "?")
            st.write(
                f"  {icon} {STEP_NAMES.get(step.name, step.name)}: "
                f"{step.duration_ms:.0f}ms"
            )
