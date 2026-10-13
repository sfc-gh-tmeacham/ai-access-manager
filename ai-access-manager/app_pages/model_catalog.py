"""Model Catalog page: every Cortex model with its lifecycle status, availability and grants.

A summary box at the top shows the account region and cross-region routing, which decide the
**Available here** column. The table can be filtered by name, lifecycle status, and availability to this
account. A tip warns that models with no status are very likely used by AI functions.

Read-only: this page runs no GRANT, REVOKE or ALTER statements.
"""

import streamlit as st
import pandas as pd
from data import (
    load_all, PREFIX, STATUS_ORDER, STATUS_EMOJI, ALL_MODEL, app_role, model_from_grant_name,
    cross_region_routing, model_availability,
)

d = load_all()
available_models = d["available_models"]
current_grants = d["current_grants"]
all_is_public = d["all_is_public"]
pub_models = d["pub_models"]
status_map = d["status_map"]
eol_map = d["eol_map"]
in_region_map = d["in_region_map"]
cross_region_map = d["cross_region_map"]
account_region = d["account_region"]
cross_region_value = d["cross_region_value"]
routing = cross_region_routing(d)


def availability(model):
    """Describes whether this account can reach a model, using the page's routing values.

    Args:
        model: Model name.

    Returns:
        The availability label from ``model_availability``, e.g. ``"In region"``.
    """
    return model_availability(model, d, routing)


# ── Build catalog table ──────────────────────────────────────────────

# A model reaches every role through either PUBLIC: the SNOWFLAKE.PUBLIC application role, or the
# PUBLIC account role (where this app's per-model grants go)
_pub_model_names = set(pub_models["name"].map(model_from_grant_name)) if not pub_models.empty else set()
_pub_model_names |= set(
    current_grants[(current_grants["granted_to"] == "ROLE") & (current_grants["grantee"] == "PUBLIC")]["model"]
    .str.upper()
)

model_status = []
for m in available_models:
    if m.upper() == ALL_MODEL:
        # Not a model: the role that grants every model, current and future. Pinned to the top.
        ls, emoji, sort = "ALL MODELS", "\U0001F310", 0
        available = "\u2014"
    else:
        ls = status_map.get(m, "No status")
        emoji, sort = STATUS_EMOJI.get(ls, ""), STATUS_ORDER.get(ls, 99)
        available = availability(m.upper())
    grants = current_grants[current_grants["model"] == m]
    grantees = grants["grantee"].tolist()
    is_public = all_is_public or m.upper() in _pub_model_names
    # None (rendered as empty) marks region data Snowflake doesn't report, distinct from an empty list
    has_regions = m.upper() == ALL_MODEL or m.upper() in status_map
    model_status.append({
        "Status": f"{emoji} {ls}".strip(),
        "Model": m,
        "Available here": available,
        "Publicly Accessible": is_public,
        "Grant Count": len(grants),
        "Grantees": ", ".join(sorted(set(grantees))) if grantees else "\u2014",
        "In-region": in_region_map.get(m.upper(), []) if has_regions else None,
        "Cross-region": cross_region_map.get(m.upper(), []) if has_regions else None,
        "EOL Date": eol_map.get(m, "\u2014"),
        "_sort": sort,
        "_ls": ls,
    })

status_df = pd.DataFrame(model_status).sort_values(["_sort", "Model"]).reset_index(drop=True)

# ── Region and routing ───────────────────────────────────────────────


def _badge(text, color):
    # Escape underscores so region names like AWS_US_EAST_1 aren't read as italics
    """Formats text as a coloured Streamlit Markdown badge.

    Underscores are escaped so region names like ``AWS_US_EAST_1`` aren't read as italics.

    Args:
        text: Badge text.
        color: Streamlit colour name, e.g. ``"blue"``.

    Returns:
        Markdown like ``":blue-badge[AWS\\_US]"``.
    """
    escaped = text.replace("_", r"\_")
    return f":{color}-badge[{escaped}]"


st.title("Model Catalog")
with st.container(border=True):
    c_region, c_routing, c_link = st.columns([1, 2, 1], vertical_alignment="center")
    with c_region:
        st.markdown("**Account region**")
        st.markdown(_badge(account_region, "blue"))
    with c_routing:
        st.markdown("**Cross-region inference**")
        if routing:
            st.markdown(" ".join(_badge(v, "green") for v in routing))
        else:
            st.markdown(_badge(cross_region_value, "orange") + " Only models in this account's region are available.")
    with c_link:
        st.page_link("app_pages/home.py", label="Change routing on Home", icon=":material/arrow_forward:")
    st.markdown("These settings decide the **Available here** column below.")

# ── Filters ──────────────────────────────────────────────────────────

col_search, col_status = st.columns([1, 2])
with col_search:
    search = st.text_input("Search models", placeholder="e.g. llama, claude...")
with col_status:
    all_statuses = sorted(status_df["_ls"].unique(), key=lambda s: 0 if s == "ALL MODELS" else STATUS_ORDER.get(s, 99))
    default_on = [s for s in all_statuses if s not in ("EOL", "INTERNAL")]
    selected_statuses = st.multiselect("Filter by lifecycle", all_statuses, default=default_on)

only_available = st.toggle(
    "Only models available to this account",
    help=f"In region in {account_region}, or reachable with the current cross-region setting "
         f"({cross_region_value}).",
)

filtered = status_df.copy()
if search:
    filtered = filtered[filtered["Model"].str.contains(search.upper(), na=False)]
if selected_statuses:
    filtered = filtered[filtered["_ls"].isin(selected_statuses)]
if only_available:
    filtered = filtered[filtered["Available here"] != "Not available"]

display_cols = [c for c in filtered.columns if not c.startswith("_")]
st.metric("Showing", f"{len(filtered)} / {len(status_df)} models", border=True)
if (status_df["_ls"] == "No status").any():
    st.info(
        "**Models with no status are very likely used by an AI function.** Snowflake doesn't yet "
        "programmatically publish which models each product surface can use (for example CoCo, AI functions, or "
        "Cortex Agents), so revoking one of these can break a feature without warning. Be careful when revoking "
        "model access.\n\n"
        "Model access alone doesn't let a user call a model: it must be paired with a Cortex role, such as "
        "`CORTEX_USER` or `AI_FUNCTIONS_USER`.",
        icon=":material/lightbulb:",
    )
st.dataframe(
    filtered[display_cols],
    width="stretch",
    hide_index=True,
    column_config={
        "Publicly Accessible": st.column_config.CheckboxColumn(
            help="Every role can use this model, because ALL or this model is granted to PUBLIC "
                 "(the PUBLIC account role or the SNOWFLAKE.PUBLIC application role). "
                 "Using it still requires a Cortex role.",
        ),
        "Available here": st.column_config.TextColumn(
            help=f"Based on this account's region ({account_region}) and its cross-region setting "
                 f"({cross_region_value}). Models with no lifecycle status are assumed available.",
        ),
        "In-region": st.column_config.ListColumn(
            help="Regions where the model runs natively. Empty when Snowflake doesn't report region data.",
        ),
        "Cross-region": st.column_config.ListColumn(
            help="CORTEX_ENABLED_CROSS_REGION values that can route requests to the model. "
                 "Empty when Snowflake doesn't report region data.",
        ),
    },
)
