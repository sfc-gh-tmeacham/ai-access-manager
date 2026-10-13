"""Model Access page: which models each role can call, through the model application roles.

Two tabs:

* **All grants** (read-only): every direct model grant in the account, with search, grantee, lifecycle
  and grantee-type filters, and a models-per-grantee summary.
* **Edit a role**: one role's current model grants, with filters, and a multiselect to choose the models
  it should have. The page compares the selection with the current grants, shows the GRANT and REVOKE
  plan, and runs it in parallel on Apply. It can optionally also grant ``CORTEX_USER`` and
  ``USE AI FUNCTIONS``.

Only direct grants are shown. Access inherited through other roles is checked on Verify Role.
"""

import streamlit as st
import pandas as pd
from data import (
    load_all, load_current_grants, load_public_grants, load_role_model_access, mark_stale,
    app_role, run_statements, cross_region_routing, model_availability, per_model_grants_tip,
    ALL_MODEL, STATUS_EMOJI, STATUS_ORDER,
)

d = load_all()
current_grants = d["current_grants"]
available_models = d["available_models"]
existing_roles = d["existing_roles"]
status_map = d["status_map"]
eol_map = d["eol_map"]
all_is_public = d["all_is_public"]
routing = cross_region_routing(d)
# ALL isn't a model, so it gets its own status, sorted first
STATUS_SORT = {"ALL MODELS": 0, **STATUS_ORDER}


def _lifecycle(model):
    """Returns a model's lifecycle status for sorting and filtering.

    Args:
        model: Model name.

    Returns:
        ``"ALL MODELS"`` for ``ALL``, the model's status (e.g. ``"GA"``), or ``"No status"``.
    """
    if model.upper() == ALL_MODEL:
        return "ALL MODELS"
    return status_map.get(model.upper(), "No status")


def _status_label(s):
    """Formats a lifecycle status with its emoji, for display.

    Args:
        s: Status from ``_lifecycle``.

    Returns:
        For example ``"✅ GA"`` or ``"No status"``.
    """
    emoji = "\U0001F310" if s == "ALL MODELS" else STATUS_EMOJI.get(s, "")
    return f"{emoji} {s}".strip()


def _sort_key(col):
    """Sort key for ``DataFrame.sort_values``: lifecycle columns by ``STATUS_SORT``, other columns as is.

    Args:
        col: The column pandas is sorting by.

    Returns:
        The column mapped to sort ranks if it's the ``_ls`` lifecycle column, otherwise unchanged.
    """
    return col.map(lambda s: STATUS_SORT.get(s, 99)) if col.name == "_ls" else col


def _edit_role():
    """Renders the Edit a role tab: a role's current model grants, and the controls to change them.

    Changes run only after the user reviews the plan and clicks Apply Changes.
    """
    st.markdown(
        "Choose a role and the models it should be able to use. The app compares your selection with the role's "
        "current grants, shows the grant and revoke plan, then lets you apply it."
    )
    selected_role = st.selectbox("Select role to manage", sorted(existing_roles), key="ma_role")
    if not selected_role:
        return

    role_current = set(
        current_grants[
            (current_grants["granted_to"] == "ROLE")
            & (current_grants["grantee"].str.upper() == selected_role.upper())
        ]["model"]
    )

    # ── Current access ───────────────────────────────────────────────
    st.subheader("Current access")
    if role_current:
        current_df = pd.DataFrame({"Model": sorted(role_current)})
        current_df["_ls"] = current_df["Model"].map(_lifecycle)
        current_df["Lifecycle"] = current_df["_ls"].map(_status_label)
        current_df["Available here"] = current_df["Model"].map(
            lambda m: "\u2014" if m == ALL_MODEL else model_availability(m, d, routing))
        current_df["EOL Date"] = current_df["Model"].map(lambda m: eol_map.get(m, "\u2014"))
        # ALL first, then lifecycle order, then name, as on the Model Catalog
        current_df = current_df.sort_values(["_ls", "Model"], key=_sort_key).reset_index(drop=True)

        col_search, col_status = st.columns([1, 2])
        with col_search:
            search = st.text_input("Search models", placeholder="e.g. llama, claude...", key="ma_search")
        with col_status:
            # Every status the role holds is selected by default, so no grant is hidden until you filter
            statuses = sorted(current_df["_ls"].unique(), key=lambda s: STATUS_SORT.get(s, 99))
            selected_statuses = st.multiselect("Filter by lifecycle", statuses, default=statuses,
                                               key=f"ma_status_{selected_role}")
        only_available = st.toggle(
            "Only models available to this account",
            key="ma_available",
            help=f"In region in {d['account_region']}, or reachable with the current cross-region setting "
                 f"({d['cross_region_value']}).",
        )

        shown = current_df
        if search:
            shown = shown[shown["Model"].str.contains(search, case=False, regex=False, na=False)]
        if selected_statuses:
            shown = shown[shown["_ls"].isin(selected_statuses)]
        if only_available:
            shown = shown[shown["Available here"] != "Not available"]

        st.metric("Showing", f"{len(shown)} / {len(current_df)} models", border=True)
        st.dataframe(shown.drop(columns="_ls"), width="stretch", hide_index=True)
    else:
        st.caption(f"**{selected_role}** has no direct model grants.")

    public_models = set(
        current_grants[(current_grants["granted_to"] == "ROLE") & (current_grants["grantee"] == "PUBLIC")]["model"]
    )
    is_public_role = selected_role.upper() == "PUBLIC"
    if all_is_public and not is_public_role:
        st.info(
            "`CORTEX-MODEL-ROLE-ALL` is on PUBLIC, so this role can also use every model through PUBLIC.",
            icon=":material/info:",
        )
    elif public_models and not is_public_role:
        st.info(
            f"This role can also use {len(public_models)} model(s) granted to PUBLIC: "
            + ", ".join(f"`{m}`" for m in sorted(public_models)),
            icon=":material/info:",
        )
    st.caption(
        "Shows direct grants to this role. Access inherited from other roles granted to it isn't shown here; "
        "use **Verify Role** to check effective access."
    )

    # ── Change access ────────────────────────────────────────────────
    st.subheader("Change access")
    # ALL first, then GA/PUPR and models with no lifecycle status (assumed available, as on the Model Catalog).
    # Models the role already holds are always listed, so they aren't revoked just for being filtered out.
    def _offered(m):
        """Whether to offer a model in the multiselect.

        Offers ALL, GA, PUPR and models without a status, plus any model the role already holds, so a held
        model isn't revoked just because it's filtered out of the options.

        Args:
            m: Model name.

        Returns:
            True if the model should be listed.
        """
        return m in role_current or m not in status_map or status_map[m] in ("GA", "PUPR")

    model_options = sorted(
        (m for m in available_models if _offered(m)),
        key=lambda m: (m != ALL_MODEL, m),
    )

    desired = st.multiselect(
        f"Models for **{selected_role}**",
        options=model_options,
        default=[m for m in model_options if m in role_current],
        help=f"Select the models this role should be able to use. {ALL_MODEL} grants every model, current and future.",
    )

    desired_set = {m.upper() for m in desired}
    to_grant = sorted(desired_set - role_current)
    to_revoke = sorted(role_current - desired_set)

    if not (to_grant or to_revoke):
        st.success("Grants already match your selection \u2014 nothing to change.")
        return

    st.subheader("Change Plan")
    plan_rows = []
    for m in to_grant:
        plan_rows.append({"Action": "GRANT", "Model": m,
            "SQL": f'GRANT APPLICATION ROLE {app_role(m)} TO ROLE "{selected_role.upper()}"'})
    for m in to_revoke:
        plan_rows.append({"Action": "REVOKE", "Model": m,
            "SQL": f'REVOKE APPLICATION ROLE {app_role(m)} FROM ROLE "{selected_role.upper()}"'})
    plan_df = pd.DataFrame(plan_rows)
    st.dataframe(plan_df, width="stretch", hide_index=True)

    all_sql = plan_df["SQL"].tolist()
    if st.checkbox("Also grant SNOWFLAKE.CORTEX_USER", value=False):
        all_sql.append(f'GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE "{selected_role.upper()}"')
    if st.checkbox("Also grant USE AI FUNCTIONS", value=False):
        all_sql.append(f'GRANT USE AI FUNCTIONS ON ACCOUNT TO ROLE "{selected_role.upper()}"')

    with st.form("apply_form"):
        st.warning(f"This will execute {len(all_sql)} statement(s). Review the plan above carefully.")
        apply = st.form_submit_button(":material/play_arrow: Apply Changes", type="primary")

    if apply:
        # Each statement grants or revokes a different model or privilege, so they can run concurrently
        errors = run_statements(all_sql, parallel=True)
        ok_count = len(all_sql) - len(errors)
        if not errors:
            st.success(f"All {ok_count} statement(s) applied successfully.")
        else:
            st.error(f"{ok_count}/{len(all_sql)} succeeded.")
            for sql, err in errors:
                st.code(f"ERROR: {err}\n  {sql}", language="text")
        load_current_grants.clear()
        load_public_grants.clear()
        load_role_model_access.clear()
        mark_stale()


def _all_grants():
    """Renders the All grants tab: every direct model grant in the account, with filters and a summary."""
    st.markdown("Every direct model grant in the account, for all grantees. Read-only: edit a role in the other tab.")
    if current_grants.empty:
        st.info("No model grants found.")
        return

    columns = {"model": "Model", "granted_to": "Granted to", "grantee": "Grantee"}
    grants = current_grants.copy()
    grants["_ls"] = grants["model"].map(_lifecycle)
    grants["eol"] = grants["model"].map(lambda m: eol_map.get(m.upper(), ""))
    grants = grants.sort_values(["_ls", "model", "grantee"], key=_sort_key)

    # ── Filters ──────────────────────────────────────────────────────
    col_search, col_grantee = st.columns([1, 2])
    with col_search:
        search = st.text_input("Search models", placeholder="e.g. claude, llama...", key="mg_search")
    with col_grantee:
        grantee_filter = st.multiselect("Grantee", sorted(grants["grantee"].unique()),
                                        placeholder="All grantees", key="mg_grantee")

    # Pills only appear when there's more than one value to choose between
    statuses = sorted(grants["_ls"].unique(), key=lambda s: STATUS_SORT.get(s, 99))
    status_filter = []
    if len(statuses) > 1:
        status_filter = st.pills(
            "Lifecycle", statuses, selection_mode="multi", format_func=_status_label,
            key="mg_lifecycle", help="Show only these statuses. Select none to show all.",
        )
    types = sorted(grants["granted_to"].unique())
    type_filter = []
    if len(types) > 1:
        type_filter = st.pills(
            "Grantee type", types, selection_mode="multi", key="mg_type",
            format_func=lambda t: t.replace("_", " ").title(),
            help="Account roles, application roles, database roles or applications. Select none to show all.",
        )

    shown = grants
    if search:
        shown = shown[shown["model"].str.contains(search, case=False, regex=False, na=False)]
    if grantee_filter:
        shown = shown[shown["grantee"].isin(grantee_filter)]
    if status_filter:
        shown = shown[shown["_ls"].isin(status_filter)]
    if type_filter:
        shown = shown[shown["granted_to"].isin(type_filter)]

    with st.container(horizontal=True):
        st.metric("Grants", f"{len(shown)} / {len(grants)}", border=True)
        st.metric("Models", shown["model"].nunique(), border=True)
        st.metric("Grantees", shown["grantee"].nunique(), border=True)

    table = shown.assign(lifecycle=shown["_ls"].map(_status_label))
    st.dataframe(
        table[["model", "lifecycle", "eol", "granted_to", "grantee"]].rename(
            columns={**columns, "lifecycle": "Lifecycle", "eol": "End-of-life date"}),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Models per grantee")
    if shown.empty:
        st.caption("No grants match these filters.")
        return
    summary = (
        shown.groupby(["granted_to", "grantee"])["model"]
        .agg(model_count="nunique", models=lambda s: ", ".join(sorted(set(s))))
        .reset_index()
        .sort_values("model_count", ascending=False)
        .rename(columns={**columns, "model_count": "Models", "models": "Model list"})
    )
    st.dataframe(summary, width="stretch", hide_index=True)


st.title("Model Access")
st.markdown(
    "Which models each role can call, through the **model application roles** (`CORTEX-MODEL-ROLE-<model>`). "
    "`ALL` is `CORTEX-MODEL-ROLE-ALL`: every model, current and future."
)
st.caption(
    "Direct grants only. Model access is paired with a Cortex role, which decides whether a role can use a Cortex "
    "feature at all (see **Feature Access**). For access inherited through other roles, see **Verify Role**."
)
per_model_grants_tip()

tab_all, tab_edit = st.tabs([":material/group: All grants", ":material/edit: Edit a role"])
with tab_all:
    _all_grants()
with tab_edit:
    _edit_role()
