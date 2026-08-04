import streamlit as st

from contract_sentinel.fixtures import SHOWCASE_ASSETS
from contract_sentinel.report import render_markdown
from contract_sentinel.risk import Change, assess_change


st.set_page_config(page_title="ChangeGuard", page_icon="🛡️", layout="wide")
st.title("🛡️ ChangeGuard")
st.caption("DataHub-aware protection for breaking data contract changes")

with st.sidebar:
    st.header("Proposed change")
    dataset = st.text_input("Dataset", "commerce.orders")
    column = st.text_input("Column", "customer_id")
    operation = st.selectbox("Operation", ["rename", "drop", "type_change", "add"])
    target = st.text_input("New name or type", "customer_key")
    analyze = st.button("Analyze blast radius", type="primary", use_container_width=True)
    st.caption("Demo mode uses a reproducible DataHub showcase metadata fixture.")

if not analyze:
    st.info("Configure a proposed schema change and select **Analyze blast radius**.")
    st.stop()

change = Change(dataset=dataset, column=column, operation=operation, new_type=target or None)
impact = assess_change(change, SHOWCASE_ASSETS)

score_col, severity_col, assets_col = st.columns(3)
score_col.metric("Risk score", f"{impact.score}/100")
severity_col.metric("Decision", impact.severity.upper())
assets_col.metric("Affected assets", len(impact.affected_assets))

if impact.severity in {"critical", "high"}:
    st.error("Deployment blocked until the migration checklist is completed.")
else:
    st.success("Change may proceed after standard validation.")

st.subheader("Why this was flagged")
for reason in impact.reasons:
    st.write(f"- {reason}")

st.subheader("DataHub lineage blast radius")
for asset in SHOWCASE_ASSETS:
    with st.expander(f"{asset['name']} · owner: {asset['owner']}", expanded=True):
        st.code(asset["path"], language=None)
        st.write(f"Type: **{asset['kind']}** · Critical: **{asset['critical']}**")

st.subheader("Safe migration plan")
for item in impact.checklist:
    st.checkbox(item, key=item)

report = render_markdown(change, impact, SHOWCASE_ASSETS)
st.download_button(
    "Download impact report",
    report,
    file_name="changeguard-impact-report.md",
    mime="text/markdown",
)

