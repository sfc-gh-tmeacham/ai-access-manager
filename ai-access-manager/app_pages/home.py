"""Home page: models at a glance, then this account's Cortex access settings compared with Snowflake's defaults.

Sections, top to bottom:

* **Models at a glance**: model counts by lifecycle status, and the number of roles with model grants.
* **Inference routing**: ``CORTEX_ENABLED_CROSS_REGION``, with pricing for each routing tier.
* **Model access**: the ``CORTEX_MODELS_ALLOWLIST`` migration (only while it's set),
  ``CORTEX-MODEL-ROLE-ALL`` on PUBLIC, individual model grants to PUBLIC, and end-of-life model grants.
* **Feature access**: the Cortex database roles and account privileges on PUBLIC.

Inference routing renders first from one fast parameter query. The slow grant data (``load_all``) loads
after it, inside the metric card container near the top, which shows a loading skeleton until the cards
fill in.

Each check is a collapsed expander (``_check``) inside a bordered group (``_group``) whose heading
summarizes its checks. Every change opens a right-side drawer (``st.dialog``) showing the SQL, and runs
only on Confirm. Statements the page can run:

* ``ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION`` and ``CORTEX_MODELS_ALLOWLIST``.
* ``CALL SNOWFLAKE.LOCAL.GRANT_TO_PUBLIC_APPLICATION_ROLE`` / ``REVOKE_FROM_PUBLIC_APPLICATION_ROLE`` for
  ``CORTEX-MODEL-ROLE-ALL``.
* GRANT and REVOKE of model application roles, Cortex database roles and account privileges to and from
  PUBLIC.
"""

import streamlit as st
import pandas as pd
import zlib
from contextlib import contextmanager
from data import (
    load_all, load_allowlist, load_bundle_status, load_cross_region, load_public_grants,
    load_current_grants, load_cortex_role_grants, load_role_model_access, mark_stale, PREFIX, STATUS_EMOJI,
    STATUS_ORDER, app_role, cortex_role_sql, run_statements, confirm_buttons, AI_FUNCTION_PRIVILEGE_PREFIX, ALL_MODEL,
    ai_function_model_note, ai_function_models_covered, per_model_grants_tip, BCR_2378_URL, show_error,
    cross_region_routing, model_availability,
)

# Inference routing renders first from a single fast parameter query; the slow grant data
# (load_all) loads after it, and the overview metrics under the title fill in once that's done.
cross_region_value, cross_region_default, cross_region_level = load_cross_region()

# ── Helper to run remediation SQL ─────────────────────────────────────

def _run_remediation(statements, invalidate=None, parallel=False):
    """Runs remediation SQL, clears only the affected caches, then reruns the app.

    On failure, shows each error and returns without rerunning, so the dialog stays open with the errors.
    Caches are cleared even on partial failure, so the page reflects whatever did succeed.

    Args:
        statements: SQL statements to run.
        invalidate: Cached loader functions to clear afterwards, e.g. ``[load_cross_region]``.
        parallel: Run the statements concurrently. Only for statements that don't depend on each other.
    """
    errors = run_statements(statements, parallel=parallel)
    if invalidate:
        for cache_fn in invalidate:
            cache_fn.clear()
    mark_stale()
    if errors:
        ok_count = len(statements) - len(errors)
        if ok_count:
            st.warning(f"{ok_count} of {len(statements)} statement(s) succeeded; the rest failed.")
        for sql, err in errors:
            st.error(f"Failed: {sql}\n\n{err}")
        return
    st.rerun(scope="app")


st.title("Home")

# ── Models at a glance ───────────────────────────────────────────────
st.subheader("Models at a glance")
st.markdown(
    "Cortex models in Snowflake's catalog for this account, counted by lifecycle status, and how many roles "
    "hold model grants directly. Not every model runs in this account's region; see **Model Catalog** for details."
)
kpi_row = st.container()

# ── Cortex access settings ───────────────────────────────────────────
st.subheader("Cortex access settings")
st.markdown(
    "How this account's Cortex routing, model access and feature access are set, compared with Snowflake's "
    "defaults. Expand any row to see details or make a change."
)

_CHECK_STYLE = {
    # level: (icon, status text colour)
    "success": (":material/check_circle:", "green"),
    "warning": (":material/warning:", "orange"),
    "error": (":material/error:", "red"),
    "info": (":material/info:", "blue"),
}


def _check(title, status, level, non_default=False):
    """Opens a collapsed expander for one setting check, with its status in the title.

    The result is recorded in the enclosing ``_group`` so its heading can summarize it.

    Args:
        title: Setting name shown in bold, e.g. ``"CORTEX_USER"``.
        status: Short status text shown after the title, e.g. ``"On PUBLIC (default)"``.
        level: ``"success"``, ``"warning"``, ``"error"`` or ``"info"``. Sets the icon and status colour.
        non_default: True if the setting differs from Snowflake's default. Counted in the group summary.

    Returns:
        The ``st.expander`` to render the check's details in.
    """
    if _group_results:
        _group_results[-1].append((level, non_default))
    icon, colour = _CHECK_STYLE[level]
    return st.expander(f"**{title}** \u00b7 :{colour}[{status}]", expanded=False, icon=icon)


def _plural(n, word):
    """Formats a count with a simply pluralized word.

    Args:
        n: The count.
        word: Singular word, e.g. ``"warning"``.

    Returns:
        For example ``"1 warning"`` or ``"2 warnings"``.
    """
    return f"{n} {word}{'' if n == 1 else 's'}"


def _summary(results):
    """Builds the coloured one-line summary shown in a group's heading.

    Args:
        results: ``(level, non_default)`` tuples recorded by ``_check`` for the group.

    Returns:
        Markdown such as ``":orange[1 warning] · :blue[2 changed from default]"``, or a green
        "No warnings, all defaults" when nothing is flagged.
    """
    errors = sum(level == "error" for level, _ in results)
    warnings = sum(level == "warning" for level, _ in results)
    # A warning already flags a change, so only count defaults on checks that aren't warnings
    changed = sum(nd for level, nd in results if level not in ("error", "warning"))
    parts = []
    if errors:
        parts.append(f":red[{_plural(errors, 'error')}]")
    if warnings:
        parts.append(f":orange[{_plural(warnings, 'warning')}]")
    if changed:
        parts.append(f":blue[{changed} changed from default]")
    return " \u00b7 ".join(parts) if parts else ":green[No warnings, all defaults]"


_group_results = []


@contextmanager
def _group(title, caption, important=False):
    """Renders a bordered box for related checks; its heading summarizes the checks rendered inside.

    The heading is a placeholder filled in after the body runs, once every ``_check`` has recorded its
    result. If the body raises, the error is shown inside the box with a Retry button and the page
    carries on with the next group, so one failed query doesn't hide the rest of the page. ``st.rerun()``
    and ``st.stop()`` raise ``BaseException`` subclasses, so they still pass through.

    Args:
        title: Group heading, e.g. ``"Model access"``.
        caption: Markdown description shown under the heading.
        important: Add an orange "Important" badge to the heading.

    Yields:
        None. Render the group's checks inside the ``with`` block.
    """
    with st.container(border=True):
        heading = st.empty()
        # Body text rather than st.caption, which is too faint for a description this long
        st.markdown(caption)
        results = []
        _group_results.append(results)
        failed = False
        try:
            yield
        except Exception as e:
            failed = True
            show_error(f"Couldn't check {title.lower()}", e, retry_key=f"_retry_group_{title}")
        finally:
            _group_results.pop()
            badge = " :orange-badge[Important]" if important else ""
            summary = ":red[Couldn't check]" if failed else _summary(results)
            heading.markdown(f"##### {title}{badge} · {summary}")


with _group(
    "Inference routing",
    "Where Snowflake can process your Cortex requests (`CORTEX_ENABLED_CROSS_REGION`). It decides which models "
    "and features the account can use and the price per AI Credit, and applies to every user regardless of grants.",
    important=True,
):
    # Cross-region inference
    CLOUD_GLOBAL_VALUES = ["AWS_GLOBAL", "AZURE_GLOBAL", "GCP_GLOBAL"]
    # Grouped by geography (US first), then by cloud within each geography
    REGIONAL_GROUPS = {
        "US": ["AWS_US", "AZURE_US", "GCP_US"],
        "EU": ["AWS_EU", "AZURE_EU"],
        "Asia-Pacific": ["AWS_APJ", "AWS_JP", "AWS_AU"],
    }
    REGIONAL_VALUES = [v for vals in REGIONAL_GROUPS.values() for v in vals]

    # List price per AI Credit by routing type (Snowflake AI pricing docs)
    PRICE_GLOBAL = 2.00
    PRICE_REGIONAL = 2.20
    PRICING_DOCS_URL = "https://docs.snowflake.com/en/user-guide/snowflake-cortex/pricing"


    def _usd(amount, decimals=2):
        """Formats a dollar amount for Streamlit Markdown.

        The ``$`` is escaped because a bare pair of dollar signs renders as LaTeX.

        Args:
            amount: Amount in US dollars.
            decimals: Decimal places to show.

        Returns:
            For example ``"\\$2.00"``.
        """
        return f"\\${amount:,.{decimals}f}"

    is_disabled = cross_region_value.upper() == "DISABLED" if cross_region_value != "n/a" else False
    is_any = cross_region_value.upper() == "ANY_REGION" if cross_region_value != "n/a" else False
    # Snowflake reports the parameter's default and whether it's set on the account (level = ACCOUNT)
    def _region_set(v):
        """Parses a comma-separated ``CORTEX_ENABLED_CROSS_REGION`` value into a set, for order-free comparison.

        Args:
            v: The parameter value, e.g. ``"AWS_US,AZURE_US"``.

        Returns:
            A set of upper-case values.
        """
        return {x.strip().upper() for x in str(v).split(",") if x.strip()}

    is_default = cross_region_default != "n/a" and _region_set(cross_region_value) == _region_set(cross_region_default)
    is_explicitly_set = cross_region_level.upper() == "ACCOUNT"
    _default_label = "default" if is_default else "not the default"

    current_regions = (
        [v.strip().upper() for v in cross_region_value.split(",") if v.strip()]
        if cross_region_value not in ("n/a", "DISABLED", "ANY_REGION") else []
    )

    ROUTING_TIERS = ["Global", "Cloud-specific", "Regional", "Disabled"]
    if is_any:
        current_tier = "Global"
    elif is_disabled:
        current_tier = "Disabled"
    elif current_regions and all(r in CLOUD_GLOBAL_VALUES for r in current_regions):
        current_tier = "Cloud-specific"
    else:
        current_tier = "Regional"


    @st.dialog("Configure cross-region inference", width="large", position="right", icon=":material/public:")
    def _confirm_change_cross_region():
        """Drawer for changing cross-region inference.

        Explains each routing tier and its price, lets the user pick a tier (and regions for the
        cloud-specific and regional tiers), and on Confirm runs
        ``ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION``.
        """
        st.markdown(
            "Cross-region inference controls where Cortex can process inference requests. "
            "Your data stays stored in your account's region: only the prompt and response are sent, "
            "transiently, to the processing region. Credits are billed in your account's region, "
            "and there are no data egress charges."
        )

        st.info(
            "**Inference is secured with every option**\n\n"
            "- Data in transit is always encrypted.\n"
            "- No customer data is persisted in the processing region; it handles the request and returns the result.\n"
            "- **Within the same cloud provider**, traffic stays on that provider's private backbone "
            "and never crosses the public internet.\n"
            "- **Across cloud providers**, traffic uses mutual TLS (mTLS), with encrypted, mutually "
            "authenticated connections between Snowflake endpoints.",
            icon=":material/lock:",
        )

        st.markdown("##### Pricing by routing type")
        st.dataframe(
            pd.DataFrame([
                {"Routing": "Global", "Parameter value": "ANY_REGION",
                 "Where requests can run": "Any Snowflake-supported region, any cloud",
                 "Price per AI Credit": f"${PRICE_GLOBAL:.2f}",
                 "Best for": "Broadest model selection, highest throughput, lowest cost"},
                {"Routing": "Cloud-specific", "Parameter value": "AWS_GLOBAL, AZURE_GLOBAL, GCP_GLOBAL",
                 "Where requests can run": "Any region within the chosen cloud provider(s)",
                 "Price per AI Credit": f"${PRICE_GLOBAL:.2f}",
                 "Best for": "Staying on one cloud's private network at the global price"},
                {"Routing": "Regional", "Parameter value": "AWS_US, AWS_EU, AZURE_EU, …",
                 "Where requests can run": "Only the chosen cloud + geography",
                 "Price per AI Credit": f"${PRICE_REGIONAL:.2f}",
                 "Best for": "Data residency within a specific geography"},
                {"Routing": "Disabled", "Parameter value": "DISABLED",
                 "Where requests can run": "Your account's home region only",
                 "Price per AI Credit": f"${PRICE_REGIONAL:.2f}",
                 "Best for": "Strict sovereignty; limits available models"},
            ]),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            f"List prices from [Snowflake AI pricing]({PRICING_DOCS_URL}). "
            "ACV-based AI Credit discounts may lower your actual rate."
        )

        st.markdown("##### Choose routing")
        tier = st.radio(
            "Routing type",
            ROUTING_TIERS,
            index=ROUTING_TIERS.index(current_tier),
            horizontal=True,
            captions=[
                f"{_usd(PRICE_GLOBAL)}/credit",
                f"{_usd(PRICE_GLOBAL)}/credit",
                f"{_usd(PRICE_REGIONAL)}/credit",
                f"{_usd(PRICE_REGIONAL)}/credit",
            ],
        )

        if tier == "Global":
            value = "ANY_REGION"
        elif tier == "Disabled":
            value = "DISABLED"
        elif tier == "Cloud-specific":
            selected = st.multiselect(
                "Cloud providers",
                CLOUD_GLOBAL_VALUES,
                default=[r for r in CLOUD_GLOBAL_VALUES if r in current_regions],
                help="Select one or more. Requests are routed only within these boundaries.",
            )
        else:
            # One pill group per geography, divided by horizontal lines
            st.markdown("**Regions** — select one or more. Requests are routed only within these boundaries.")
            selected = []
            for i, (geo, vals) in enumerate(REGIONAL_GROUPS.items()):
                if i:
                    st.divider()
                selected += st.pills(
                    geo,
                    vals,
                    selection_mode="multi",
                    default=[v for v in vals if v in current_regions],
                    key=f"cross_region_pills_{geo}",
                )

        if tier in ("Cloud-specific", "Regional"):
            if not selected:
                st.warning("Select at least one value.")
                confirm_buttons("confirm_cross_region", disabled=True)
                return
            value = ",".join(selected)

        # Cost impact of the selection
        if tier in ("Global", "Cloud-specific"):
            st.success(
                f"**{_usd(PRICE_GLOBAL)} per AI Credit**, the lowest available rate.",
                icon=":material/savings:",
            )
        else:
            premium = (PRICE_REGIONAL - PRICE_GLOBAL) / PRICE_GLOBAL
            st.warning(
                f"**{_usd(PRICE_REGIONAL)} per AI Credit**, {premium:.0%} more than global routing "
                f"({_usd(PRICE_GLOBAL)}). For example, 10,000 AI Credits would cost "
                f"{_usd(10_000 * PRICE_REGIONAL, 0)} instead of {_usd(10_000 * PRICE_GLOBAL, 0)}. "
                + ("Disabling also limits you to models deployed in your home region."
                   if tier == "Disabled" else
                   "Choose this when inference must stay within a specific geography."),
                icon=":material/payments:",
            )

        # Network path for the selection
        if tier == "Global":
            st.caption(
                ":material/lock: Requests may be processed on another cloud provider; "
                "cross-cloud traffic is protected with mTLS."
            )
        elif tier == "Disabled":
            st.caption(":material/lock: Requests are processed in your account's home region only.")
        else:
            st.caption(
                ":material/lock: Requests stay within the selected cloud provider(s) on the "
                "provider's private backbone network."
            )

        sql = f"ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = '{value}'"
        st.code(sql, language="sql")
        if confirm_buttons("confirm_cross_region"):
            _run_remediation([sql], invalidate=[load_cross_region])

    if is_disabled:
        _xr_status, _xr_level = f"Disabled ({_default_label})", "warning"
    else:
        _xr_status, _xr_level = f"{cross_region_value} ({_default_label})", "info"

    with _check("Cross-region inference", _xr_status, _xr_level, non_default=not is_default):
        if is_disabled:
            st.warning(
                "Requests are processed only in your account's home region. "
                "Some frontier models and Cortex features require cross-region inference "
                "and will not be available.",
                icon=":material/warning:",
            )
        elif is_any:
            st.info(
                "Requests can be routed to any Snowflake-supported region. "
                "Broadest model selection, highest throughput.",
                icon=":material/public:",
            )
        else:
            st.info(
                f"Requests are restricted to: {', '.join(current_regions) if current_regions else cross_region_value}.",
                icon=":material/public:",
            )
        st.caption(
            f"Snowflake default: `{cross_region_default}`. "
            + (f"Set on the account to `{cross_region_value}`." if is_explicitly_set
               else "Not set on the account, so the default applies.")
        )
        btn_label = ":material/public: Enable cross-region" if is_disabled else ":material/settings: Change routing"
        st.button(btn_label, key="btn_cross_region", on_click=_confirm_change_cross_region)


# ── Grant and model data (slow on a cold cache) ──────────────────────

# Load inside the empty metric row so its loading skeleton holds the cards' space, and the page doesn't
# jump when they fill in
with kpi_row:
    d = load_all(skeleton_height=120)
allowlist_value = d["allowlist_value"]
all_is_public = d["all_is_public"]
all_via_app_role = d["all_via_app_role"]
all_via_account_role = d["all_via_account_role"]
pub_models = d["pub_models"]
current_grants = d["current_grants"]
available_models = d["available_models"]
status_map = d["status_map"]

granted_roles_count = current_grants[current_grants["granted_to"] == "ROLE"]["grantee"].nunique()


def _models_with_status(status):
    """Counts available models with a given lifecycle status.

    Args:
        status: Lifecycle status, e.g. ``"GA"``.

    Returns:
        The number of models in ``available_models`` with that status.
    """
    return sum(1 for m in available_models if status_map.get(m) == status)


with kpi_row, st.container(horizontal=True):
    st.metric("GA models", _models_with_status("GA"), border=True, help="Generally available")
    st.metric("Public preview models", _models_with_status("PUPR"), border=True, help="PUPR")
    st.metric("Private preview models", _models_with_status("PRPR"), border=True, help="PRPR")
    st.metric("Legacy models", _models_with_status("LEGACY"), border=True, help="LEGACY lifecycle status")
    st.metric("Roles with model grants", granted_roles_count, border=True,
              help="Account roles, including PUBLIC, that hold at least one model application role directly.")


# Allowlist migration (BCR-2378): only shown while the deprecated parameter is still set
CLEAR_ALLOWLIST_SQL = "ALTER ACCOUNT SET CORTEX_MODELS_ALLOWLIST = 'None'"
allowlist_set = bool(allowlist_value) and allowlist_value not in ("n/a", "None", "")

if allowlist_set:
    # Once the 2026_07 bundle is on, Snowflake no longer consults the allowlist at all
    _bundle = load_bundle_status("2026_07")
    _allowlist_ignored = _bundle in ("ENABLED", "RELEASED")

    @st.dialog("Set CORTEX_MODELS_ALLOWLIST to None", width="large", position="right", icon=":material/healing:")
    def _confirm_clear_allowlist():
        """Drawer for setting ``CORTEX_MODELS_ALLOWLIST`` to ``None``.

        The wording depends on whether the 2026_07 behavior change bundle is enabled: if it is, the allowlist
        is no longer consulted and this is cleanup only; if not, roles that only reach a model through the
        allowlist lose access.
        """
        if _allowlist_ignored:
            st.markdown("The allowlist is no longer consulted, so this is cleanup only: "
                        "**no role gains or loses model access.**")
        else:
            st.markdown("This removes the allowlist fallback. **Any role that only reaches a model through the "
                        "allowlist loses access to it.** Make sure the matching model roles are granted first.")
        st.code(CLEAR_ALLOWLIST_SQL, language="sql")
        if confirm_buttons("confirm_allowlist"):
            _run_remediation([CLEAR_ALLOWLIST_SQL], invalidate=[load_allowlist])

    with st.container(border=True):
        st.markdown("##### :orange[Migration required] \u00b7 BCR-2378")
        st.caption("Snowflake is retiring `CORTEX_MODELS_ALLOWLIST`. Model RBAC becomes the only way to "
                   "control model access, and the parameter will only accept `'None'`.")
        st.warning(f"`CORTEX_MODELS_ALLOWLIST` is still set to `{allowlist_value}`.", icon=":material/warning:")
        if _allowlist_ignored:
            st.info(
                "**It no longer has any effect here.** The 2026_07 behavior change bundle is enabled, so "
                "Snowflake decides model access from model RBAC grants only. Setting it to `None` is cleanup "
                "and doesn't change who can use which model.",
                icon=":material/info:",
            )
        else:
            _bundle_note = ("The 2026_07 behavior change bundle isn't enabled yet"
                            if _bundle == "DISABLED" else
                            "This app couldn't read the 2026_07 behavior change bundle status")
            st.info(
                f"**It may still grant access.** {_bundle_note}. Until the bundle is enabled, the allowlist is a fallback: when "
                "a role has no model RBAC grant for a model, Snowflake checks the allowlist. Grant the matching "
                "model roles (on **Model Access**) and test with a role other than ACCOUNTADMIN "
                "(on **Verify Role**) before setting it to `None`.",
                icon=":material/info:",
            )
        st.caption(f"Timeline and migration steps: [BCR-2378]({BCR_2378_URL}).")
        st.button(":material/healing: Set allowlist to None", key="btn_allowlist",
                  on_click=_confirm_clear_allowlist, type="primary")


with _group(
    "Model access",
    "Which models roles can call, through the model application roles (`CORTEX-MODEL-ROLE-<model>`). "
    "By default `CORTEX-MODEL-ROLE-ALL` is on PUBLIC, so every role can use every model. "
    "A role also needs feature access (below) to use one.",
):
    # ALL public
    # Documented procedures for Snowflake's default (bootstrap) grant on the SNOWFLAKE.PUBLIC application role.
    # A plain GRANT/REVOKE ... ROLE PUBLIC targets the account role instead and doesn't touch the bootstrap grant.
    GRANT_ALL_SQL = "CALL SNOWFLAKE.LOCAL.GRANT_TO_PUBLIC_APPLICATION_ROLE('APP_ROLE', 'CORTEX-MODEL-ROLE-ALL')"
    REVOKE_ALL_APP_ROLE_SQL = "CALL SNOWFLAKE.LOCAL.REVOKE_FROM_PUBLIC_APPLICATION_ROLE('APP_ROLE', 'CORTEX-MODEL-ROLE-ALL')"
    REVOKE_ALL_ACCOUNT_ROLE_SQL = 'REVOKE APPLICATION ROLE SNOWFLAKE."CORTEX-MODEL-ROLE-ALL" FROM ROLE PUBLIC'

    # Revoke only the path(s) that actually grant ALL
    revoke_all_sql = []
    if all_via_app_role:
        revoke_all_sql.append(REVOKE_ALL_APP_ROLE_SQL)
    if all_via_account_role:
        revoke_all_sql.append(REVOKE_ALL_ACCOUNT_ROLE_SQL)

    @st.dialog("Revoke ALL from PUBLIC", width="large", position="right", icon=":material/warning:")
    def _confirm_revoke_all():
        """Drawer for revoking ``CORTEX-MODEL-ROLE-ALL`` from PUBLIC.

        Warns which roles keep model access (those with their own grants, and models granted to PUBLIC
        individually) and shows a checklist to follow first. On Confirm, revokes ALL through whichever paths
        grant it: the ``SNOWFLAKE.PUBLIC`` application role, the PUBLIC account role, or both.
        """
        st.markdown(
            "This revokes `CORTEX-MODEL-ROLE-ALL` from PUBLIC. "
            "After this, roles will only have access to models explicitly granted to them."
        )
        # Who keeps model access: roles with their own model grants, plus any models granted to PUBLIC directly
        _direct = current_grants[(current_grants["granted_to"] == "ROLE") & (current_grants["grantee"] != "PUBLIC")]
        _n_roles = _direct["grantee"].nunique()
        _public_kept = sorted(
            current_grants[
                (current_grants["granted_to"] == "ROLE")
                & (current_grants["grantee"] == "PUBLIC")
                & (current_grants["model"] != ALL_MODEL)
            ]["model"].unique()
        )
        _kept = (f"Only **{_n_roles}** role(s) have their own model grants today." if _n_roles
                 else "**No roles have their own model grants today.**")
        if _public_kept:
            _kept += f" PUBLIC keeps the models granted to it directly: {', '.join(f'`{m}`' for m in _public_kept)}."
        st.warning(
            "**Every role without its own model grants loses access to all Cortex models immediately.** "
            "Cortex features that rely on a model, including AI functions, stop working for those roles. "
            "New models are no longer available to everyone either; each one must be granted.\n\n"
            f"{_kept}",
            icon=":material/warning:",
        )
        st.info(
            "**Before you confirm, make sure every role that should have model access has been granted it.**\n\n"
            "1. On **Model Access**, grant each role the models it needs. Its current grants are listed there.\n"
            "2. On **Feature Access**, check those roles also have the Cortex features they need.\n"
            "3. Right after revoking, use **Verify Role** to test a few of those roles (not ACCOUNTADMIN). "
            "Until then it shows every role can use every model, because ALL is still on PUBLIC.",
            icon=":material/checklist:",
        )
        for s in revoke_all_sql:
            st.code(s, language="sql")
        if confirm_buttons("confirm_revoke_all"):
            _run_remediation(revoke_all_sql, invalidate=[load_public_grants, load_current_grants, load_role_model_access])

    @st.dialog("Grant ALL to PUBLIC", width="large", position="right", icon=":material/lock_open:")
    def _confirm_grant_all():
        """Drawer for granting ``CORTEX-MODEL-ROLE-ALL`` back to PUBLIC, Snowflake's default.

        Explains why this is recommended and that model access is paired with Cortex roles. On Confirm, runs
        ``CALL SNOWFLAKE.LOCAL.GRANT_TO_PUBLIC_APPLICATION_ROLE``.
        """
        st.markdown(
            "This restores Snowflake's default grant of `CORTEX-MODEL-ROLE-ALL` through the "
            "`SNOWFLAKE.PUBLIC` application role, making every Cortex model, current and future, "
            "accessible to all roles in the account."
        )
        st.success(
            "**Tip: granting ALL to PUBLIC is recommended.** Model availability changes quickly: Snowflake adds, "
            "updates and retires models often. With ALL on PUBLIC, new models work as soon as they're released, "
            "with no per-model grants to keep up to date. It's also Snowflake's default for new accounts. "
            "Restrict models one by one only when you have a specific reason, such as legal or compliance "
            "requirements.",
            icon=":material/lightbulb:",
        )
        st.info(
            "**Model access alone isn't enough.** A role can only use a model if it also has the Cortex role for "
            "the feature calling it: `CORTEX_USER`, or a narrower role such as `CORTEX_AGENT_USER` or "
            "`AI_FUNCTIONS_USER`. AI functions also need the `USE AI FUNCTIONS` privilege. Manage these on the "
            "**Feature Access** page.",
            icon=":material/info:",
        )
        st.markdown(
            "**In short:** think of two locks.\n\n"
            "- **Model grants** (this setting) decide *which models* can be used.\n"
            "- **Cortex roles** decide *who* can use a Cortex feature at all.\n\n"
            "A user needs both. Granting ALL keeps the first lock open for every model, so you control access "
            "in one place: the Cortex roles."
        )
        st.code(GRANT_ALL_SQL, language="sql")
        if confirm_buttons("confirm_grant_all"):
            _run_remediation([GRANT_ALL_SQL], invalidate=[load_public_grants, load_current_grants, load_role_model_access])

    with _check(
        "CORTEX-MODEL-ROLE-ALL",
        f"{'On' if all_is_public else 'Not on'} PUBLIC ({'default' if all_is_public else 'not the default'})",
        "info",
        non_default=not all_is_public,
    ):
        if all_is_public:
            paths = []
            if all_via_app_role:
                paths.append("the `SNOWFLAKE.PUBLIC` application role (Snowflake default)")
            if all_via_account_role:
                paths.append("a direct grant to the `PUBLIC` account role")
            st.info(
                f"All models are accessible to every role via {' and '.join(paths)}. "
                "If you want per-role control, you can revoke this.",
                icon=":material/info:",
            )
            st.button(
                ":material/lock: Revoke ALL from PUBLIC",
                key="btn_revoke_all",
                on_click=_confirm_revoke_all,
                type="primary",
            )
        else:
            st.info(
                "`CORTEX-MODEL-ROLE-ALL` is not granted to PUBLIC — "
                "model access is restricted to explicit grants.",
                icon=":material/info:",
            )
            st.button(
                ":material/lock_open: Grant ALL to PUBLIC",
                key="btn_grant_all",
                on_click=_confirm_grant_all,
                type="secondary",
            )

    # Individual PUBLIC model grants
    public_individual = current_grants[
        (current_grants["granted_to"] == "ROLE")
        & (current_grants["grantee"] == "PUBLIC")
        & (current_grants["model"] != "ALL")
    ]

    _pub_models = sorted(public_individual["model"].unique()) if not public_individual.empty else []
    _granted = {m.upper() for m in _pub_models}

    @st.dialog("Apply changes to PUBLIC", width="large", position="right", icon=":material/rule:")
    def _confirm_pub_changes(to_grant, to_revoke):
        """Drawer for applying pending changes from the "Granted to PUBLIC" table.

        On Confirm, grants and revokes the model roles to and from the PUBLIC account role, in parallel, and
        clears the pending edits.

        Args:
            to_grant: Model names to grant to PUBLIC.
            to_revoke: Model names to revoke from PUBLIC.
        """
        if to_grant:
            st.markdown(f"**Grant {len(to_grant)}** model(s) to PUBLIC, so every role can use them: "
                        f"{', '.join(f'`{m}`' for m in to_grant)}.")
        if to_revoke:
            st.markdown(f"**Revoke {len(to_revoke)}** model(s) from PUBLIC: "
                        f"{', '.join(f'`{m}`' for m in to_revoke)}.")
        if all_is_public:
            st.info(
                "`CORTEX-MODEL-ROLE-ALL` is on PUBLIC, so every role can already use every model. These changes "
                "only take effect once ALL is revoked. Then they decide which models stay available to everyone.",
                icon=":material/info:",
            )
        elif to_revoke:
            st.warning(
                "Roles that don't have their own grant for the revoked models lose access to them immediately.",
                icon=":material/warning:",
            )
        pub_sql = ([f"GRANT APPLICATION ROLE {app_role(m)} TO ROLE PUBLIC" for m in to_grant]
                   + [f"REVOKE APPLICATION ROLE {app_role(m)} FROM ROLE PUBLIC" for m in to_revoke])
        st.code(";\n".join(pub_sql) + ";", language="sql")
        if confirm_buttons("confirm_pub_changes"):
            # Pending edits are applied, so start fresh once the grants reload
            st.session_state.pop("pub_desired", None)
            _run_remediation(pub_sql, invalidate=[load_public_grants, load_current_grants, load_role_model_access],
                             parallel=True)

    def _reset_pub_changes():
        """Discards pending edits in the "Granted to PUBLIC" table.

        Clears ``pub_desired`` from session state and bumps ``pub_editor_reset``, which changes the data
        editor's key so Streamlit drops its own edit state too.
        """
        st.session_state.pop("pub_desired", None)
        # A new editor key drops the editor's own edit state too, so the checkboxes go back to the real grants
        st.session_state["pub_editor_reset"] = st.session_state.get("pub_editor_reset", 0) + 1

    def _set_all_pub(models, want, granted):
        """Checks or unchecks every listed model in the "Granted to PUBLIC" table, as pending changes.

        Nothing runs until **Apply**. Models already in the wanted state have no pending change. Bumps
        ``pub_editor_reset`` so the data editor rebuilds from the updated pending changes.

        Args:
            models: Model names to change, i.e. the rows currently shown.
            want: True to check them all, False to uncheck them all.
            granted: Upper-case model names PUBLIC currently holds.
        """
        desired = st.session_state.setdefault("pub_desired", {})
        for m in models:
            if want == (m.upper() in granted):
                desired.pop(m, None)
            else:
                desired[m] = want
        st.session_state["pub_editor_reset"] = st.session_state.get("pub_editor_reset", 0) + 1

    with _check(
        "Individual model grants to PUBLIC",
        f"{len(_pub_models)} model(s)" if _pub_models else "None",
        "info" if _pub_models else "success",
        non_default=bool(_pub_models),
    ):
        per_model_grants_tip()
        if not _pub_models:
            st.success(
                "No individual model grants to PUBLIC (grants of `CORTEX-MODEL-ROLE-ALL` are covered above).",
                icon=":material/check_circle:",
            )
        st.markdown("Every role can use the checked models through PUBLIC. Check or uncheck models, then **Apply**.")

        _eol_map = d["eol_map"]
        _status = {m: status_map.get(m.upper(), "No status") for m in available_models if m.upper() != ALL_MODEL}
        # End-of-life models are only listed when PUBLIC already has them, so they can be revoked but not granted
        _models = [m for m in _status if _status[m] != "EOL" or m.upper() in _granted]
        # Lifecycle order (GA, PUPR, PRPR, LEGACY, INTERNAL, EOL), then models with no status; by name within each
        _models.sort(key=lambda m: (STATUS_ORDER.get(_status[m], 99), m))

        # Pending checkbox changes, kept in session state so they survive filtering. model -> wanted on PUBLIC
        _desired = st.session_state.setdefault("pub_desired", {})

        # Filters: name search, lifecycle (only worth showing when there's more than one status) and availability.
        # Unapplied changes to models they hide are kept.
        _routing = cross_region_routing(d)
        _available = {m: model_availability(m, d, _routing) for m in _models}
        _present = sorted({_status[m] for m in _models}, key=lambda s: STATUS_ORDER.get(s, 99))
        _col_search, _col_lifecycle = st.columns([1, 2])
        with _col_search:
            _search = st.text_input("Search models", placeholder="e.g. llama, claude...", key="pub_search")
        _shown = []
        if len(_present) > 1:
            with _col_lifecycle:
                _shown = st.pills(
                    "Lifecycle",
                    _present,
                    selection_mode="multi",
                    format_func=lambda s: f"{STATUS_EMOJI.get(s, '')} {s}".strip(),
                    key="pub_lifecycle",
                    help="Show only these statuses. Select none to show all. Unapplied changes are kept.",
                )
        _only_available = st.toggle(
            "Only models available to this account",
            key="pub_available",
            help=f"In region in {d['account_region']}, or reachable with the current cross-region setting "
                 f"({d['cross_region_value']}). Unapplied changes are kept.",
        )
        _rows = [
            m for m in _models
            if (not _search or _search.lower() in m.lower())
            and (not _shown or _status[m] in _shown)
            and (not _only_available or _available[m] != "Not available")
        ]
        _filtered = len(_rows) < len(_models)

        _pub_table = pd.DataFrame({
            "Granted to PUBLIC": [_desired.get(m, m.upper() in _granted) for m in _rows],
            "Model": _rows,
            "Lifecycle": [f"{STATUS_EMOJI.get(_status[m], '')} {_status[m]}".strip() for m in _rows],
            "Available here": [_available[m] for m in _rows],
            "End-of-life date": [_eol_map.get(m.upper(), "") for m in _rows],
        })
        if not _pub_table["End-of-life date"].any():
            _pub_table = _pub_table.drop(columns="End-of-life date")
        # Bulk toggles for the rows shown, so they respect the filters
        _checked = _pub_table["Granted to PUBLIC"]
        _scope = "shown models" if _filtered else "models"
        with st.container(horizontal=True, vertical_alignment="center"):
            st.button(
                ":material/check_box: Check all",
                key="btn_pub_check_all",
                on_click=_set_all_pub,
                args=(_rows, True, _granted),
                disabled=_pub_table.empty or bool(_checked.all()),
                help=f"Check all {_scope}. Nothing changes until you click **Apply**.",
            )
            st.button(
                ":material/check_box_outline_blank: Uncheck all",
                key="btn_pub_uncheck_all",
                on_click=_set_all_pub,
                args=(_rows, False, _granted),
                disabled=_pub_table.empty or not bool(_checked.any()),
                help=f"Uncheck all {_scope}. Nothing changes until you click **Apply**.",
            )
            if _filtered:
                st.caption(f"Showing {len(_rows)} of {len(_models)} models.")
        # Key on the rows shown, the current grants and resets, so the editor rebuilds from the pending changes
        # when the filters change, from fresh grants after Apply, and from the real grants after Reset
        _key = zlib.crc32(("|".join(_rows) + "#" + "|".join(sorted(_granted))
                           + f"#{st.session_state.get('pub_editor_reset', 0)}").encode())
        _edited = st.data_editor(
            _pub_table,
            hide_index=True,
            width="stretch",
            disabled=[c for c in _pub_table.columns if c != "Granted to PUBLIC"],
            column_config={
                "Granted to PUBLIC": st.column_config.CheckboxColumn("Granted to PUBLIC", width="small"),
                "Available here": st.column_config.TextColumn(
                    help=f"Based on this account's region ({d['account_region']}) and its cross-region setting "
                         f"({d['cross_region_value']}). Models with no lifecycle status are assumed available.",
                ),
            },
            key=f"pub_editor_{_key}",
        )
        for m, want in zip(_edited["Model"], _edited["Granted to PUBLIC"]):
            want = bool(want)
            if want == (m.upper() in _granted):
                _desired.pop(m, None)
            else:
                _desired[m] = want

        _to_grant = sorted(m for m, want in _desired.items() if want)
        _to_revoke = sorted(m for m, want in _desired.items() if not want)
        _n = len(_to_grant) + len(_to_revoke)
        if _n:
            st.caption(f"Pending: {len(_to_grant)} to grant, {len(_to_revoke)} to revoke.")
        if any(_status[m] == "EOL" for m in _models):
            st.caption("End-of-life models are only listed when PUBLIC already has them.")
        with st.container(horizontal=True):
            st.button(
                f":material/check: Apply ({_n} change{'' if _n == 1 else 's'})",
                key="btn_pub_apply",
                type="primary",
                on_click=_confirm_pub_changes,
                args=(_to_grant, _to_revoke),
                disabled=not _n,
            )
            st.button(":material/undo: Reset", key="btn_pub_reset", on_click=_reset_pub_changes, disabled=not _n)


    # EOL models in use
    eol_models_list = [m for m in available_models if status_map.get(m) == "EOL"]
    # ACCOUNTADMIN always has access to every model, so its grants aren't actionable here
    eol_in_use = current_grants[
        current_grants["model"].isin(eol_models_list)
        & ~(
            (current_grants["granted_to"] == "ROLE")
            & (current_grants["grantee"].str.upper() == "ACCOUNTADMIN")
        )
    ]

    @st.dialog("Revoke all EOL model grants", width="large", position="right", icon=":material/healing:")
    def _confirm_revoke_eol():
        """Drawer for revoking every grant of an end-of-life model. Runs the revokes in parallel on Confirm."""
        st.markdown(f"This will revoke **{len(revoke_eol_sql)}** grant(s) for EOL models:")
        for s in revoke_eol_sql:
            st.code(s, language="sql")
        if confirm_buttons("confirm_eol"):
            _run_remediation(revoke_eol_sql, invalidate=[load_current_grants, load_role_model_access], parallel=True)

    with _check(
        "EOL model grants",
        f"{len(eol_in_use)} grant(s) to end-of-life models" if not eol_in_use.empty else "None",
        "warning" if not eol_in_use.empty else "success",
    ):
        if not eol_in_use.empty:
            eol_names = sorted(eol_in_use["model"].unique())
            eol_grant_details = eol_in_use.groupby("model")["grantee"].apply(lambda s: ", ".join(sorted(set(s)))).reset_index()
            st.warning(
                f"Grants exist for end-of-life models: **{', '.join(eol_names)}**. "
                "Consider revoking these grants and migrating to supported models.",
                icon=":material/warning:",
            )
            st.dataframe(eol_grant_details.rename(columns={"grantee": "Grantees", "model": "Model"}),
                         width="stretch", hide_index=True)

            revoke_eol_sql = []
            for _, row in eol_in_use.iterrows():
                revoke_eol_sql.append(
                    f'REVOKE APPLICATION ROLE {app_role(row["model"])} FROM ROLE "{row["grantee"]}"'
                )
            st.button(":material/healing: Revoke EOL grants", key="btn_eol", on_click=_confirm_revoke_eol)
        else:
            st.success("No grants to end-of-life models.", icon=":material/check_circle:")

    st.page_link(
        "app_pages/model_access.py",
        label="Grant models to specific roles on the Model Access page, which also lists every model grant",
        icon=":material/arrow_forward:",
    )


with _group(
    "Feature access",
    "Which Cortex features roles can use, through the `SNOWFLAKE` database roles and the account privileges "
    "`USE AI FUNCTIONS` and `EXECUTE AGENT TASK`. Each check shows whether it's on PUBLIC, which every role "
    "inherits, and whether that's the default.",
):
    # Cortex access through PUBLIC: one card per grant, each with its own fix
    cortex_grants, cortex_missing = load_cortex_role_grants()
    _role_rows = cortex_grants[cortex_grants["granted_to"] == "ROLE"]
    public_holds = set(_role_rows[_role_rows["grantee"] == "PUBLIC"]["role"])


    def _direct_holders(name):
        """Lists the account roles that hold a Cortex role or privilege directly.

        PUBLIC and ACCOUNTADMIN are excluded, so the list shows which other roles keep access after a revoke
        from PUBLIC.

        Args:
            name: Role or privilege name, e.g. ``"CORTEX_USER"``.

        Returns:
            Sorted list of upper-case role names.
        """
        g = _role_rows[_role_rows["role"] == name]["grantee"].str.upper()
        return sorted(set(g) - {"PUBLIC", "ACCOUNTADMIN"})


    def _public_access_card(name, kind, default_on_public, on_text, off_text, revoke_effect, grant_effect,
                            explain="", note="", container=None, extra=None):
        """Renders the check for one Cortex role or privilege on PUBLIC, with a grant or revoke drawer.

        Does nothing if the role doesn't exist in this account.

        Args:
            name: Role or privilege name, e.g. ``"CORTEX_USER"`` or ``"USE AI FUNCTIONS"``.
            kind: ``"DATABASE ROLE"`` or ``"PRIVILEGE"``. Passed to ``cortex_role_sql``.
            default_on_public: Whether Snowflake grants it to PUBLIC by default.
            on_text: What being on PUBLIC means, shown when it is.
            off_text: What not being on PUBLIC means, shown when it isn't.
            revoke_effect: Markdown shown in the drawer before revoking from PUBLIC.
            grant_effect: Markdown shown in the drawer before granting to PUBLIC.
            explain: Optional Markdown explanation shown in the check.
            note: Optional warning shown in the check.
            container: Optional container to render the check in, e.g. a column for the narrow roles.
            extra: Optional function called inside the check to render extra content.
        """
        if name in cortex_missing:
            return
        if container is not None:
            with container:
                _public_access_card(name, kind, default_on_public, on_text, off_text, revoke_effect,
                                    grant_effect, explain, note, extra=extra)
            return
        on = name in public_holds
        sql = cortex_role_sql("REVOKE" if on else "GRANT", name, kind, "PUBLIC")
        verb = "Revoke" if on else "Grant"

        def _confirm():
            """Drawer body for granting or revoking this role or privilege on PUBLIC."""
            st.markdown(revoke_effect if on else grant_effect)
            st.code(sql, language="sql")
            if confirm_buttons(f"confirm_public_{name}"):
                _run_remediation([sql], invalidate=[load_cortex_role_grants])

        dialog = st.dialog(
            f"{verb} {name} {'from' if on else 'to'} PUBLIC",
            width="large",
            position="right",
            icon=":material/remove_circle:" if on else ":material/add_circle:",
        )(_confirm)
        is_default = on == default_on_public
        label = "account privilege" if kind == "PRIVILEGE" else "database role"

        with _check(
            name,
            f"{'On' if on else 'Not on'} PUBLIC ({'default' if is_default else 'not the default'})",
            "info",
            non_default=not is_default,
        ):
            st.info(
                f"{label.capitalize()}. " + (on_text if on else off_text),
                icon=":material/badge:",
            )
            if explain:
                st.markdown(explain)
            if extra:
                extra()
            if note:
                st.warning(note, icon=":material/priority_high:")
            if explain or note:
                st.divider()
            st.button(
                f":material/{'lock' if on else 'lock_open'}: {verb} {name} {'from' if on else 'to'} PUBLIC",
                key=f"btn_public_{name}",
                on_click=dialog,
                type="primary" if on else "secondary",
            )


    # CORTEX_USER
    if "CORTEX_AGENT_USER" in public_holds:
        _agent_fallback = ("CoCo, Cortex Agents and Snowflake CoWork stay available to everyone, because PUBLIC "
                           "also holds `CORTEX_AGENT_USER`. Revoke that too to restrict them.")
    else:
        _agent_fallback = "Those roles also lose CoCo, Cortex Agents and Snowflake CoWork."
    _public_access_card(
        "CORTEX_USER", "DATABASE ROLE", default_on_public=True,
        on_text="Every role gets AI functions (together with `USE AI FUNCTIONS`), CoCo, Cortex Agents "
                "and Snowflake CoWork through PUBLIC.",
        off_text="Only roles granted `CORTEX_USER` directly have it.",
        revoke_effect="**Roles without a direct grant lose AI functions** unless they hold `AI_FUNCTIONS_USER`. "
                      f"{_agent_fallback}\n\nTo keep only AI functions for specific roles, grant them "
                      "`AI_FUNCTIONS_USER` on the **Feature Access** page.",
        grant_effect="Every role gets AI functions (together with `USE AI FUNCTIONS`), CoCo, Cortex Agents "
                     "and Snowflake CoWork.",
        explain=(
            "**What it is.** The broad Cortex database role. It covers all of Snowflake's Covered AI Features: "
            "AI functions, Cortex Agents, CoCo, Snowflake CoWork, Cortex Analyst, embeddings and the Cortex REST API.\n\n"
            "**How it relates to the other roles.** It already includes everything the narrower roles grant "
            "(`CORTEX_AGENT_USER`, `AI_FUNCTIONS_USER`, `CORTEX_ANALYST_USER`, `CORTEX_EMBED_USER`, "
            "`CORTEX_REST_API_USER`). "
            "Grants add up, so a role that holds `CORTEX_USER` keeps all of it no matter which narrow roles it has. "
            "To limit a role to one feature, take `CORTEX_USER` away and grant the narrow role instead.\n\n"
            "**What being on PUBLIC means.** Every user and role inherits it. While it's on PUBLIC, revoking it "
            "from a single role or granting narrow roles changes nothing, because everyone already has the full set. "
            "Which *models* a role can use is still decided separately, by model RBAC."
        ),
        note="**For AI functions, `CORTEX_USER` isn't enough on its own:** the role also needs `USE AI FUNCTIONS`.",
    )

    # USE AI FUNCTIONS
    _ai_keep = _direct_holders("USE AI FUNCTIONS")
    _blanket_holders = set(_role_rows[_role_rows["role"] == "USE AI FUNCTIONS"]["grantee"].str.upper())
    _per_fn = _role_rows[_role_rows["kind"] == "FUNCTION PRIVILEGE"]
    # Roles that would keep some AI functions through per-function grants alone
    _per_fn_only = set(_per_fn["grantee"].str.upper()) - set(_ai_keep) - {"PUBLIC", "ACCOUNTADMIN"}


    def _per_function_grants():
        """Renders the per-function ``USE AI FUNCTION <name>`` grants inside the USE AI FUNCTIONS check.

        Notes that the blanket privilege and per-function privileges are an OR, so per-function grants have
        no effect on roles that hold the blanket privilege.
        """
        st.markdown(
            "**Per-function grants.** It can also be granted per function, for example "
            "`USE AI FUNCTION AI_COMPLETE`, to allow a role only some AI functions. The two are an OR: if a role "
            "holds the blanket privilege, per-function grants and revokes have no effect on it."
        )
        if _per_fn.empty:
            st.caption("No per-function grants in this account.")
            return
        rows = []
        for grantee, g in _per_fn.groupby(_per_fn["grantee"].str.upper()):
            if grantee in _blanket_holders:
                effect = "No: overridden, holds USE AI FUNCTIONS directly"
            elif "PUBLIC" in _blanket_holders:
                effect = "No: overridden, USE AI FUNCTIONS is on PUBLIC"
            else:
                effect = "Yes: only these AI functions"
            rows.append({
                "Role": grantee,
                "Functions": ", ".join(sorted(r[len(AI_FUNCTION_PRIVILEGE_PREFIX):] for r in g["role"])),
                "In effect?": effect,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if "PUBLIC" in _blanket_holders:
            st.caption("While `USE AI FUNCTIONS` is on PUBLIC, every per-function grant is overridden.")

    _public_access_card(
        "USE AI FUNCTIONS", "PRIVILEGE", default_on_public=True,
        on_text="Every role holds the AI functions privilege through PUBLIC. Roles still need "
                "`CORTEX_USER` or `AI_FUNCTIONS_USER` to call AI functions.",
        off_text="Only roles granted `USE AI FUNCTIONS` directly can call AI functions.",
        revoke_effect="**AI functions stop working for every role that doesn't hold `USE AI FUNCTIONS` directly**, "
                      "even roles with `CORTEX_USER`.\n\nRoles that keep it: "
                      f"{', '.join(_ai_keep) if _ai_keep else 'none'} (plus ACCOUNTADMIN)."
                      + (f"\n\nRoles with per-function grants keep just those functions: "
                         f"{', '.join(sorted(_per_fn_only))}." if _per_fn_only else ""),
        grant_effect="Every role gets the privilege, so any role with `CORTEX_USER` or `AI_FUNCTIONS_USER` "
                     "can call AI functions.\n\n" + ai_function_model_note(d)
                     + ("" if ai_function_models_covered(d) else
                        " To grant them, grant `USE AI FUNCTIONS` to PUBLIC on the **Feature Access** page instead."),
        explain=(
            "**What it is.** An account privilege, not a role. It's a second requirement for calling AI functions "
            "such as `AI_COMPLETE`, `AI_CLASSIFY` and `AI_EXTRACT`.\n\n"
            "**How it relates to the roles.** Neither includes the other: a role needs this privilege **and** "
            "`CORTEX_USER` or `AI_FUNCTIONS_USER`.\n\n"
            "| Database role | `USE AI FUNCTIONS` | Can call AI functions? |\n"
            "|---|---|---|\n"
            "| Yes | Yes | Yes |\n"
            "| Yes | No | No |\n"
            "| No | Yes | Only `AI_AGG` and `AI_SUMMARIZE_AGG` |\n\n"
            "**What being on PUBLIC means.** Every role meets this requirement, so whether a role can call AI "
            "functions comes down to its database roles. It currently isn't enforced for AI function calls made "
            "inside Snowflake Native Apps."
        ),
        extra=_per_function_grants,
    )

    # EXECUTE AGENT TASK
    _task_keep = _direct_holders("EXECUTE AGENT TASK")
    _public_access_card(
        "EXECUTE AGENT TASK", "PRIVILEGE", default_on_public=True,
        on_text="Every user can create and run CoCo automations through PUBLIC.",
        off_text="Only users with a role that holds `EXECUTE AGENT TASK` directly can create or run CoCo automations.",
        revoke_effect="**Users without a role that holds `EXECUTE AGENT TASK` can no longer create or run CoCo "
                      "automations.**\n\nRoles that keep it: "
                      f"{', '.join(_task_keep) if _task_keep else 'none'} (plus ACCOUNTADMIN).\n\n"
                      "Their existing automations stay, with run history until it expires, and can be resumed if "
                      "access is restored.",
        grant_effect="Every user can create and run CoCo automations, as long as they can use CoCo.",
        explain=(
            "**What it is.** An account privilege for CoCo automations: prompts CoCo runs on a schedule, "
            "unattended, in a Snowflake-managed sandbox. Each automation is stored as an agent task in the "
            "creating user's personal database.\n\n"
            "**How it relates to the roles.** It's separate from the Cortex roles. Users also need the standard "
            "access for the CoCo surface they use, for example `CORTEX_USER` or `CORTEX_AGENT_USER`.\n\n"
            "**Which role a run uses.** Each run uses the creating user's *default* role and default secondary "
            "roles, not the role that was active when the automation was created. Those roles need access to "
            "everything the prompt uses.\n\n"
            "**What being on PUBLIC means.** It's on PUBLIC by default, so every user can create automations. To "
            "limit them to selected roles, revoke it from PUBLIC and grant it to those roles on the "
            "**Feature Access** page.\n\n"
            "**Cost.** During the preview, each run is billed as a standard Snowflake task plus the CoCo tokens "
            "it uses."
        ),
    )

    # Narrow Cortex roles: grouped because they all only matter once CORTEX_USER is off PUBLIC
    NARROW_CORTEX_ROLES = ["CORTEX_AGENT_USER", "AI_FUNCTIONS_USER", "CORTEX_EMBED_USER", "CORTEX_ANALYST_USER",
                           "CORTEX_REST_API_USER"]
    _narrow = None
    if any(r not in cortex_missing for r in NARROW_CORTEX_ROLES):
        _narrow = st.container()
        _narrow.markdown("**Narrow Cortex roles**")
        if "CORTEX_USER" in public_holds:
            _narrow.caption(
                "Each one covers a single feature that `CORTEX_USER` already includes. While `CORTEX_USER` is on "
                "PUBLIC, these change nothing for access. They matter once you revoke it: any still on PUBLIC keeps "
                "that feature open to every role."
            )
        else:
            _narrow.caption(
                "Each one covers a single feature that `CORTEX_USER` already includes. `CORTEX_USER` is off PUBLIC, "
                "so any of these still on PUBLIC keeps that feature open to every role."
            )

    # CORTEX_AGENT_USER
    if "CORTEX_USER" in public_holds:
        _agent_revoke = ("**No change in access:** CoCo, Cortex Agents and Snowflake CoWork stay available to "
                         "everyone, because PUBLIC also holds `CORTEX_USER`.")
        _agent_on = ("Every role gets Cortex Agents and agentic surfaces, including CoCo and Snowflake CoWork, "
                     "through PUBLIC. This adds nothing right now, because PUBLIC also holds `CORTEX_USER`.")
    else:
        _agent_revoke = ("Roles without a direct grant lose Cortex Agents and agentic surfaces, including CoCo "
                         "and Snowflake CoWork.")
        _agent_on = ("Every role gets Cortex Agents and agentic surfaces, including CoCo and Snowflake CoWork, "
                     "through PUBLIC.")
    _public_access_card(
        "CORTEX_AGENT_USER", "DATABASE ROLE", default_on_public=False,
        container=_narrow,
        on_text=_agent_on,
        off_text="Only roles granted `CORTEX_AGENT_USER` (or `CORTEX_USER`) directly have Cortex Agents.",
        revoke_effect=_agent_revoke,
        grant_effect="Every role gets Cortex Agents and agentic surfaces, including CoCo and Snowflake CoWork.",
        explain=(
            "**What it is.** A narrow database role for Cortex Agents and the agent-based products built on them, "
            "including CoCo and Snowflake CoWork. It doesn't cover AI functions, Cortex Analyst or the "
            "other Covered AI Features.\n\n"
            "**How it relates to the other roles.** It's a subset of `CORTEX_USER`: anything it grants, "
            "`CORTEX_USER` grants too. It only makes a difference for roles that *don't* hold `CORTEX_USER`, "
            "which is how you give a role agents without AI functions. A role that holds both keeps everything "
            "`CORTEX_USER` gives.\n\n"
            "**What being on PUBLIC means.** It isn't on PUBLIC by default. If it is, every role keeps agents and "
            "CoCo even after you revoke `CORTEX_USER` from PUBLIC. Agent calls also run with each user's "
            "*default* role, so that role needs the grant."
        ),
    )

    # AI_FUNCTIONS_USER
    if "CORTEX_USER" in public_holds:
        _aif_on = ("Every role holds it through PUBLIC. This adds nothing right now, because PUBLIC also holds "
                   "`CORTEX_USER`, which includes AI functions.")
        _aif_revoke = ("**No change in access:** everyone keeps AI functions, because PUBLIC also holds "
                       "`CORTEX_USER`.")
    else:
        _aif_on = ("Every role meets the database-role requirement for AI functions through PUBLIC, "
                   "even without `CORTEX_USER`.")
        _aif_revoke = ("Roles without a direct grant of `AI_FUNCTIONS_USER` or `CORTEX_USER` lose AI functions, "
                       "except `AI_AGG` and `AI_SUMMARIZE_AGG`.")
    if "USE AI FUNCTIONS" not in public_holds:
        _aif_revoke += " (`USE AI FUNCTIONS` is also off PUBLIC, so roles need it granted directly as well.)"
    _public_access_card(
        "AI_FUNCTIONS_USER", "DATABASE ROLE", default_on_public=False,
        container=_narrow,
        on_text=_aif_on,
        off_text="Only roles granted `AI_FUNCTIONS_USER` (or `CORTEX_USER`) directly meet the database-role "
                 "requirement for AI functions.",
        revoke_effect=_aif_revoke,
        grant_effect="Every role can call AI functions (together with `USE AI FUNCTIONS`), without getting CoCo, "
                     "Cortex Agents or the rest of Cortex.",
        explain=(
            "**What it is.** A narrow database role for Cortex AI functions only, such as `AI_COMPLETE`, "
            "`AI_CLASSIFY` and `AI_EXTRACT`, without CoCo, Cortex Agents or the other Covered AI Features.\n\n"
            "**How it relates to the other roles.** It's a subset of `CORTEX_USER`: a role needs one of the two "
            "to call AI functions. It only makes a difference for roles that *don't* hold `CORTEX_USER`, which is "
            "how you keep AI functions for a role after taking `CORTEX_USER` away.\n\n"
            "**What being on PUBLIC means.** If it's on PUBLIC, every role can call AI functions even after you "
            "revoke `CORTEX_USER` from PUBLIC, while CoCo and agents stay restricted."
        ),
        note="**`AI_FUNCTIONS_USER` isn't enough on its own:** the role also needs `USE AI FUNCTIONS`.",
    )

    # CORTEX_EMBED_USER
    if "CORTEX_USER" in public_holds:
        _embed_on = ("Every role can call embedding functions through PUBLIC. This adds nothing right now, "
                     "because PUBLIC also holds `CORTEX_USER`, which includes embeddings.")
        _embed_revoke = ("**No change in access:** everyone keeps embedding functions, because PUBLIC also holds "
                         "`CORTEX_USER`.")
    else:
        _embed_on = "Every role can call embedding functions through PUBLIC."
        _embed_revoke = "Roles without a direct grant of `CORTEX_EMBED_USER` or `CORTEX_USER` lose embedding functions."
    _public_access_card(
        "CORTEX_EMBED_USER", "DATABASE ROLE", default_on_public=False,
        container=_narrow,
        on_text=_embed_on,
        off_text="Only roles granted `CORTEX_EMBED_USER` (or `CORTEX_USER`) directly can call embedding functions.",
        revoke_effect=_embed_revoke,
        grant_effect="Every role can call embedding functions, without getting the rest of Cortex.",
        explain=(
            "**What it is.** A narrow database role for embedding functions only, such as `EMBED_TEXT_768` and "
            "`EMBED_TEXT_1024`, without access to the other Cortex features.\n\n"
            "**How it relates to the other roles.** It's a subset of `CORTEX_USER`, which already includes "
            "embeddings. It only makes a difference for roles that *don't* hold `CORTEX_USER`, for example a "
            "pipeline role that only builds vector embeddings.\n\n"
            "**What being on PUBLIC means.** It isn't on PUBLIC by default. If it is, every role can call "
            "embedding functions even after you revoke `CORTEX_USER` from PUBLIC."
        ),
        note="**Embedding models also need model access.** Once the 2026_07 behavior change bundle is enabled, model "
             "RBAC applies to embedding models too (`AI_EMBED`, `AI_MULTI_EMBED`, `AI_SIMILARITY`, `EMBED_TEXT_*`). "
             "The role then also needs the embedding model's application role, or `CORTEX-MODEL-ROLE-ALL`.",
    )

    # CORTEX_ANALYST_USER
    if "CORTEX_USER" in public_holds:
        _analyst_on = ("Every role can use Cortex Analyst through PUBLIC. This adds nothing right now, "
                       "because PUBLIC also holds `CORTEX_USER`, which includes Cortex Analyst.")
        _analyst_revoke = ("**No change in access:** everyone keeps Cortex Analyst, because PUBLIC also holds "
                           "`CORTEX_USER`.")
    else:
        _analyst_on = "Every role can use Cortex Analyst through PUBLIC."
        _analyst_revoke = "Roles without a direct grant of `CORTEX_ANALYST_USER` or `CORTEX_USER` lose Cortex Analyst."
    _public_access_card(
        "CORTEX_ANALYST_USER", "DATABASE ROLE", default_on_public=False,
        container=_narrow,
        on_text=_analyst_on,
        off_text="Only roles granted `CORTEX_ANALYST_USER` (or `CORTEX_USER`) directly can use Cortex Analyst.",
        revoke_effect=_analyst_revoke,
        grant_effect="Every role can use Cortex Analyst, without getting the rest of Cortex.",
        explain=(
            "**What it is.** A narrow database role for Cortex Analyst only: natural-language questions answered "
            "with SQL generated from a semantic view or semantic model.\n\n"
            "**How it relates to the other roles.** It's a subset of `CORTEX_USER`, which already includes "
            "Cortex Analyst. It only makes a difference for roles that *don't* hold `CORTEX_USER`.\n\n"
            "**What being on PUBLIC means.** If it's on PUBLIC, every role can use Cortex Analyst even after you "
            "revoke `CORTEX_USER` from PUBLIC. The role still needs `SELECT` on the tables in the semantic view, "
            "and `USAGE` on any Cortex Search services it references, so the data itself stays protected by "
            "normal RBAC."
        ),
        note="**Snowflake recommends moving to Cortex Agents,** which support every Cortex Analyst capability. "
             "Agents are covered by `CORTEX_AGENT_USER`, not this role.",
    )

    # CORTEX_REST_API_USER
    if "CORTEX_USER" in public_holds:
        _rest_on = ("Every role can call the Cortex REST API through PUBLIC. This adds nothing right now, "
                    "because PUBLIC also holds `CORTEX_USER`, which includes the REST API.")
        _rest_revoke = ("**No change in access:** everyone keeps the Cortex REST API, because PUBLIC also holds "
                        "`CORTEX_USER`.")
    else:
        _rest_on = "Every role can call the Cortex REST API through PUBLIC."
        _rest_revoke = "Roles without a direct grant of `CORTEX_REST_API_USER` or `CORTEX_USER` lose the Cortex REST API."
    _public_access_card(
        "CORTEX_REST_API_USER", "DATABASE ROLE", default_on_public=False,
        container=_narrow,
        on_text=_rest_on,
        off_text="Only roles granted `CORTEX_REST_API_USER` (or `CORTEX_USER`) directly can call the Cortex REST API.",
        revoke_effect=_rest_revoke,
        grant_effect="Every role can call the Cortex REST API, without getting the rest of Cortex.",
        explain=(
            "**What it is.** A narrow database role for the Cortex REST API only: the OpenAI-compatible Chat "
            "Completions endpoint and the Anthropic-compatible Messages endpoint. It doesn't cover AI functions, "
            "Cortex Agents, Cortex Analyst, Cortex Fine-tuning or Cortex Search.\n\n"
            "**How it relates to the other roles.** It's a subset of `CORTEX_USER`, which already includes the "
            "REST API. It only makes a difference for roles that *don't* hold `CORTEX_USER`, for example a "
            "dedicated service user for an application that calls models over HTTP.\n\n"
            "**What being on PUBLIC means.** It isn't on PUBLIC by default. If it is, every user with a token can "
            "call the REST API even after you revoke `CORTEX_USER` from PUBLIC. REST API requests also run with "
            "the user's *default* role, so that role needs the grant. Which models they can call is still "
            "decided by model RBAC."
        ),
        note="**The vector embedding endpoint (`/api/v2/cortex/inference:embed`) is documented as needing "
             "`CORTEX_USER`,** not `CORTEX_REST_API_USER`.",
    )

    if "COPILOT_USER" not in public_holds and "COPILOT_USER" not in cortex_missing:
        with _check("COPILOT_USER", "Revoked from PUBLIC (no longer restricts CoCo)", "warning"):
            st.warning(
                "Starting the week of October 19, 2026, `COPILOT_USER` no longer controls CoCo in Snowsight, "
                "so this revoke won't keep CoCo off. "
                "To restrict CoCo, revoke `CORTEX_USER` and `CORTEX_AGENT_USER` from PUBLIC and grant them to "
                "specific roles, or set `CORTEX_CODE_SNOWSIGHT_DAILY_EST_CREDIT_LIMIT_PER_USER` to 0.",
                icon=":material/warning:",
            )

    st.page_link(
        "app_pages/feature_access.py",
        label="Grant Cortex roles and privileges to specific roles on the Feature Access page",
        icon=":material/arrow_forward:",
    )
