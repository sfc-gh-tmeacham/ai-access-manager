"""Feature Access page: the Cortex roles and privileges that decide which features a role can use.

Sections:

* **How Cortex access works** (expander): how ``CORTEX_USER``, the narrower roles, ``USE AI FUNCTIONS``,
  PUBLIC and ``EXECUTE AGENT TASK`` fit together.
* **Roles and privileges**: every entry in ``CORTEX_ACCESS_ROLES`` with its description, whether it's on
  PUBLIC, and which account roles hold it directly.
* **Manage grants**: grant or revoke one role or privilege for chosen account roles, with warnings about
  PUBLIC, and optional model grants when granting AI function access.
* **Per-function AI privileges**: ``USE AI FUNCTION <name>`` grants for individual AI functions.

Changes are shown as a GRANT/REVOKE plan and run in parallel only when the user clicks Apply Changes.
ACCOUNTADMIN is excluded from the grant lists because it always has full access.
"""

import streamlit as st
import pandas as pd
from data import (
    load_all, load_cortex_role_grants, load_current_grants, load_role_model_access, mark_stale, run_statements,
    CORTEX_ACCESS_ROLES, cortex_role_sql, ALL_MODEL,
    AI_FUNCTION_PRIVILEGES, AI_FUNCTION_PRIVILEGE_PREFIX, ai_function_privilege,
    ai_function_model_note, ai_function_models_covered, ai_function_suggested_models,
)

d = load_all()
existing_roles = d["existing_roles"]
current_grants = d["current_grants"]

with st.spinner("Loading Cortex role grants..."):
    cortex_grants, missing = load_cortex_role_grants()

ROLE_KIND = {name: kind for name, kind, _ in CORTEX_ACCESS_ROLES}
ROLE_DESC = {name: desc for name, _, desc in CORTEX_ACCESS_ROLES}
present = [name for name, _, _ in CORTEX_ACCESS_ROLES if name not in missing]

AI_DB_ROLES = ("CORTEX_USER", "AI_FUNCTIONS_USER")
AI_PRIVILEGE = "USE AI FUNCTIONS"
AUTOMATION_PRIVILEGE = "EXECUTE AGENT TASK"

# ACCOUNTADMIN always has full access, so it isn't shown or managed here
role_grants = cortex_grants[
    (cortex_grants["granted_to"] == "ROLE")
    & (cortex_grants["grantee"].str.upper() != "ACCOUNTADMIN")
]
app_grants = cortex_grants[cortex_grants["granted_to"] == "APPLICATION"]


def holders(name):
    """Lists the account roles that hold a Cortex role or privilege directly.

    Args:
        name: Role or privilege name, e.g. ``"CORTEX_USER"``.

    Returns:
        A set of upper-case role names, ACCOUNTADMIN excluded. Includes ``"PUBLIC"`` if it holds it.
    """
    return set(role_grants[role_grants["role"] == name]["grantee"].str.upper())


def on_public(name):
    """Whether a Cortex role or privilege is granted to PUBLIC.

    Args:
        name: Role or privilege name.

    Returns:
        True if PUBLIC holds it directly.
    """
    return "PUBLIC" in holders(name)


def has_ai_privilege(role):
    """Whether a role has ``USE AI FUNCTIONS``, directly or through PUBLIC.

    Inheritance through other roles isn't checked.

    Args:
        role: Upper-case account role name.

    Returns:
        True if the role or PUBLIC holds the privilege.
    """
    return role in holders(AI_PRIVILEGE) or on_public(AI_PRIVILEGE)


def layer(name):
    """Describes what kind of control a role or privilege is, for the table's Layer column.

    Args:
        name: Role or privilege name from ``CORTEX_ACCESS_ROLES``.

    Returns:
        A label such as ``"Database role (one required for AI functions)"`` or ``"Application Role"``.
    """
    if name in AI_DB_ROLES:
        return "Database role (one required for AI functions)"
    if name == AI_PRIVILEGE:
        return "Account privilege (also required for AI functions)"
    if name == AUTOMATION_PRIVILEGE:
        return "Account privilege (CoCo automations)"
    return ROLE_KIND[name].title()


def _ai_model_notice(roles, key):
    """Shows that AI functions also need the models they run on, and offers to grant them.

    Args:
        roles: Account roles being granted AI function access.
        key: Widget key for the "Also grant the suggested models" checkbox.

    Returns:
        Plan rows ``(action, what, kind, role)`` for the suggested model grants if the user ticks the
        checkbox, otherwise an empty list. Also empty when no model grants are needed.
    """
    st.info(ai_function_model_note(d), icon=":material/info:")
    if ai_function_models_covered(d):
        return []
    suggested = ai_function_suggested_models(d)
    role_models = current_grants[current_grants["granted_to"] == "ROLE"]
    to_add = {}
    for r in roles:
        held = set(role_models[role_models["grantee"].str.upper() == r.upper()]["model"])
        if ALL_MODEL not in held:
            to_add[r] = [m for m in suggested if m not in held]
    to_add = {r: ms for r, ms in to_add.items() if ms}
    if not to_add:
        st.caption("These roles already hold every suggested model.")
        return []
    if st.checkbox("Also grant the suggested models", value=False, key=key):
        return [("GRANT", m, "MODEL", r) for r, ms in to_add.items() for m in ms]
    return []


# ── How access works ─────────────────────────────────────────────────

st.title("Feature Access")
st.caption(
    "Which account roles hold each Cortex and CoCo access role or privilege. "
    "Model RBAC decides *which* models a role can use; these decide whether it can use Cortex at all."
)

with st.expander("How Cortex access works", icon=":material/help:"):
    st.markdown(
        "**`CORTEX_USER` is the broad role; the others are narrower slices of it.** "
        "`CORTEX_USER` covers all of Snowflake's Covered AI Features. Each narrow role covers one area, "
        "and everything it grants is already included in `CORTEX_USER`:\n\n"
        "| Role | Covers |\n"
        "|---|---|\n"
        "| `CORTEX_USER` | Everything below (all Covered AI Features) |\n"
        "| `CORTEX_AGENT_USER` | Cortex Agents and agentic surfaces (CoCo, Snowflake CoWork) |\n"
        "| `AI_FUNCTIONS_USER` | AI functions only |\n"
        "| `CORTEX_ANALYST_USER` | Cortex Analyst only |\n"
        "| `CORTEX_EMBED_USER` | Embedding functions only |\n"
        "| `CORTEX_REST_API_USER` | Cortex REST API only |\n\n"
        "Grants add up, so a role with `CORTEX_USER` keeps everything no matter which narrow roles it has. "
        "To restrict a role to one area, revoke `CORTEX_USER` from it (and from PUBLIC) and grant the narrow role.\n\n"
        "**AI functions also need `USE AI FUNCTIONS`.** That's an account privilege, not a role, and "
        "neither includes the other:\n\n"
        "| Database role | `USE AI FUNCTIONS` | Can call AI functions? |\n"
        "|---|---|---|\n"
        "| Yes | Yes | Yes |\n"
        "| Yes | No | No |\n"
        "| No | Yes | Only `AI_AGG` and `AI_SUMMARIZE_AGG` |\n\n"
        "- **`USE AI FUNCTIONS`** can be narrowed to single functions, for example "
        "`GRANT USE AI FUNCTION AI_COMPLETE ON ACCOUNT TO ROLE analyst`. The blanket and per-function "
        "privileges are an OR: a role with the blanket privilege can call every AI function, so per-function "
        "grants and revokes have no effect on it. Per-function grants still need `CORTEX_USER` or "
        "`AI_FUNCTIONS_USER`. Manage them under **Per-function AI privileges** below.\n"
        "- **PUBLIC** holds `CORTEX_USER` and `USE AI FUNCTIONS` by default. A grant on PUBLIC "
        "reaches every role, so revoking from one role has no effect while PUBLIC still holds it.\n"
        "- **Cortex Agents** run with each user's *default* role, so that's the role that needs the grant.\n"
        "- **CoCo automations** need the `EXECUTE AGENT TASK` account privilege, which is on PUBLIC by default. "
        "Each run uses the creating user's default role and default secondary roles.\n"
        "- `USE AI FUNCTIONS` currently isn't enforced for AI function calls made inside Snowflake Native Apps."
    )

# ── Overview ─────────────────────────────────────────────────────────

st.subheader("Roles and privileges")
st.markdown(
    "Every Snowflake role and account privilege that controls access to Cortex or CoCo, what it covers, and "
    "which account roles hold it directly. **On PUBLIC** means every user has it. Access inherited through other "
    "roles isn't counted here; check that on **Verify Role**. To change a grant, use **Manage grants** below."
)

overview = []
for name in present:
    grantees = sorted(holders(name))
    overview.append({
        "Role or privilege": name,
        "Description": ROLE_DESC[name] or "\u2014",
        "On PUBLIC": "PUBLIC" in grantees,
        "Account roles": len(grantees),
        "Granted to": ", ".join(grantees) if grantees else "\u2014",
        "Layer": layer(name),
    })
st.dataframe(
    pd.DataFrame(overview),
    hide_index=True,
    width="stretch",
    column_config={
        "On PUBLIC": st.column_config.CheckboxColumn(help="Every user and role inherits PUBLIC"),
        "Description": st.column_config.TextColumn(width="large"),
    },
)
if missing:
    st.caption(f"Not present in this account: {', '.join(missing)}")

def _plan_and_apply(rows, form_key):
    """Shows a GRANT/REVOKE plan and a form that runs it.

    On Apply Changes, runs every statement in parallel, reports failures, and clears the grant caches.

    Args:
        rows: ``(action, what, kind, role)`` tuples, as accepted by ``cortex_role_sql``.
        form_key: Unique key for the form, so several plans can appear on the page.
    """
    plan = pd.DataFrame(
        [{"Action": a, "What": what, "Role": r, "SQL": cortex_role_sql(a, what, k, r)} for a, what, k, r in rows]
    )
    st.dataframe(plan, hide_index=True, width="stretch")

    with st.form(form_key):
        st.warning(f"This will execute {len(plan)} statement(s). Review the plan above carefully.")
        apply = st.form_submit_button(":material/play_arrow: Apply Changes", type="primary")

    if apply:
        # Each row grants or revokes a different role or privilege, so they can run concurrently
        errors = run_statements(plan["SQL"].tolist(), parallel=True)
        # Clear even on partial failure so the page reflects whatever did succeed
        load_cortex_role_grants.clear()
        load_current_grants.clear()
        load_role_model_access.clear()
        mark_stale()
        if errors:
            for s, err in errors:
                st.error(f"Failed: {s}\n\n{err}")
        else:
            st.rerun()


# ── Manage one role ──────────────────────────────────────────────────

st.subheader("Manage grants")

selected = st.selectbox(
    "Cortex role",
    present,
    format_func=lambda n: f"{n}  ({ROLE_KIND[n].title()})",
)
kind = ROLE_KIND[selected]
if ROLE_DESC[selected]:
    st.caption(ROLE_DESC[selected])

have = holders(selected)
grantable = sorted(r for r in existing_roles if r != "ACCOUNTADMIN")

desired = st.multiselect(
    f"Account roles that should hold **{selected}**",
    grantable,
    default=sorted(r for r in have if r in grantable),
    help="ACCOUNTADMIN is excluded because it always has full access.",
    key=f"cortex_role_grantees_{selected}",
)
desired_set = set(desired)

apps_holding = sorted(app_grants[app_grants["role"] == selected]["grantee"].unique())
if apps_holding:
    st.caption(f"Also held by applications (read-only here): {', '.join(apps_holding)}")

to_grant = sorted(desired_set - have)
to_revoke = sorted(have - desired_set)

if not (to_grant or to_revoke):
    st.success("Grants already match your selection \u2014 nothing to change.")
else:
    rows = (
        [("GRANT", selected, kind, r) for r in to_grant]
        + [("REVOKE", selected, kind, r) for r in to_revoke]
    )

    # ── What this plan actually does ─────────────────────────────────
    explained = False
    revoking_public = "PUBLIC" in to_revoke

    # Database role granted, but the role can't call AI functions without the privilege
    if selected in AI_DB_ROLES and AI_PRIVILEGE not in missing:
        lacking = [r for r in to_grant if not has_ai_privilege(r)]
        if lacking:
            explained = True
            st.warning(
                f"**These roles still won't be able to call AI functions:** {', '.join(lacking)}. "
                f"They don't hold `{AI_PRIVILEGE}` directly or through PUBLIC.",
                icon=":material/warning:",
            )
            if st.checkbox(f"Also grant {AI_PRIVILEGE} to these roles", value=True,
                           key=f"add_ai_priv_{selected}"):
                rows += [("GRANT", AI_PRIVILEGE, "PRIVILEGE", r) for r in lacking]

    # Privilege revoked from PUBLIC: only direct holders keep AI functions
    if selected == AI_PRIVILEGE and revoking_public:
        explained = True
        keep = sorted(desired_set)
        st.error(
            f"**AI functions will stop working for every role that doesn't hold `{AI_PRIVILEGE}` directly**, "
            f"even roles with `CORTEX_USER`. Roles that keep it: "
            f"{', '.join(keep) if keep else 'none'} (plus ACCOUNTADMIN).",
            icon=":material/error:",
        )

    # CORTEX_USER revoked from PUBLIC
    if selected == "CORTEX_USER" and revoking_public:
        explained = True
        if on_public("CORTEX_AGENT_USER"):
            agent_note = (
                "CoCo, Cortex Agents and Snowflake CoWork stay available to everyone, because PUBLIC "
                "also holds `CORTEX_AGENT_USER`. Revoke that too to restrict them."
            )
        else:
            agent_note = "Roles without a direct grant also lose CoCo, Cortex Agents and Snowflake CoWork."
        st.warning(
            "**Roles without a direct grant lose AI functions** unless they hold `AI_FUNCTIONS_USER`. "
            f"{agent_note} To keep only AI functions for specific roles, grant them `AI_FUNCTIONS_USER`.",
            icon=":material/warning:",
        )

    # CORTEX_AGENT_USER revoked from PUBLIC while CORTEX_USER still grants the same surfaces
    if selected == "CORTEX_AGENT_USER" and revoking_public and on_public("CORTEX_USER"):
        explained = True
        st.info(
            "CoCo, Cortex Agents and Snowflake CoWork stay available to everyone, because PUBLIC "
            "also holds `CORTEX_USER`.",
            icon=":material/info:",
        )

    # CoCo automations privilege revoked from PUBLIC
    if selected == AUTOMATION_PRIVILEGE and revoking_public:
        explained = True
        keep = sorted(desired_set)
        st.warning(
            f"**Users without a role that holds `{AUTOMATION_PRIVILEGE}` can no longer create or run CoCo "
            f"automations.** Roles that keep it: {', '.join(keep) if keep else 'none'} (plus ACCOUNTADMIN).\n\n"
            "Their existing automations stay, with run history until it expires, and can be resumed if access "
            "is restored.",
            icon=":material/warning:",
        )
    if selected == AUTOMATION_PRIVILEGE and to_grant:
        st.info(
            "Each automation run uses the creating user's default role and default secondary roles, so those "
            "roles also need access to everything the automation's prompt uses.",
            icon=":material/info:",
        )

    # Revoking from individual roles has no effect while PUBLIC keeps it
    unaffected = [r for r in to_revoke if r != "PUBLIC"] if "PUBLIC" in desired_set else []
    if unaffected:
        explained = True
        st.info(
            f"**No change in access for {', '.join(unaffected)}:** they still get `{selected}` through PUBLIC.",
            icon=":material/info:",
        )

    # Generic fallback for other roles revoked from PUBLIC
    if revoking_public and selected not in (AI_PRIVILEGE, AUTOMATION_PRIVILEGE, "CORTEX_USER", "CORTEX_AGENT_USER"):
        explained = True
        st.warning(
            f"Revoking **{selected}** from PUBLIC removes it from every role that doesn't hold it directly.",
            icon=":material/warning:",
        )

    if explained:
        st.caption("These checks cover direct grants and PUBLIC only, not access inherited through other roles.")

    # Roles getting USE AI FUNCTIONS, directly or through the checkbox above, also need the models
    ai_priv_grantees = [r for a, what, _, r in rows if a == "GRANT" and what == AI_PRIVILEGE]
    if ai_priv_grantees:
        rows += _ai_model_notice(ai_priv_grantees, f"ai_models_{selected}")

    _plan_and_apply(rows, f"apply_cortex_role_{selected}")


# ── Per-function AI privileges ───────────────────────────────────────

st.subheader("Per-function AI privileges")
st.caption(
    "Instead of the blanket `USE AI FUNCTIONS`, a role can be allowed specific functions only, for example "
    "`USE AI FUNCTION AI_COMPLETE`. The two work as an OR: a role that holds the blanket privilege can call every "
    "AI function, so per-function grants and revokes have no effect on it."
)

FN_LABEL = dict(AI_FUNCTION_PRIVILEGES)
fn_role = st.selectbox(
    "Account role",
    grantable,
    index=None,
    placeholder="Choose a role",
    key="ai_fn_role",
    help="ACCOUNTADMIN is excluded because it always has full access.",
)

if fn_role:
    fn_have = {
        g[len(AI_FUNCTION_PRIVILEGE_PREFIX):]
        for g in role_grants[
            (role_grants["kind"] == "FUNCTION PRIVILEGE") & (role_grants["grantee"].str.upper() == fn_role)
        ]["role"]
    }
    fn_desired = st.multiselect(
        f"AI functions **{fn_role}** may call",
        [fn for fn, _ in AI_FUNCTION_PRIVILEGES],
        default=sorted(f for f in fn_have if f in FN_LABEL),
        format_func=lambda fn: FN_LABEL[fn] if FN_LABEL[fn] == fn else f"{fn}  ({FN_LABEL[fn]})",
        key=f"ai_fn_grants_{fn_role}",
    )
    fn_grant = sorted(set(fn_desired) - fn_have)
    # Grants for functions this app doesn't list are left alone rather than revoked
    fn_unknown = sorted(fn_have - set(FN_LABEL))
    fn_revoke = sorted(fn_have - set(fn_desired) - set(fn_unknown))
    if fn_unknown:
        st.caption(f"Also granted, not managed here: {', '.join(fn_unknown)}")

    # Blanket privilege makes per-function grants a no-op for this role
    if fn_role in holders(AI_PRIVILEGE) or (fn_role == "PUBLIC" and on_public(AI_PRIVILEGE)):
        blanket_via = "directly"
    elif on_public(AI_PRIVILEGE):
        blanket_via = "through PUBLIC"
    else:
        blanket_via = None
    if blanket_via:
        st.info(
            f"**These grants have no effect right now:** {fn_role} holds `{AI_PRIVILEGE}` {blanket_via}, which "
            "already covers every AI function. Per-function grants only start limiting the role once it no "
            f"longer has the blanket privilege"
            + (" (revoke it from PUBLIC on the Home page or above)." if blanket_via == "through PUBLIC"
               else "."),
            icon=":material/info:",
        )

    rows = (
        [("GRANT", ai_function_privilege(fn), "FUNCTION PRIVILEGE", fn_role) for fn in fn_grant]
        + [("REVOKE", ai_function_privilege(fn), "FUNCTION PRIVILEGE", fn_role) for fn in fn_revoke]
    )

    # Per-function grants still need one of the database roles
    has_db_role = any(fn_role in holders(r) or on_public(r) for r in AI_DB_ROLES)
    if fn_grant and not has_db_role:
        st.warning(
            f"**{fn_role} still won't be able to call these functions:** it doesn't hold `CORTEX_USER` or "
            "`AI_FUNCTIONS_USER` directly or through PUBLIC, and per-function privileges need one of them.",
            icon=":material/warning:",
        )
        if "AI_FUNCTIONS_USER" not in missing and st.checkbox(
            "Also grant AI_FUNCTIONS_USER to this role", value=True, key=f"ai_fn_db_role_{fn_role}"
        ):
            rows.append(("GRANT", "AI_FUNCTIONS_USER", ROLE_KIND["AI_FUNCTIONS_USER"], fn_role))

    if fn_grant:
        rows += _ai_model_notice([fn_role], f"ai_fn_models_{fn_role}")

    if rows:
        st.caption("These checks cover direct grants and PUBLIC only, not access inherited through other roles.")
        _plan_and_apply(rows, f"apply_ai_fn_{fn_role}")
    else:
        st.success("Grants already match your selection \u2014 nothing to change.")
