"""Shared data access, SQL helpers and UI helpers for the AI Access Manager app.

Every page imports from this module. It reads Cortex access configuration from Snowflake with SHOW and
SELECT statements, caches the results with ``st.cache_data``, and provides helpers that build and run
GRANT, REVOKE and ALTER statements.

Caching:
    Grant and model loaders are slow (one query per model role), so they use ``GRANTS_TTL``. Parameter
    loaders use shorter TTLs. Pages clear the relevant caches after their own changes, and the sidebar's
    Refresh data button calls ``clear_all_caches``.

Threading:
    Several loaders fan out queries on a ``ThreadPoolExecutor``. Functions that run on worker threads
    must not call ``st.*``: Streamlit's script context only exists on the main thread. Their docstrings
    say so.
"""

import os
import re
import json
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
import pandas as pd

PREFIX = "CORTEX-MODEL-ROLE-"
# CORTEX-MODEL-ROLE-ALL isn't a model with a lifecycle status, but grants access to every model
ALL_MODEL = "ALL"

# Lifecycle statuses in display order. Pages sort models without a status last (99).
STATUS_ORDER = {"GA": 1, "PUPR": 2, "PRPR": 3, "LEGACY": 4, "INTERNAL": 5, "EOL": 6}
STATUS_EMOJI = {
    "GA": "\u2705", "PUPR": "\U0001F7E1", "PRPR": "\U0001F512",
    "LEGACY": "\u26A0\uFE0F", "INTERNAL": "\U0001F3E0", "EOL": "\u274C",
}

# Thread pool size for loaders that run one query per model role, role or user
MAX_WORKERS = 8
# Grant and model loaders are slow (one query per model role), so they're cached longer. The app clears
# them after its own changes; changes made outside the app show up after this, or on Refresh data.
GRANTS_TTL = "15m"


def app_role(model):
    """Returns the fully qualified model application role for a model.

    Args:
        model: Model name, e.g. ``claude-sonnet-4-5`` or ``ALL``. Case doesn't matter.

    Returns:
        The role identifier, e.g. ``SNOWFLAKE."CORTEX-MODEL-ROLE-CLAUDE-SONNET-4-5"``.
    """
    return f'SNOWFLAKE."{PREFIX}{model.upper()}"'


def model_from_grant_name(name):
    """Extracts the model name from a grant's object name.

    Args:
        name: Object name from SHOW GRANTS, e.g. ``SNOWFLAKE."CORTEX-MODEL-ROLE-X"``.

    Returns:
        The upper-case model name (``X``), or an empty string if ``name`` isn't a model role.
    """
    name = str(name)
    i = name.upper().find(PREFIX)
    return name[i + len(PREFIX):].strip('"').upper() if i >= 0 else ""


def get_conn():
    """Returns the app's Snowflake connection.

    ``SNOWFLAKE_CONNECTION_TTL`` (an environment variable) controls how long Streamlit reuses it.

    Returns:
        The ``st.connection("snowflake")`` connection object.
    """
    return st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))


def get_session():
    """Returns the Snowpark session behind the app's connection.

    Returns:
        A ``snowflake.snowpark.Session``.
    """
    return get_conn().session()


def q(sql):
    """Runs a SQL statement and returns its rows as a DataFrame.

    Args:
        sql: The statement to run, usually SHOW or SELECT.

    Returns:
        A ``pandas.DataFrame`` with one row per result row, using Snowflake's column names. Empty if
        the statement returns no rows.

    Raises:
        snowflake.snowpark.exceptions.SnowparkSQLException: If the statement fails.
    """
    return pd.DataFrame([r.as_dict() for r in get_session().sql(sql).collect()])


def run_statements(statements, parallel=False):
    """Runs SQL statements and reports the ones that failed.

    Every statement runs even if others fail. With ``parallel=True`` they run concurrently on a thread
    pool, so only use it for statements that don't depend on each other, such as grants to different
    roles or models.

    Args:
        statements: Iterable of SQL statements.
        parallel: Run the statements concurrently instead of one after another.

    Returns:
        A list of ``(sql, error_message)`` tuples for the statements that failed, in input order.
        Empty when all succeed.
    """
    session = get_session()

    def _one(sql):
        """Runs one statement, returning ``(sql, None)`` on success or ``(sql, error_message)`` on failure."""
        try:
            session.sql(sql).collect()
            return sql, None
        except Exception as e:
            return sql, str(e)

    statements = list(statements)
    if parallel and len(statements) > 1:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(statements))) as pool:
            results = list(pool.map(_one, statements))
    else:
        results = [_one(s) for s in statements]
    return [(sql, err) for sql, err in results if err is not None]


def confirm_buttons(key, disabled=False):
    """Shows Confirm and Cancel buttons side by side inside a dialog.

    Cancel closes the dialog by rerunning the app, so users don't depend on the dialog's X, which a host
    toolbar can cover.

    Args:
        key: Widget key for the Confirm button. Cancel uses ``f"{key}_cancel"``.
        disabled: Disable the Confirm button, e.g. until the user makes a selection.

    Returns:
        True on the run where Confirm was clicked, otherwise False.
    """
    with st.container(horizontal=True):
        confirmed = st.button("Confirm", type="primary", key=key, disabled=disabled)
        if st.button("Cancel", key=f"{key}_cancel"):
            st.rerun()
    return confirmed


def mark_stale():
    """Flags that data must be reloaded, so ``load_all`` shows its progress toasts on the next run."""
    st.session_state["_data_loaded"] = False


# Substrings (lower-case) of Snowflake errors caused by the app's role lacking a privilege
_PRIVILEGE_ERROR_MARKERS = (
    "insufficient privileges",
    "not authorized",
    "access control error",
    "does not have privilege",
)
_ERROR_CODE_PREFIX = re.compile(r"^(\(\d+\):\s*)?([0-9a-f-]{20,}:\s*)?(\d{6}\s*\([0-9A-Z]+\):\s*)?")


def describe_error(e):
    """Turns an exception into a short summary and a hint on what to do about it.

    Privilege errors get a hint about the app's owner role, since the app runs with owner's rights and
    needs ACCOUNTADMIN. Doesn't call ``st.*``, so it's safe on worker threads.

    Args:
        e: The exception, usually a ``SnowparkSQLException``.

    Returns:
        A tuple ``(summary, hint)``. ``summary`` is a one-line version of the error without Snowflake's
        numeric prefixes; ``hint`` is Markdown.
    """
    message = str(getattr(e, "message", None) or e).strip()
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    summary = lines[0] if lines else type(e).__name__
    # Snowflake puts "SQL compilation error:" on the first line and the useful part on the next
    if len(lines) > 1 and summary.rstrip(":").lower().endswith("error"):
        summary = f"{summary} {lines[1]}"
    # Drop Snowpark's "(1304): <query id>: 002003 (42S02): " prefix
    summary = _ERROR_CODE_PREFIX.sub("", summary) or summary
    if any(marker in message.lower() for marker in _PRIVILEGE_ERROR_MARKERS):
        hint = (
            "This looks like a **privilege error**. The app runs with its owner's rights, so the Streamlit "
            "app must be owned by `ACCOUNTADMIN`. Re-run `install.ipynb` as `ACCOUNTADMIN` to reinstall it. "
            "If the message says an object *does not exist or not authorized*, the object may also be "
            "missing in this account."
        )
    else:
        hint = ("This may be temporary. Try the sidebar's **Refresh data**; if it keeps failing, "
                "check the error details.")
    return summary, hint


def show_error(title, e, retry_key=None):
    """Shows a friendly error with a hint, the full error in an expander, and an optional Retry button.

    Args:
        title: What failed, e.g. ``"This page couldn't load"``.
        e: The exception.
        retry_key: Widget key for a Retry button that clears every cache and reruns. No button if None.
    """
    summary, hint = describe_error(e)
    st.error(f"**{title}.** {summary}\n\n{hint}", icon=":material/error:")
    with st.expander("Error details"):
        st.code(f"{type(e).__name__}: {e}", language="text")
    if retry_key:
        st.button(":material/refresh: Retry", key=retry_key, on_click=clear_all_caches)


# Allowlist migration and model RBAC (BCR-2378)
BCR_2378_URL = "https://docs.snowflake.com/en/release-notes/bcr-bundles/un-bundled/bcr-2378"
# Current docs page for model access control (model application roles and the default ALL grant)
MODEL_RBAC_DOCS_URL = "https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-privileges-and-access"


def per_model_grants_tip():
    """Shows the tip that individual model grants are only for specific requirements.

    Used on the Home page and the Model Access page, so both show the same advice and sources.
    """
    st.info(
        "**Tip: only restrict models one by one when you need to.** `CORTEX-MODEL-ROLE-ALL` on PUBLIC is "
        "Snowflake's default for new accounts, and it also covers models Snowflake releases later.\n\n"
        "- Even with every model granted, **a user can't use a model unless they also have access to a Cortex "
        "feature that uses it**, such as AI functions or Cortex Agents (see **Feature Access**).\n"
        "- Restricting a model also restricts the Cortex features that use it, for example AI functions, and "
        "Cortex Search for embedding models.\n"
        "- With per-model grants, each new model Snowflake releases has to be granted before anyone can use it.\n"
        "- Use per-model grants when you have a specific reason to limit which models roles can use, such as "
        "legal or compliance requirements. Only revoke models for this reason.\n\n"
        f"Sources: [BCR-2378]({BCR_2378_URL}) · [Privileges and model access for Cortex AI Functions]"
        f"({MODEL_RBAC_DOCS_URL}).",
        icon=":material/lightbulb:",
    )


def clear_all_caches():
    """Clears every cached loader in this module and marks data as stale.

    Called by the sidebar's Refresh data button, and after changes that can affect many loaders.
    """
    mark_stale()
    load_allowlist.clear()
    load_bundle_status.clear()
    load_cross_region.clear()
    load_public_grants.clear()
    load_model_roles.clear()
    load_current_grants.clear()
    load_base_models.clear()
    load_roles.clear()
    load_cortex_role_grants.clear()
    load_account_region.clear()
    load_role_model_access.clear()
    load_coco_limits.clear()
    load_coco_user_overrides.clear()
    load_user_coco_settings.clear()
    load_user_names.clear()


# Cortex / CoCo access roles managed on the Feature Access page.
# Descriptions come from the Snowflake docs, or for undocumented roles, from the role's own grants
# (SHOW GRANTS TO DATABASE/APPLICATION ROLE).
CORTEX_ACCESS_ROLES = [
    # (name, kind, description)
    ("CORTEX_USER", "DATABASE ROLE",
     "All Covered AI Features: AI functions, CoCo, Cortex Agents, Snowflake CoWork, Cortex Analyst, embeddings. "
     "Includes everything the narrower roles grant. Granted to PUBLIC by default."),
    ("CORTEX_AGENT_USER", "DATABASE ROLE",
     "Cortex Agents and agentic surfaces, including CoCo and Snowflake CoWork. "
     "Already included in CORTEX_USER. Not on PUBLIC by default."),
    ("AI_FUNCTIONS_USER", "DATABASE ROLE",
     "AI functions only. Already included in CORTEX_USER; use it after revoking CORTEX_USER."),
    ("USE AI FUNCTIONS", "PRIVILEGE",
     "Account privilege required with CORTEX_USER or AI_FUNCTIONS_USER to call AI functions. On PUBLIC by default."),
    ("EXECUTE AGENT TASK", "PRIVILEGE",
     "Account privilege to create and run CoCo automations (scheduled CoCo runs). On PUBLIC by default."),
    ("COPILOT_USER", "DATABASE ROLE",
     "Legacy (Snowflake Copilot). Stops gating CoCo in Snowsight starting the week of Oct 19, 2026 (BCR-2445)."),
    ("CORTEX_ANALYST_USER", "DATABASE ROLE", "Cortex Analyst only."),
    ("CORTEX_EMBED_USER", "DATABASE ROLE", "Embedding functions only."),
    ("CORTEX_REST_API_USER", "DATABASE ROLE",
     "Cortex REST API only. Already included in CORTEX_USER. Not on PUBLIC by default."),
    ("CORTEX_SENSE_USER", "DATABASE ROLE",
     "Cortex Sense (preview): use of the CORTEX_SENSE_PREVIEW function."),
    ("CORTEX_SENSE_BUILDER", "DATABASE ROLE",
     "Cortex Sense (preview). Currently holds the same grants as CORTEX_SENSE_USER."),
    ("ML_USER", "DATABASE ROLE",
     "ML functions: forecasting, anomaly detection, classification, top insights and comparisons, plus "
     "related helper functions."),
    ("CORTEX_MODELS_ADMIN", "APPLICATION ROLE",
     "Holds USAGE on every model in SNOWFLAKE.MODELS. Grant it as carefully as CORTEX-MODEL-ROLE-ALL."),
    ("CORTEX_ANALYST_REQUESTS_ADMIN", "APPLICATION ROLE",
     "Cortex Analyst request logs for all semantic models and views: read them, and delete or truncate the raw "
     "log table for retention."),
    ("CORTEX_ANALYST_REQUESTS_VIEWER", "APPLICATION ROLE",
     "Read-only Cortex Analyst request logs for all semantic models and views (CORTEX_ANALYST_REQUESTS_V)."),
    ("AI_OBSERVABILITY_ADMIN", "APPLICATION ROLE",
     "Delete rows from the AI observability event table, for retention management."),
    ("AI_OBSERVABILITY_READER", "APPLICATION ROLE",
     "Read the raw AI observability event table directly, bypassing per-object scoping. Routine monitoring "
     "only needs MONITOR on the agent or search service."),
    ("AI_OBSERVABILITY_EVENTS_LOOKUP", "APPLICATION ROLE",
     "Use the AI observability lookup functions (agent monitoring, evaluation data, traces and logs)."),
    ("OOB_CORTEX_EXTENSION_TAG_APPLY", "APPLICATION ROLE",
     "Apply the SNOWFLAKE.TAGS.CORTEX_EXTENSION_CATEGORIES tag to Cortex extensions."),
    ("OOB_CORTEX_EXTENSION_CERTIFIED_TAG_APPLY", "APPLICATION ROLE",
     "Apply the SNOWFLAKE.TAGS.CORTEX_EXTENSION_CERTIFIED tag to Cortex extensions."),
]


def cortex_role_sql(action, name, kind, grantee):
    """Builds a GRANT or REVOKE statement for a Cortex role, privilege or model.

    ``name`` and ``kind`` come from fixed allowlists (``CORTEX_ACCESS_ROLES``, the AI function list or the
    model catalog), never from free text.

    Args:
        action: ``"GRANT"`` or ``"REVOKE"``.
        name: Role, privilege or model name, e.g. ``CORTEX_USER``, ``USE AI FUNCTIONS`` or a model.
        kind: ``"DATABASE ROLE"``, ``"APPLICATION ROLE"``, ``"PRIVILEGE"``, ``"FUNCTION PRIVILEGE"`` or
            ``"MODEL"``.
        grantee: Account role to grant to or revoke from. It's double-quoted in the statement.

    Returns:
        The SQL statement.
    """
    to_from = "TO" if action == "GRANT" else "FROM"
    if kind in ("PRIVILEGE", "FUNCTION PRIVILEGE"):
        return f'{action} {name} ON ACCOUNT {to_from} ROLE "{grantee}"'
    if kind == "MODEL":
        return f'{action} APPLICATION ROLE {app_role(name)} {to_from} ROLE "{grantee}"'
    return f'{action} {kind} SNOWFLAKE.{name} {to_from} ROLE "{grantee}"'


# AI functions also need model access, but Snowflake doesn't document every function's model.
# Models without a lifecycle status are the best available guess, recomputed from SHOW CORTEX BASE MODELS.

def ai_function_suggested_models(d):
    """Lists the models suggested as the ones AI functions run on.

    These are models Snowflake reports without a lifecycle status, which are very likely used by AI
    functions. Snowflake doesn't publish an exact mapping.

    Args:
        d: The dict returned by ``load_all``.

    Returns:
        Sorted list of model names, excluding ``ALL``.
    """
    return sorted(m for m in d["available_models"] if m.upper() != ALL_MODEL and m.upper() not in d["status_map"])


def ai_function_models_covered(d):
    """Explains why AI functions need no model grants right now, if that's the case.

    Args:
        d: The dict returned by ``load_all``.

    Returns:
        A Markdown sentence giving the reason (``CORTEX-MODEL-ROLE-ALL`` on PUBLIC, or the allowlist set
        to ``ALL``), or an empty string if model grants are needed.
    """
    if d["all_is_public"]:
        return "`CORTEX-MODEL-ROLE-ALL` is on PUBLIC, so every role can use every model."
    if str(d["allowlist_value"]).upper() == "ALL":
        return "`CORTEX_MODELS_ALLOWLIST` is `ALL`, which allows models a role isn't granted."
    return ""


def ai_function_model_note(d):
    """Builds the note that AI functions also need access to the models they run on.

    Args:
        d: The dict returned by ``load_all``.

    Returns:
        Markdown text: either why nothing needs granting, or the list of suggested models to grant.
    """
    head = "**AI functions also need access to the models they run on.**"
    covered = ai_function_models_covered(d)
    if covered:
        return f"{head} Nothing to grant right now: {covered}"
    models = ai_function_suggested_models(d)
    return (
        f"{head} Snowflake doesn't document which models each function uses. Suggested: the models without a "
        f"lifecycle status: {', '.join(f'`{m}`' for m in models) or 'none'}.\n\n"
        "This list is a best guess and can change as Snowflake adds models or gives them a lifecycle status. "
        "Not every model on it is used by AI functions, and some functions may use models that do have a status."
    )


# Per-function alternatives to the blanket USE AI FUNCTIONS privilege (Cortex AI Functions privileges docs).
# (privilege name, function it controls)
AI_FUNCTION_PRIVILEGES = [
    ("AI_COMPLETE", "AI_COMPLETE"), ("AI_CLASSIFY", "AI_CLASSIFY"), ("AI_FILTER", "AI_FILTER"),
    ("AI_AGG", "AI_AGG"), ("AI_EMBED", "AI_EMBED"), ("AI_EXTRACT", "AI_EXTRACT"),
    ("AI_SENTIMENT", "AI_SENTIMENT"), ("AI_SUMMARIZE_AGG", "AI_SUMMARIZE_AGG"),
    ("AI_SIMILARITY", "AI_SIMILARITY"), ("AI_TRANSCRIBE", "AI_TRANSCRIBE"),
    ("AI_PARSE_DOCUMENT", "AI_PARSE_DOCUMENT"), ("AI_REDACT", "AI_REDACT"),
    ("AI_TRANSLATE", "AI_TRANSLATE"), ("AI_COUNT_TOKENS", "AI_COUNT_TOKENS"),
    ("COMPLETE", "SNOWFLAKE.CORTEX.COMPLETE"), ("CLASSIFY_TEXT", "SNOWFLAKE.CORTEX.CLASSIFY_TEXT"),
    ("COUNT_TOKENS", "SNOWFLAKE.CORTEX.COUNT_TOKENS"), ("EMBED_TEXT", "SNOWFLAKE.CORTEX.EMBED_TEXT"),
    ("ENTITY_SENTIMENT", "SNOWFLAKE.CORTEX.ENTITY_SENTIMENT"),
    ("EXTRACT_ANSWER", "SNOWFLAKE.CORTEX.EXTRACT_ANSWER"),
    ("PARSE_DOCUMENT", "SNOWFLAKE.CORTEX.PARSE_DOCUMENT"), ("SENTIMENT", "SNOWFLAKE.CORTEX.SENTIMENT"),
    ("SUMMARIZE", "SNOWFLAKE.CORTEX.SUMMARIZE"), ("SUMMARIZE_AGG", "SNOWFLAKE.CORTEX.SUMMARIZE_AGG"),
    ("TRANSLATE", "SNOWFLAKE.CORTEX.TRANSLATE"), ("TRY_COMPLETE", "SNOWFLAKE.CORTEX.TRY_COMPLETE"),
]
AI_FUNCTION_PRIVILEGE_PREFIX = "USE AI FUNCTION "   # trailing space: excludes the blanket USE AI FUNCTIONS


def ai_function_privilege(fn):
    """Returns the per-function privilege name for an AI function.

    Args:
        fn: Function name, e.g. ``AI_COMPLETE``.

    Returns:
        The privilege, e.g. ``USE AI FUNCTION AI_COMPLETE``.
    """
    return AI_FUNCTION_PRIVILEGE_PREFIX + fn


def _fetch_cortex_role_grants(name, kind):
    """Fetches the grants of one Cortex database or application role.

    Runs on a worker thread, so it doesn't call ``st.*``.

    Args:
        name: Role name in the SNOWFLAKE database or application, e.g. ``CORTEX_USER``.
        kind: ``"DATABASE ROLE"`` or ``"APPLICATION ROLE"``.

    Returns:
        A list of dicts with keys ``role``, ``kind``, ``granted_to`` and ``grantee``, or None if the
        role doesn't exist in this account.
    """
    try:
        g = q(f"SHOW GRANTS OF {kind} SNOWFLAKE.{name}")
    except Exception:
        return None
    return [
        {"role": name, "kind": kind, "granted_to": x["granted_to"], "grantee": x["grantee_name"]}
        for _, x in g.iterrows()
    ]


@st.cache_data(ttl=GRANTS_TTL)
def load_cortex_role_grants():
    """Loads who holds each Cortex role and privilege in ``CORTEX_ACCESS_ROLES``.

    Database and application roles are read with one SHOW GRANTS OF query each, in parallel. Account
    privileges (``USE AI FUNCTIONS``, ``EXECUTE AGENT TASK`` and per-function ``USE AI FUNCTION``
    privileges) come from a single SHOW GRANTS ON ACCOUNT.

    Returns:
        A tuple ``(grants, missing)``:

        * ``grants``: DataFrame with columns ``role``, ``kind``, ``granted_to`` and ``grantee``.
        * ``missing``: Names of roles from ``CORTEX_ACCESS_ROLES`` that don't exist in this account.
    """
    rows, missing = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_cortex_role_grants, name, kind): name
            for name, kind, _ in CORTEX_ACCESS_ROLES if kind != "PRIVILEGE"
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                missing.append(futures[fut])
            else:
                rows.extend(result)

    acct = q("SHOW GRANTS ON ACCOUNT")
    if not acct.empty:
        # Account privileges managed alongside the roles, e.g. USE AI FUNCTIONS and EXECUTE AGENT TASK
        account_privileges = {name for name, kind, _ in CORTEX_ACCESS_ROLES if kind == "PRIVILEGE"}
        for _, x in acct[acct["privilege"].isin(account_privileges)].iterrows():
            rows.append({"role": x["privilege"], "kind": "PRIVILEGE",
                         "granted_to": x["granted_to"], "grantee": x["grantee_name"]})
        # Per-function privileges, e.g. USE AI FUNCTION AI_COMPLETE
        per_fn = acct[acct["privilege"].str.startswith(AI_FUNCTION_PRIVILEGE_PREFIX, na=False)]
        for _, x in per_fn.iterrows():
            rows.append({"role": x["privilege"], "kind": "FUNCTION PRIVILEGE",
                         "granted_to": x["granted_to"], "grantee": x["grantee_name"]})

    return pd.DataFrame(rows, columns=["role", "kind", "granted_to", "grantee"]), sorted(missing)


@st.cache_data(ttl="2m")
def load_allowlist():
    """Loads the account's ``CORTEX_MODELS_ALLOWLIST`` value.

    Returns:
        The parameter value, e.g. ``"None"`` or ``"ALL"``, or ``"n/a"`` if it can't be read.
    """
    df = q("SHOW PARAMETERS LIKE 'CORTEX_MODELS_ALLOWLIST' IN ACCOUNT")
    return df["value"].iloc[0] if not df.empty else "n/a"


@st.cache_data(ttl="10m")
def load_bundle_status(bundle):
    """Loads the status of a behavior change bundle.

    Args:
        bundle: Bundle name, e.g. ``"2026_07"``.

    Returns:
        ``"ENABLED"``, ``"DISABLED"`` or ``"RELEASED"``, or ``"n/a"`` if it can't be read.
    """
    try:
        df = q(f"SELECT SYSTEM$BEHAVIOR_CHANGE_BUNDLE_STATUS('{bundle}') AS status")
        return str(df.iloc[0, 0]).upper() if not df.empty else "n/a"
    except Exception:
        return "n/a"


@st.cache_data(ttl="2m")
def load_cross_region():
    """Loads the account's ``CORTEX_ENABLED_CROSS_REGION`` setting.

    Returns:
        A tuple ``(value, default, level)``. ``value`` and ``default`` are the current and Snowflake
        default values (``"n/a"`` if unreadable); ``level`` is ``"ACCOUNT"`` when set explicitly,
        otherwise an empty string.
    """
    df = q("SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT")
    if df.empty:
        return "n/a", "n/a", ""
    row = df.iloc[0]
    value, default, level = row.get("value"), row.get("default"), row.get("level")
    return (str(value) if value else "n/a", str(default) if default else "n/a",
            str(level) if level and str(level) != "None" else "")


# CoCo daily credit-limit parameters, one per surface.
# -1 = no limit, 0 = blocked, positive = estimated credits per user over a rolling 24 hours.
COCO_LIMIT_PARAMS = {
    "Snowsight": "CORTEX_CODE_SNOWSIGHT_DAILY_EST_CREDIT_LIMIT_PER_USER",
    "CLI": "CORTEX_CODE_CLI_DAILY_EST_CREDIT_LIMIT_PER_USER",
    "Desktop": "CORTEX_CODE_DESKTOP_DAILY_EST_CREDIT_LIMIT_PER_USER",
}
_COCO_LIMIT_LIKE = "CORTEX_CODE%DAILY_EST_CREDIT_LIMIT_PER_USER"
_COCO_SURFACE = {param: surface for surface, param in COCO_LIMIT_PARAMS.items()}


def _num(v):
    """Converts a parameter value to a number.

    Args:
        v: The raw value, usually a string from SHOW PARAMETERS.

    Returns:
        The value as a float, or None if it isn't numeric.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl="2m")
def load_coco_limits():
    """Loads the account-level CoCo daily credit limit for each surface.

    Returns:
        A DataFrame with one row per surface, in ``COCO_LIMIT_PARAMS`` order, and columns:

        * ``surface``: ``"Snowsight"``, ``"CLI"`` or ``"Desktop"``.
        * ``param``: The parameter name.
        * ``value``: Current value as a float (``-1`` no limit, ``0`` blocked), or None if unreadable.
        * ``default``: Snowflake's default, or None if the parameter doesn't exist in this account.
        * ``level``: ``"ACCOUNT"`` when set explicitly, an empty string when the default applies.
    """
    df = q(f"SHOW PARAMETERS LIKE '{_COCO_LIMIT_LIKE}' IN ACCOUNT")
    found = {r["key"]: r for _, r in df.iterrows()} if not df.empty else {}
    rows = []
    for surface, param in COCO_LIMIT_PARAMS.items():
        r = found.get(param)
        level = r.get("level") if r is not None else None
        rows.append({
            "surface": surface,
            "param": param,
            "value": _num(r.get("value")) if r is not None else None,
            "default": _num(r.get("default")) if r is not None else None,
            "level": str(level) if level and str(level) != "None" else "",
        })
    return pd.DataFrame(rows)


def _user_coco_overrides(user):
    """Reads one user's user-level CoCo limit overrides.

    Runs on a worker thread, so it doesn't call ``st.*``.

    Args:
        user: User name. It's double-quoted in the query.

    Returns:
        A tuple ``(rows, error)``. ``rows`` is a list of dicts with keys ``user``, ``surface`` and
        ``value``, one per surface set at the user level. ``error`` is an empty string, or the error
        message if the query failed (then ``rows`` is empty).
    """
    ident = '"' + str(user).replace('"', '""') + '"'
    try:
        result = get_session().sql(f"SHOW PARAMETERS LIKE '{_COCO_LIMIT_LIKE}' IN USER {ident}").collect()
    except Exception as e:
        return [], str(e)
    rows = []
    for r in result:
        d = r.as_dict()
        if str(d.get("level", "")).upper() == "USER" and d.get("key") in _COCO_SURFACE:
            rows.append({"user": user, "surface": _COCO_SURFACE[d["key"]], "value": _num(d.get("value"))})
    return rows, ""


@st.cache_data(ttl="5m")
def load_user_names():
    """Loads the names of every user in the account.

    Returns:
        Sorted list of user names.
    """
    users = q("SHOW USERS")
    return sorted(users["name"]) if not users.empty else []


@st.cache_data(ttl="2m")
def load_user_coco_settings(user):
    """Loads one user's user-level CoCo overrides, for the CoCo On/Off Users tab.

    Args:
        user: User name.

    Returns:
        A tuple ``(overrides, error)``. ``overrides`` maps surface name to value for surfaces with a
        user-level override; surfaces without one are absent. ``error`` is an empty string, or the error
        message if the query failed.
    """
    rows, err = _user_coco_overrides(user)
    return {r["surface"]: r["value"] for r in rows}, err


@st.cache_data(ttl=GRANTS_TTL)
def load_coco_user_overrides():
    """Scans every user for user-level CoCo limit overrides.

    Snowflake has no account-wide view of user-level parameters, so this runs one SHOW PARAMETERS
    query per user, in parallel.

    Returns:
        A tuple ``(overrides, errors)``:

        * ``overrides``: DataFrame with columns ``user``, ``surface`` and ``value``.
        * ``errors``: DataFrame with columns ``user`` and ``error`` for users that couldn't be checked.
    """
    names = load_user_names()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(_user_coco_overrides, names))
    overrides = [row for rows, _ in results for row in rows]
    errors = [{"user": name, "error": err} for name, (_, err) in zip(names, results) if err]
    return (pd.DataFrame(overrides, columns=["user", "surface", "value"]),
            pd.DataFrame(errors, columns=["user", "error"]))


@st.cache_data(ttl=GRANTS_TTL)
def load_public_grants():
    """Loads the model roles granted to the ``SNOWFLAKE.PUBLIC`` application role.

    This is the path Snowflake uses for its default grant of ``CORTEX-MODEL-ROLE-ALL``. Grants to the
    PUBLIC account role come from ``load_current_grants`` instead.

    Returns:
        A tuple ``(pub_models, all_public)``. ``pub_models`` is the SHOW GRANTS DataFrame filtered to
        model roles; ``all_public`` is True if it includes ``CORTEX-MODEL-ROLE-ALL``.
    """
    pub = q("SHOW GRANTS TO APPLICATION ROLE SNOWFLAKE.PUBLIC")
    pub_models = pub[pub["name"].str.contains(PREFIX, na=False)] if not pub.empty else pub
    all_public = (
        not pub_models.empty
        and pub_models["name"].str.contains(PREFIX + "ALL").any()
    )
    return pub_models, all_public


@st.cache_data(ttl=GRANTS_TTL)
def load_model_roles():
    """Loads every model application role in the SNOWFLAKE application.

    Returns:
        Sorted list of role names, e.g. ``["CORTEX-MODEL-ROLE-ALL", "CORTEX-MODEL-ROLE-ARCTIC-EXTRACT", ...]``.
    """
    catalog = q(f"SHOW APPLICATION ROLES LIKE '{PREFIX}%' IN APPLICATION SNOWFLAKE")
    return sorted(catalog["name"].tolist())


def _fetch_grants_for_role(role_name):
    """Fetches who holds one model application role.

    Runs on a worker thread, so it doesn't call ``st.*``.

    Args:
        role_name: Model role name, e.g. ``CORTEX-MODEL-ROLE-ARCTIC-EXTRACT``.

    Returns:
        A list of dicts with keys ``model``, ``granted_to`` and ``grantee``.
    """
    rows = []
    g = q(f'SHOW GRANTS OF APPLICATION ROLE SNOWFLAKE."{role_name}"')
    for _, x in g.iterrows():
        rows.append({
            "model": role_name[len(PREFIX):],
            "granted_to": x["granted_to"],
            "grantee": x["grantee_name"],
        })
    return rows


@st.cache_data(ttl=GRANTS_TTL)
def load_current_grants(_model_roles_tuple):
    """Loads every direct grant of every model application role, in parallel.

    Args:
        _model_roles_tuple: Model role names from ``load_model_roles``, as a tuple. The leading
            underscore tells ``st.cache_data`` not to hash it.

    Returns:
        A DataFrame with columns ``model`` (name without the role prefix), ``granted_to`` (e.g.
        ``ROLE``) and ``grantee``.
    """
    all_rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_grants_for_role, r): r for r in _model_roles_tuple}
        for fut in as_completed(futures):
            all_rows.extend(fut.result())
    return pd.DataFrame(all_rows, columns=["model", "granted_to", "grantee"])


@st.cache_data(ttl=GRANTS_TTL)
def load_base_models():
    """Loads Snowflake's Cortex base models with lifecycle and region data.

    Returns:
        The SHOW CORTEX BASE MODELS result as a DataFrame, including ``name``, ``lifecycle_status``,
        ``eol_date``, ``in_region_availability`` and ``cross_region_availability``.
    """
    return q("SHOW CORTEX BASE MODELS IN SCHEMA SNOWFLAKE.MODELS")


@st.cache_data(ttl="1h")
def load_account_region():
    """Loads the account's region without its region-group prefix.

    Returns:
        The upper-case region, e.g. ``AWS_US_EAST_1`` for ``PUBLIC.AWS_US_EAST_1``, or ``"n/a"`` if it
        can't be read.
    """
    df = q("SELECT CURRENT_REGION() AS region")
    if df.empty or df.iloc[0, 0] is None:
        return "n/a"
    return str(df.iloc[0, 0]).split(".")[-1].upper()


def _region_list(value):
    """Parses a region column from SHOW CORTEX BASE MODELS.

    Args:
        value: JSON array text, an already-parsed list, ``"None"`` or None.

    Returns:
        Upper-case region names, or an empty list if there are none or the value can't be parsed.
    """
    if value is None or str(value) in ("None", ""):
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    return [str(v).upper() for v in parsed] if isinstance(parsed, list) else []


@st.cache_data(ttl="5m")
def load_roles():
    """Loads the names of every account role.

    Returns:
        A set of upper-case role names.
    """
    return set(q("SHOW ROLES")["name"].str.upper())


def _sql_str(s):
    """Quotes a value as a single-quoted SQL string literal, escaping backslashes and quotes.

    Args:
        s: The value to quote.

    Returns:
        The SQL literal.
    """
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


def _privilege_leaves(node):
    """Flattens an EXPLAIN_PRIVILEGES result node into readable privilege requirements.

    Args:
        node: A JSON node from EXPLAIN_PRIVILEGES, with ``privilege``, ``oneOf`` or ``allOf``.

    Returns:
        A list of strings like ``"USAGE on MODEL SNOWFLAKE.MODELS.X"``. ``oneOf`` alternatives are joined
        with ``or``.
    """
    if "privilege" in node:
        return [f'{node["privilege"]} on {node.get("objectType", "")} {node.get("objectName", "")}'.strip()]
    if "oneOf" in node:
        return [" or ".join(p for child in node["oneOf"] for p in _privilege_leaves(child))]
    if "allOf" in node:
        return [p for child in node["allOf"] for p in _privilege_leaves(child)]
    return []


def _check_model_access(role, model):
    """Checks whether a role can use one model.

    Asks EXPLAIN_PRIVILEGES whether the role could run ``DESCRIBE MODEL`` on it. EXPLAIN_PRIVILEGES
    evaluates the role and the roles granted to it (including PUBLIC) without activating it, so it works
    for roles this app's owner's-rights session can't USE.

    Runs on a worker thread, so it doesn't call ``st.*``.

    Args:
        role: Account role to check.
        model: Model name.

    Returns:
        A tuple ``(model, can_use, detail)``. ``can_use`` is True, False, or None if the check failed;
        ``detail`` lists missing privileges or the error, and is empty when ``can_use`` is True.
    """
    probe = f'DESCRIBE MODEL SNOWFLAKE.MODELS."{model.replace(chr(34), chr(34) * 2)}"'
    sql = (f"CALL EXPLAIN_PRIVILEGES(statement => {_sql_str(probe)}, "
           f"missing_only => TRUE, for_role => {_sql_str(role)})")
    try:
        result = json.loads(get_session().sql(sql).collect()[0][0])
    except Exception as e:
        return model, None, str(e)
    if result.get("authorized") is True:
        return model, True, ""
    return model, False, "; ".join(_privilege_leaves(result)) or "Not authorized"


@st.cache_data(ttl=GRANTS_TTL)
def load_role_model_access(role, models):
    """Checks which models a role can use, in parallel.

    Args:
        role: Account role to check.
        models: Model names, as a tuple so ``st.cache_data`` can hash it.

    Returns:
        A DataFrame with columns ``model``, ``can_use`` and ``detail``, as from ``_check_model_access``.
    """
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        rows = list(pool.map(lambda m: _check_model_access(role, m), models))
    return pd.DataFrame(rows, columns=["model", "can_use", "detail"])


def _loading_skeleton(height):
    """Returns a context manager that shows a skeleton placeholder while its block runs.

    ``st.skeleton`` only appears if the block takes longer than half a second, so cached loads show
    nothing. Falls back to doing nothing on older Streamlit versions without ``st.skeleton``, for example
    if the app falls back to the runtime's pre-installed Streamlit instead of the ``pyproject.toml`` version.

    Args:
        height: Skeleton height in pixels.

    Returns:
        A context manager.
    """
    return st.skeleton(height=height) if hasattr(st, "skeleton") else nullcontext()


def load_all(skeleton_height=400):
    """Loads the data most pages need, in two parallel phases, and derives common values from it.

    Phase 1 runs the independent loaders (allowlist, region, cross-region setting, PUBLIC grants, model
    roles, base models). Phase 2 loads every model grant and the account roles. Progress toasts show
    only when data is actually reloading, not on every widget rerun. While a slow load runs, a skeleton
    placeholder shows where the call is made, so a cold cache doesn't leave the page blank.

    Args:
        skeleton_height: Height in pixels of the loading skeleton. Pass the height of the content that
            will replace it, so the layout doesn't jump when it loads.

    Returns:
        A dict with these keys:

        * ``allowlist_value``: ``CORTEX_MODELS_ALLOWLIST`` value.
        * ``cross_region_value``, ``cross_region_default``, ``cross_region_level``: From
          ``load_cross_region``.
        * ``pub_models``: Model roles on the ``SNOWFLAKE.PUBLIC`` application role.
        * ``all_is_public``: True if ``CORTEX-MODEL-ROLE-ALL`` reaches PUBLIC by either path.
        * ``all_via_app_role``, ``all_via_account_role``: Which path(s) grant it.
        * ``model_roles``: Every model role name.
        * ``current_grants``: Every direct model grant (see ``load_current_grants``).
        * ``available_models``: Model names, without the role prefix.
        * ``existing_roles``: Set of account role names.
        * ``status_map``, ``eol_map``: Model name to lifecycle status and end-of-life date.
        * ``in_region_map``, ``cross_region_map``: Model name to native regions and to the
          ``CORTEX_ENABLED_CROSS_REGION`` values that can reach it.
        * ``account_region``: This account's region.
    """
    with _loading_skeleton(skeleton_height):
        return _load_all()


def _load_all():
    """Does the loading for ``load_all``; see there for the returned dict."""
    # Only toast when data is actually being (re)loaded, not on every widget rerun
    show_toasts = not st.session_state.get("_data_loaded", False)

    # Phase 1: independent loaders in parallel
    if show_toasts:
        st.toast("Loading account configuration...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_allowlist = pool.submit(load_allowlist)
        f_region = pool.submit(load_account_region)
        f_cross_region = pool.submit(load_cross_region)
        f_public = pool.submit(load_public_grants)
        f_roles_catalog = pool.submit(load_model_roles)
        f_base = pool.submit(load_base_models)
        allowlist_value = f_allowlist.result()
        cross_region_value, cross_region_default, cross_region_level = f_cross_region.result()
        pub_models, all_via_app_role = f_public.result()
        model_roles = f_roles_catalog.result()
        base_models = f_base.result()
        account_region = f_region.result()

    status_map = {}
    eol_map = {}
    in_region_map = {}      # model -> regions where it runs natively
    cross_region_map = {}   # model -> CORTEX_ENABLED_CROSS_REGION values that can reach it
    if not base_models.empty:
        for _, row in base_models.iterrows():
            name = row["name"].upper() if isinstance(row["name"], str) else row["name"]
            ls = row.get("lifecycle_status")
            if ls and str(ls) != "None":
                status_map[name] = str(ls)
            eol = row.get("eol_date")
            if eol and str(eol) != "None":
                eol_map[name] = str(eol)
            in_region_map[name] = _region_list(row.get("in_region_availability"))
            cross_region_map[name] = _region_list(row.get("cross_region_availability"))

    # Phase 2: grants and account roles in parallel
    # (load_current_grants itself parallelizes internally)
    if show_toasts:
        st.toast("Loading grants...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_grants = pool.submit(load_current_grants, tuple(model_roles))
        f_roles = pool.submit(load_roles)
        current_grants = f_grants.result()
        existing_roles = f_roles.result()

    if show_toasts:
        st.toast("Ready", icon=":material/check_circle:")
    st.session_state["_data_loaded"] = True

    # ALL can reach everyone two ways: the SNOWFLAKE.PUBLIC application role
    # (Snowflake's default) or a direct grant to the PUBLIC account role
    all_via_account_role = bool(
        (
            (current_grants["model"] == ALL_MODEL)
            & (current_grants["granted_to"] == "ROLE")
            & (current_grants["grantee"] == "PUBLIC")
        ).any()
    )
    all_is_public = all_via_app_role or all_via_account_role

    available_models = [r[len(PREFIX):] for r in model_roles]

    return {
        "allowlist_value": allowlist_value,
        "cross_region_value": cross_region_value,
        "cross_region_default": cross_region_default,
        "cross_region_level": cross_region_level,
        "pub_models": pub_models,
        "all_is_public": all_is_public,
        "all_via_app_role": all_via_app_role,
        "all_via_account_role": all_via_account_role,
        "model_roles": model_roles,
        "current_grants": current_grants,
        "available_models": available_models,
        "existing_roles": existing_roles,
        "status_map": status_map,
        "eol_map": eol_map,
        "in_region_map": in_region_map,
        "cross_region_map": cross_region_map,
        "account_region": account_region,
    }


def cross_region_routing(d):
    """Returns the account's cross-region routing values.

    Args:
        d: The dict returned by ``load_all``.

    Returns:
        Upper-case ``CORTEX_ENABLED_CROSS_REGION`` values, e.g. ``["AWS_US", "AZURE_US"]``. Empty when
        cross-region inference is disabled or the setting couldn't be read.
    """
    return [
        v.strip().upper() for v in d["cross_region_value"].split(",")
        if v.strip() and v.strip().upper() not in ("DISABLED", "N/A")
    ]


def model_availability(model, d, routing=None):
    """Describes whether this account can reach a model.

    Args:
        model: Model name.
        d: The dict returned by ``load_all``.
        routing: Pre-computed ``cross_region_routing(d)``, to avoid recomputing it for every model.

    Returns:
        ``"In region"``, ``"Cross-region (<values>)"``, ``"Not available"``, or ``"Assumed available"``
        for models without a lifecycle status (Snowflake reports no region data for them).
    """
    model = model.upper()
    if model not in d["status_map"]:
        # No lifecycle status means no region data either, so assume the account can use it
        return "Assumed available"
    if d["account_region"] in d["in_region_map"].get(model, []):
        return "In region"
    routing = cross_region_routing(d) if routing is None else routing
    reachable = [v for v in routing if v in d["cross_region_map"].get(model, [])]
    if reachable:
        return f"Cross-region ({', '.join(reachable)})"
    return "Not available"
