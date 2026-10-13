"""CoCo On/Off page: turn CoCo on or off for each surface, for the whole account or for individual users.

Each surface (Snowsight, CLI, Desktop) has a ``CORTEX_CODE_*_DAILY_EST_CREDIT_LIMIT_PER_USER`` parameter.
The app uses only ``-1`` (no limit, on) and ``0`` (blocked, off). Positive values are treated as legacy:
per-user quotas are the recommended way to control spending.

Tabs:

* **Account**: a card per surface with Disable, Enable or Remove limit, plus per-user quota guidance.
  Runs ``ALTER ACCOUNT SET <param> = 0`` or ``ALTER ACCOUNT UNSET <param>``.
* **Users**: set one user's value per surface (use the account setting, on or off), and scan every user
  for user-level overrides, with a bulk remove. Runs ``ALTER USER ... SET <param>`` or ``UNSET <param>``.

Every change opens a right-side drawer showing the SQL and runs only on Confirm.
"""

import streamlit as st
import pandas as pd
from data import (
    COCO_LIMIT_PARAMS, load_coco_limits, load_coco_user_overrides, load_user_coco_settings, load_user_names,
    mark_stale, run_statements, confirm_buttons,
)

CREDIT_LIMIT_DOCS_URL = "https://docs.snowflake.com/en/user-guide/cortex-code/credit-usage-limit"
QUOTAS_DOCS_URL = "https://docs.snowflake.com/en/user-guide/budgets/per-user-quotas"

# Per-user choices: inherit the account value, or override it with -1 (on) or 0 (off). Positive limits are
# legacy, so they aren't offered.
USER_CHOICES = {"account": "Use account setting", "on": "On", "off": "Off"}


def _state(value):
    """Classifies a limit value.

    Args:
        value: Parameter value as a float, or None/NaN when unset (the default, ``-1``).

    Returns:
        ``"enabled"`` for no limit (negative or unset), ``"disabled"`` for ``0``, ``"limited"`` for a
        positive (legacy) limit.
    """
    if value is None or pd.isna(value) or value < 0:
        return "enabled"
    return "disabled" if value == 0 else "limited"


def _describe(value):
    """Short label for a limit value, used in the overrides table.

    Args:
        value: Parameter value, as accepted by ``_state``.

    Returns:
        ``"No limit"``, ``"Blocked (0)"`` or, for example, ``"25 est. credits/day (legacy)"``.
    """
    state = _state(value)
    if state == "enabled":
        return "No limit"
    if state == "disabled":
        return "Blocked (0)"
    return f"{value:g} est. credits/day (legacy)"


def _on_off(value):
    """Coloured On, Off or legacy-limit label for a limit value, used on the Users tab.

    Args:
        value: Parameter value, as accepted by ``_state``.

    Returns:
        Streamlit Markdown, e.g. ``":green[**On**]"``.
    """
    state = _state(value)
    if state == "enabled":
        return ":green[**On**]"
    if state == "disabled":
        return ":red[**Off**]"
    return f":orange[**Limited**] · {value:g} est. credits/day :orange-badge[Legacy]"


def _ident(name):
    """Quotes a name as a double-quoted SQL identifier.

    Args:
        name: User name.

    Returns:
        The quoted identifier, with any double quotes doubled.
    """
    return '"' + str(name).replace('"', '""') + '"'


def _run(statements):
    """Runs account-level SQL, refreshes the limit cache, then reruns the app.

    On failure, shows each error and returns without rerunning.

    Args:
        statements: SQL statements to run, one after another.
    """
    errors = run_statements(statements)
    for s, err in errors:
        st.error(f"Failed: {s}\n\n{err}")
    if errors:
        return
    load_coco_limits.clear()
    mark_stale()
    st.rerun(scope="app")


@st.dialog("Turn CoCo on or off", width="large", position="right", icon=":material/terminal:")
def _confirm(surface, param, action):
    """Drawer for changing one surface at the account level.

    Args:
        surface: ``"Snowsight"``, ``"CLI"`` or ``"Desktop"``.
        param: The surface's credit limit parameter.
        action: ``"disable"`` sets it to ``0``. ``"enable"`` and ``"remove_limit"`` unset it, restoring
            the default (no limit); ``"remove_limit"`` also warns about losing a spending cap.
    """
    if action == "disable":
        sql = f"ALTER ACCOUNT SET {param} = 0"
        st.markdown(f"Blocks **CoCo {surface}** for every user in the account.")
    else:
        sql = f"ALTER ACCOUNT UNSET {param}"
        if action == "remove_limit":
            st.warning(
                f"This removes the daily spending cap on **CoCo {surface}**. If you need spending control, "
                "set up a per-user quota first.",
                icon=":material/warning:",
            )
        st.markdown(f"Restores the default for **CoCo {surface}**: no limit.")
    st.caption("Users with a user-level override keep their own value. Find them on the **Users** tab.")
    st.code(sql, language="sql")
    if confirm_buttons(f"confirm_{action}_{surface}"):
        _run([sql])


@st.dialog("Change CoCo for a user", width="large", position="right", icon=":material/manage_accounts:")
def _confirm_user_changes(user, changes):
    """Drawer for applying one user's pending CoCo changes from the Users tab.

    Describes each change in plain words, notes that turning CoCo on doesn't grant the Cortex roles
    a user also needs, and on Confirm runs the ALTER USER statements in parallel. Afterwards it clears
    the user's pending choices so the controls reload from the new values.

    Args:
        user: User name.
        changes: ``(surface, choice, account_value)`` tuples, where ``choice`` is ``"account"``,
            ``"on"`` or ``"off"`` and ``account_value`` is the surface's account-level value.
    """
    st.markdown(f"Changes CoCo for **{user}** only. Other users keep the account settings.")
    statements = []
    for surface, choice, account_value in changes:
        param = COCO_LIMIT_PARAMS[surface]
        account_on = _state(account_value) != "disabled"
        if choice == "account":
            statements.append(f"ALTER USER {_ident(user)} UNSET {param}")
            st.markdown(f"- **{surface}**: use the account setting ({'on' if account_on else 'off'}).")
        elif choice == "on":
            statements.append(f"ALTER USER {_ident(user)} SET {param} = -1")
            st.markdown(f"- **{surface}**: on, with no limit"
                        + (", **even though it's off for the account**." if not account_on else "."))
        else:
            statements.append(f"ALTER USER {_ident(user)} SET {param} = 0")
            st.markdown(f"- **{surface}**: off" + (", even though it's on for the account." if account_on else "."))
    if any(choice == "on" for _, choice, _ in changes):
        st.info(
            "Turning CoCo on here doesn't grant access by itself: the user's roles also need `CORTEX_USER` or "
            "`CORTEX_AGENT_USER` (see **Feature Access**).",
            icon=":material/info:",
        )
    st.code(";\n".join(statements) + ";", language="sql")
    if confirm_buttons("confirm_user_changes"):
        # Each statement changes a different parameter, so they can run concurrently
        failed = run_statements(statements, parallel=True)
        load_user_coco_settings.clear()
        load_coco_user_overrides.clear()
        if failed:
            st.error(f"{len(failed)} of {len(statements)} statement(s) failed. The others were applied.")
            for s, e in failed:
                st.code(f"{s}\n  {e}", language="text")
            return
        # Drop the pending choices so the controls reload from the new values
        for surface in COCO_LIMIT_PARAMS:
            st.session_state.pop(f"coco_user_{user}_{surface}", None)
        mark_stale()
        st.rerun(scope="app")


@st.dialog("Remove user-level overrides", width="large", position="right", icon=":material/person_remove:")
def _confirm_unset_overrides(statements, n_exempt, n_blocked):
    """Drawer for removing every user-level override found by the scan.

    Warns when users lose a ``-1`` exemption or regain access from a ``0`` block. Runs the statements in
    parallel on Confirm.

    Args:
        statements: ``ALTER USER ... UNSET <param>`` statements, one per override.
        n_exempt: Number of overrides set to ``-1`` (no limit).
        n_blocked: Number of overrides set to ``0`` (blocked).
    """
    st.markdown(f"Removes **{len(statements)}** user-level override(s), so those users get the account values.")
    if n_exempt:
        st.warning(
            f"{n_exempt} override(s) are `-1` (no limit). Those users lose their exemption and become subject "
            "to the account limit or block.",
            icon=":material/warning:",
        )
    if n_blocked:
        st.info(
            f"{n_blocked} override(s) are `0` (blocked). Those users regain access wherever the account allows it.",
            icon=":material/info:",
        )
    st.code(";\n".join(statements) + ";", language="sql")
    if confirm_buttons("confirm_unset_overrides"):
        # Each override is independent, so run them concurrently
        failed = run_statements(statements, parallel=True)
        load_coco_user_overrides.clear()
        load_user_coco_settings.clear()
        if failed:
            st.error(f"{len(failed)} of {len(statements)} statement(s) failed. The others were applied.")
            for s, e in failed:
                st.code(f"{s}\n  {e}", language="text")
            return
        mark_stale()
        st.rerun(scope="app")


limits = load_coco_limits()
# Surfaces whose parameter exists in this account
available = limits[limits["default"].notna()]
account_values = dict(zip(limits["surface"], limits["value"]))

st.title("CoCo On/Off")
st.caption("Turn CoCo on or off for each surface, for the whole account or for individual users.")

st.info(
    "**Use these parameters to turn a CoCo surface on or off. Use per-user quotas to control spending.**\n\n"
    "- `-1` (the default) means no limit, and `0` blocks a surface. Unsetting it restores the default.\n"
    "- **Any other value is legacy.** Per-user quotas are the best practice for controlling spend. Positive "
    "limits still work, but they're estimated, use a rolling 24-hour window, cover CoCo only and send no "
    "notifications (see below).",
    icon=":material/lightbulb:",
)

tab_account, tab_users = st.tabs([":material/domain: Account", ":material/group: Users"])

# ── Account ──────────────────────────────────────────────────────────
with tab_account:
    for col, row in zip(st.columns(len(limits)), limits.itertuples()):
        with col, st.container(border=True):
            st.markdown(f"##### CoCo {row.surface}")
            if pd.isna(row.default):
                st.caption(f"`{row.param}` isn't available in this account.")
                continue

            state = _state(row.value)
            if state == "enabled":
                st.markdown(":green[:material/check_circle: **Enabled**] · no limit")
            elif state == "disabled":
                st.markdown(":red[:material/block: **Disabled**]")
            else:
                st.markdown(f":orange[:material/speed: **Limited**] · {row.value:g} est. credits/day "
                            ":orange-badge[Legacy]")
            st.caption(f"`{row.param}` · " + ("set on the account" if row.level else "default"))

            if state == "limited":
                st.caption(":material/lightbulb: Positive limits are legacy. Use a per-user quota to control spend.")
            with st.container(horizontal=True):
                if state != "disabled":
                    st.button(":material/block: Disable", key=f"disable_{row.surface}",
                              on_click=_confirm, args=(row.surface, row.param, "disable"))
                if state == "disabled":
                    st.button(":material/check_circle: Enable", key=f"enable_{row.surface}",
                              on_click=_confirm, args=(row.surface, row.param, "enable"))
                elif state == "limited":
                    st.button(":material/all_inclusive: Remove limit", key=f"remove_{row.surface}",
                              on_click=_confirm, args=(row.surface, row.param, "remove_limit"))

    st.caption(
        "Turning CoCo off doesn't affect Snowflake CoWork or Cortex Agents. Users also need `CORTEX_USER` or "
        "`CORTEX_AGENT_USER` to use CoCo (see Feature Access)."
    )

    st.subheader("Spending control with per-user quotas")
    st.markdown(
        "Per-user quotas are Snowflake objects built for controlling each user's spend:\n\n"
        "- **Daily, weekly or monthly limits**, reset at 00:00 UTC.\n"
        "- **Scope by user tag**, such as team or cost center, or apply to all users with exclusions.\n"
        "- **Automatic blocking** when a user reaches the limit, released when the cycle resets.\n"
        "- **Notifications** on actual or projected spend.\n"
        "- **One quota covers CoCo on all three surfaces**, and can also cover AI functions, Cortex Agents "
        "and Snowflake CoWork.\n\n"
        f"To set one up, see [Per-user quotas]({QUOTAS_DOCS_URL}). For the parameters on this page, see "
        f"[Daily credit usage limits for CoCo]({CREDIT_LIMIT_DOCS_URL})."
    )

# ── Users ────────────────────────────────────────────────────────────
with tab_users:
    st.markdown(
        "Give individual users an exception to the account settings, for example to block CoCo for one user, or "
        "to keep it on for a few users while it's off for the account. A user-level value replaces the account "
        "value for that user.\n\n"
        "Use this for exceptions. To control CoCo for groups of users, use Cortex roles (**Feature Access**); to "
        "control spending, use per-user quotas."
    )

    st.subheader("Edit a user")
    user = st.selectbox("User", load_user_names(), index=None, placeholder="Choose a user", key="coco_user")
    if user:
        overrides_for_user, err = load_user_coco_settings(user)
        if err:
            st.error(f"Couldn't read CoCo settings for {user}: {err}")
        else:
            changes = []
            for col, row in zip(st.columns(len(available)), available.itertuples()):
                with col, st.container(border=True):
                    st.markdown(f"##### CoCo {row.surface}")
                    has_override = row.surface in overrides_for_user
                    user_value = overrides_for_user.get(row.surface)
                    effective = user_value if has_override else row.value
                    st.markdown(f"Account: {_on_off(row.value)}")
                    st.markdown(f"This user: {_on_off(effective)} · "
                                + ("user override" if has_override else "account setting"))

                    if not has_override:
                        current = "account"
                    elif _state(user_value) == "limited":
                        # A legacy positive limit matches none of the choices
                        current = None
                        st.caption("This user has a legacy limit. Choose an option to replace it.")
                    else:
                        current = "on" if _state(user_value) == "enabled" else "off"

                    choice = st.segmented_control(
                        f"CoCo {row.surface} for {user}",
                        list(USER_CHOICES),
                        format_func=USER_CHOICES.get,
                        default=current,
                        key=f"coco_user_{user}_{row.surface}",
                        label_visibility="collapsed",
                    )
                    if choice is not None and choice != current:
                        changes.append((row.surface, choice, row.value))

            with st.container(horizontal=True):
                if st.button(f":material/check: Apply ({len(changes)} change{'s' if len(changes) != 1 else ''})",
                             type="primary", key="coco_user_apply", disabled=not changes):
                    _confirm_user_changes(user, changes)
                if st.button(":material/undo: Reset", key="coco_user_reset", disabled=not changes):
                    for surface in COCO_LIMIT_PARAMS:
                        st.session_state.pop(f"coco_user_{user}_{surface}", None)
                    st.rerun()

    # ── All user-level overrides ─────────────────────────────────────
    st.subheader("All user-level overrides")
    st.caption(
        "Snowflake has no account-wide view of user-level values, so scanning runs one query per user. "
        "It can take a while on large accounts."
    )

    if st.button(":material/person_search: Scan users", key="coco_scan"):
        st.session_state["coco_scanned"] = True

    if st.session_state.get("coco_scanned"):
        with st.spinner("Checking each user's CoCo limits..."):
            overrides, errors = load_coco_user_overrides()

        if overrides.empty:
            st.success("No user-level overrides. Every user gets the account values.",
                       icon=":material/check_circle:")
        else:
            with st.container(horizontal=True):
                for surface in COCO_LIMIT_PARAMS:
                    st.metric(f"{surface} overrides", int((overrides["surface"] == surface).sum()), border=True)

            view = pd.DataFrame({
                "User": overrides["user"],
                "Surface": overrides["surface"],
                "User value": overrides["value"].map(_describe),
                "Account value": overrides["surface"].map(lambda s: _describe(account_values.get(s))),
            })
            st.dataframe(view, hide_index=True, width="stretch")
            n_legacy = int((overrides["value"] > 0).sum())
            if n_legacy:
                st.warning(
                    f"{n_legacy} override(s) use a positive limit, which is legacy. Use per-user quotas to control "
                    "these users' spend, then remove the overrides.",
                    icon=":material/warning:",
                )

            st.markdown("To remove these overrides so the account values apply:")
            unset_sql = [f"ALTER USER {_ident(u)} UNSET {COCO_LIMIT_PARAMS[s]}"
                         for u, s in zip(overrides["user"], overrides["surface"])]
            st.code(";\n".join(unset_sql) + ";", language="sql")
            st.button(
                ":material/person_remove: Remove these overrides",
                key="unset_overrides",
                on_click=_confirm_unset_overrides,
                args=(unset_sql,
                      int((overrides["value"] < 0).sum()),
                      int((overrides["value"] == 0).sum())),
            )

        if not errors.empty:
            st.warning(f"Couldn't check {len(errors)} user(s).", icon=":material/warning:")
            st.dataframe(errors.rename(columns={"user": "User", "error": "Error"}), hide_index=True,
                         width="stretch")
