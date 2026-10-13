"""Verify Role page: which models a role can actually use.

For each model, asks ``EXPLAIN_PRIVILEGES`` whether the role could run ``DESCRIBE MODEL`` on it (see
``data.load_role_model_access``). That covers roles granted to it and PUBLIC, and works without
activating the role, so it can check any role.

Shows can-use and can't-use counts, a filterable table with the missing privileges, and a note when
``CORTEX_MODELS_ALLOWLIST`` would still allow models marked No. Checks model access only, not Cortex
features.

Read-only: this page runs no GRANT, REVOKE or ALTER statements.
"""

import streamlit as st
import pandas as pd
from data import load_all, load_role_model_access, ALL_MODEL, STATUS_EMOJI, STATUS_ORDER

d = load_all()
existing_roles = d["existing_roles"]
status_map = d["status_map"]
allowlist_value = d["allowlist_value"]

st.title("Verify Role")
st.caption(
    "Checks which models a role can use from its grants, including roles granted to it and PUBLIC. "
    "The role isn't activated, so this works for any role, including ones not in this app's role hierarchy."
)

test_role = st.selectbox("Role to test", sorted(existing_roles), index=None, placeholder="Choose a role")

if test_role and st.button(":material/play_arrow: Check model access", type="primary"):
    st.session_state["verify_role"] = test_role

checked = st.session_state.get("verify_role")
if checked and checked == test_role:
    models = tuple(sorted(m for m in d["available_models"] if m.upper() != ALL_MODEL))
    with st.spinner(f"Checking {len(models)} models for {checked}..."):
        access = load_role_model_access(checked, models)

    can, cannot, errored = (access["can_use"] == True), (access["can_use"] == False), access["can_use"].isna()
    with st.container(horizontal=True):
        st.metric("Can use", int(can.sum()), border=True)
        st.metric("Can't use", int(cannot.sum()), border=True)
        if errored.any():
            st.metric("Couldn't check", int(errored.sum()), border=True)

    if d["all_is_public"]:
        st.info(
            "`CORTEX-MODEL-ROLE-ALL` is on PUBLIC, so every role can use every model.",
            icon=":material/info:",
        )

    access["Lifecycle"] = access["model"].map(
        lambda m: f"{STATUS_EMOJI.get(status_map.get(m), '')} {status_map.get(m, 'No status')}".strip()
    )
    access["_sort"] = access["model"].map(lambda m: STATUS_ORDER.get(status_map.get(m), 99))
    access["Can use"] = access["can_use"].map({True: "Yes", False: "No"}).fillna("Couldn't check")

    show = st.segmented_control(
        "Show", ["All", "Can use", "Can't use"], default="All", key="verify_filter",
    )
    view = access
    if show == "Can use":
        view = access[can]
    elif show == "Can't use":
        view = access[~can]

    st.dataframe(
        view.sort_values(["_sort", "model"])[["model", "Lifecycle", "Can use", "detail"]]
            .rename(columns={"model": "Model", "detail": "Missing / error"}),
        width="stretch",
        hide_index=True,
        column_config={"Missing / error": st.column_config.TextColumn(width="large")},
    )

    _allow = str(allowlist_value).upper()
    if _allow == "ALL":
        allowlist_note = " `CORTEX_MODELS_ALLOWLIST` is `ALL`, so calls are allowed even for models marked No."
    elif _allow not in ("NONE", "N/A", ""):
        allowlist_note = (f" `CORTEX_MODELS_ALLOWLIST` is `{allowlist_value}`, so those models are allowed "
                          "even where marked No.")
    else:
        allowlist_note = ""
    st.caption(
        "Based on grants only, using `EXPLAIN_PRIVILEGES` for `DESCRIBE MODEL` on each model. "
        "Feature policies can still block a call." + allowlist_note
    )
