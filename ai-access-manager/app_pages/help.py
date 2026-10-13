"""Help & FAQ page, in two tabs.

* **FAQ**: questions in expanders, grouped into Getting started, Model access, Feature access and Beyond
  this app.
* **Terminology**: tables of terms built from ``TERMS``. Each term links to official Snowflake
  documentation, or is marked *this app* for labels the app itself uses.

The docs URLs are module constants, checked against the Snowflake docs search when added. Static content
only: this page runs no queries.
"""

import streamlit as st

st.title("Help & FAQ")
st.caption("Common questions about Cortex model and feature access, and how to use this app.")

tab_faq, tab_terms = st.tabs([":material/quiz: FAQ", ":material/menu_book: Terminology"])

# A narrower column keeps answers readable in the wide layout
with tab_faq, st.columns([3, 1])[0]:
    # ── Getting started ──────────────────────────────────────────────
    st.subheader("Getting started")

    with st.expander("What does this app do?"):
        st.markdown(
            "**It's one place to inspect and manage who can use Cortex AI in this account:** which models "
            "each role can call, which Cortex features it can use, and where CoCo is turned on.\n\n"
            "- See how Cortex access is set today\n"
            "- Spot non-default configurations\n"
            "- Grant or revoke access without writing SQL by hand"
        )

    with st.expander("Which page does what?"):
        st.markdown(
            "**Overview**\n"
            "- **Home**: model counts at a glance, then this account's Cortex access settings compared with "
            "Snowflake's defaults: inference routing, model access and feature access, each with a fix.\n"
            "- **Model Catalog**: every model with its lifecycle status, whether it's available in this "
            "account's region, and whether it's on PUBLIC.\n\n"
            "**Admin**\n"
            "- **Model Access**: the **All grants** tab lists every model grant in the account; the "
            "**Edit a role** tab changes which models one role can use.\n"
            "- **Feature Access**: the Cortex roles and privileges that decide which features a role can use, "
            "who holds them, and per-function AI privileges.\n"
            "- **Verify Role**: checks which models a role can actually use, including through roles granted "
            "to it and PUBLIC.\n"
            "- **CoCo On/Off**: turns CoCo on or off for each surface (Snowsight, CLI and Desktop), for the "
            "whole account on the **Account** tab or for individual users on the **Users** tab."
        )

    with st.expander("What role do I need to run this app?"):
        st.markdown(
            "**ACCOUNTADMIN**, or a role with equivalent privileges.\n\n"
            "- It's needed to read all grants and run grant and revoke statements.\n"
            "- Some read-only pages work with less, but the Admin pages require it."
        )

    with st.expander("What order should I do things in?"):
        st.markdown(
            "**Control access with features. Restrict individual models only if you must.**\n\n"
            "**For most accounts**\n\n"
            "1. **Home**: review your Cortex access settings. If `CORTEX_MODELS_ALLOWLIST` is still set, Home "
            "shows how to retire it.\n"
            "2. **Feature Access**: decide which roles need which Cortex features, and grant them the appropriate "
            "database roles and privileges. Keep `CORTEX-MODEL-ROLE-ALL` on PUBLIC, so feature access is the "
            "only control you manage.\n"
            "3. **CoCo On/Off**: turn CoCo surfaces on or off if needed. Use per-user quotas to control spending.\n\n"
            "**Only if you have a specific requirement to limit models**, such as legal or compliance:\n\n"
            "4. **Model Access**: grant each role the models it needs. The **All grants** tab lists every model "
            "grant in the account.\n"
            "5. **Home**: once those grants are in place, revoke `CORTEX-MODEL-ROLE-ALL` from PUBLIC and choose "
            "which individual models PUBLIC keeps.\n"
            "6. **Verify Role**: check that each role can use the models you intended.\n\n"
            "**Avoid individual model grants if at all possible.** They can have side effects that are hard to "
            "predict:\n"
            "- Restricting a model also restricts the Cortex features that use it, such as AI functions, and "
            "Cortex Search for embedding models.\n"
            "- Snowflake doesn't yet publish which models each feature relies on, so a revoke can break a feature "
            "without warning.\n"
            "- Each new model Snowflake releases has to be granted before anyone can use it."
        )

    with st.expander("What does the Home page check?"):
        st.markdown(
            "**Models at a glance** counts the models in Snowflake's catalog by lifecycle status, and the roles "
            "that hold model grants.\n\n"
            "**Cortex access settings** compares these with Snowflake's defaults:\n\n"
            "- **Cross-region inference**: enabled, disabled, or restricted to specific regions.\n"
            "- **CORTEX_MODELS_ALLOWLIST**: shown only while this deprecated parameter is still set.\n"
            "- **CORTEX-MODEL-ROLE-ALL**: whether all models are accessible via PUBLIC.\n"
            "- **Individual model grants to PUBLIC**: per-model grants beyond the blanket ALL.\n"
            "- **End-of-life model grants**: grants to models that are deprecated or sunset.\n"
            "- **Cortex database roles**: which are on PUBLIC, and whether that matches the default.\n"
            "- **USE AI FUNCTIONS**: whether the account-level privilege is on PUBLIC, plus "
            "per-function grants.\n"
            "- **EXECUTE AGENT TASK**: whether the CoCo automations privilege is on PUBLIC.\n\n"
            "To see which roles hold which model grants, use the **All grants** tab on **Model Access**."
        )

    # ── Model access ─────────────────────────────────────────────────
    st.subheader("Model access")

    with st.expander("What is CORTEX-MODEL-ROLE-ALL?"):
        st.markdown(
            "**An application role that covers every Cortex model, current and future.**\n\n"
            "- It lives in the `SNOWFLAKE` database.\n"
            "- By default it's granted to `PUBLIC` through the `SNOWFLAKE.PUBLIC` application role, "
            "so every role in your account can use every model.\n"
            "- Revoking it from `PUBLIC` is the first step toward per-role model control."
        )

    with st.expander("What's the difference between the application role and account role grant paths?"):
        st.markdown(
            "**Same result, different revoke.** `CORTEX-MODEL-ROLE-ALL` can reach `PUBLIC` two ways:\n\n"
            "1. **Via the `SNOWFLAKE.PUBLIC` application role**: Snowflake's default (bootstrap) grant.\n"
            "2. **Via a direct grant to the `PUBLIC` account role**: someone ran "
            "`GRANT APPLICATION ROLE ... TO ROLE PUBLIC` manually.\n\n"
            "Either way everyone gets all models, but the revoke mechanism differs. "
            "The Home page shows which path is active."
        )

    with st.expander("Will revoking ALL from PUBLIC break anything?"):
        st.markdown(
            "**Yes, if you haven't set up grants first.**\n\n"
            "- Revoking `CORTEX-MODEL-ROLE-ALL` from `PUBLIC` immediately cuts off any role that "
            "doesn't have its own model grants.\n"
            "- Grant the Cortex roles (on **Feature Access**) and model access (on **Model Access**) that roles "
            "need **before** you revoke it.\n"
            "- The Revoke ALL drawer on the Home page lists the roles that keep access and a checklist to follow."
        )

    # ── Feature access ───────────────────────────────────────────────
    st.subheader("Feature access")

    with st.expander("What are the Cortex database roles (CORTEX_USER, CORTEX_AGENT_USER, etc.)?"):
        st.markdown(
            "**They control feature-level access** (whether a role can use Cortex at all), "
            "as opposed to model-level access. They live in the `SNOWFLAKE` database.\n\n"
            "| Role | Covers |\n"
            "|---|---|\n"
            "| `CORTEX_USER` | All Covered AI Features: AI functions, CoCo, Cortex Agents, Snowflake CoWork, Cortex Analyst, embeddings |\n"
            "| `CORTEX_AGENT_USER` | Cortex Agents and agentic surfaces, including CoCo and Snowflake CoWork |\n"
            "| `AI_FUNCTIONS_USER` | AI functions only (AI_COMPLETE, AI_CLASSIFY, etc.) |\n"
            "| `CORTEX_EMBED_USER` | Embedding functions only |\n"
            "| `CORTEX_ANALYST_USER` | Cortex Analyst only |\n"
            "| `CORTEX_REST_API_USER` | Cortex REST API only |\n"
            "| `COPILOT_USER` | Legacy. Stops gating CoCo in Snowsight starting the week of Oct 19, 2026 (BCR-2445) |\n\n"
            "- `CORTEX_USER` is granted to `PUBLIC` by default, so everyone has all features.\n"
            "- The narrower roles matter only after you revoke `CORTEX_USER` from `PUBLIC`.\n"
            "- The **Feature Access** page lists every Cortex-related role and privilege, including application "
            "roles such as those for Cortex Analyst request logs and AI observability."
        )

    with st.expander("What is USE AI FUNCTIONS?"):
        st.markdown(
            "**An account-level privilege (not a database role) that gates Cortex AI functions** "
            "such as `AI_COMPLETE` and `AI_CLASSIFY`.\n\n"
            "- Users need **both** this privilege **and** `CORTEX_USER` or `AI_FUNCTIONS_USER`.\n"
            "- It's granted to `PUBLIC` by default.\n"
            "- Per-function privileges (`USE AI FUNCTION AI_COMPLETE`, etc.) allow finer-grained control."
        )

    with st.expander("What's the minimum configuration for CoCo and CoWork?"):
        st.markdown(
            "**A Cortex database role, plus agent access for CoWork.**\n\n"
            "**CoCo in Snowsight and CoWork agents**\n"
            "- `SNOWFLAKE.CORTEX_USER` or `SNOWFLAKE.CORTEX_AGENT_USER`\n"
            "- For CoCo in Snowsight, until BCR-2445 reaches your account (rolling out from the week "
            "of Oct 19, 2026): `SNOWFLAKE.COPILOT_USER` as well\n\n"
            "**CoWork agents also need**\n"
            "- `USAGE` on the agent's database, schema, and the agent itself\n"
            "- Privileges on the agent's tools (SELECT on tables, USAGE on search services, etc.)\n"
            "- A **default role** with these privileges (agents use the default role, not the session role)\n"
            "- A **default warehouse** with USAGE granted to the default role\n\n"
            "To allow CoWork but block CoCo in Snowsight, set its daily credit limit to 0. The **CoCo On/Off** "
            "page does this for each surface (Snowsight, CLI and Desktop), for the whole account or, on its "
            "**Users** tab, for individual users:"
        )
        st.code(
            "ALTER ACCOUNT SET CORTEX_CODE_SNOWSIGHT_DAILY_EST_CREDIT_LIMIT_PER_USER = 0;",
            language="sql",
        )
        st.markdown(
            "Only `-1` (no limit) and `0` (blocked) are recommended; any other value is legacy. For spending "
            "control, **per-user quotas** are the best practice. They support "
            "daily, weekly or monthly limits, tag-based scope, automatic blocking and notifications."
        )

    # ── Beyond this app ──────────────────────────────────────────────
    st.subheader("Beyond this app")

    with st.expander("What AI-related privileges does this app not manage?"):
        st.markdown(
            "**Object privileges for creating and using AI objects.** This app manages model access "
            "(**Model Access**), the Cortex database roles, USE AI FUNCTIONS and CoCo automations "
            "(`EXECUTE AGENT TASK`) (all on **Feature Access**), cross-region routing (**Home**) and CoCo surface "
            "access (**CoCo On/Off**). "
            "Manage the privileges below with standard `GRANT` statements.\n\n"
            "| Feature | To create | To use |\n"
            "|---|---|---|\n"
            "| Cortex Agents | `CREATE AGENT` on the schema | `USAGE` on the agent |\n"
            "| Semantic views | `CREATE SEMANTIC VIEW` on the schema | `SELECT` on the semantic view |\n"
            "| Cortex Search services | `CREATE CORTEX SEARCH SERVICE` on the schema | `USAGE` on the service |\n\n"
            "- **All objects** also need `USAGE` on their database and schema.\n"
            "- **Agents**: callers need privileges on each tool's objects, and agents run with the "
            "caller's **default role** and default warehouse, so grant tool access to that role. "
            "`MODIFY` lets a role edit or drop an agent; `MONITOR` lets it view threads, logs and traces.\n"
            "- **Semantic views**: the creator also needs `SELECT` on the tables and views it references.\n"
            "- **Cortex Search services**: the creator also needs `SELECT` on the source tables and "
            "views, `USAGE` on the refresh warehouse, and `CORTEX_USER` or `CORTEX_EMBED_USER`."
        )
        st.markdown("To give a role access to semantic views and search services created later in a schema:")
        st.code(
            "GRANT SELECT ON FUTURE SEMANTIC VIEWS IN SCHEMA <db>.<schema> TO ROLE <role_name>;\n"
            "GRANT USAGE ON FUTURE CORTEX SEARCH SERVICES IN SCHEMA <db>.<schema> TO ROLE <role_name>;",
            language="sql",
        )


# ── Terminology ──────────────────────────────────────────────────────

# Official Snowflake documentation, checked against the docs search
DOCS = "https://docs.snowflake.com/en"
ACCESS_CONTROL = f"{DOCS}/user-guide/security-access-control-overview"
SNOWFLAKE_DB_ROLES = f"{DOCS}/sql-reference/snowflake-db-roles"
APPLICATION_ROLES = f"{DOCS}/sql-reference/sql/show-application-roles"
GRANT_PRIVILEGE = f"{DOCS}/sql-reference/sql/grant-privilege"
EXPLAIN_PRIVILEGES = f"{DOCS}/sql-reference/functions/explain_privileges"
AI_PRIVILEGES = f"{DOCS}/user-guide/snowflake-cortex/aisql-privileges-and-access"
BASE_MODELS = f"{DOCS}/sql-reference/sql/show-cortex-base-models"
CROSS_REGION = f"{DOCS}/user-guide/snowflake-cortex/cross-region-inference"
REGIONAL_AVAILABILITY = f"{DOCS}/user-guide/snowflake-cortex/aisql-regional-availability"
BCR_2378 = f"{DOCS}/release-notes/bcr-bundles/un-bundled/bcr-2378"
BEHAVIOR_CHANGES = f"{DOCS}/release-notes/intro-bcr-releases"
AI_FUNCTIONS = f"{DOCS}/user-guide/snowflake-cortex/aisql"
CORTEX_AGENTS = f"{DOCS}/user-guide/snowflake-cortex/cortex-agents"
AGENTS_ACCESS = f"{DOCS}/user-guide/snowflake-cortex/cortex-agents-setup"
CORTEX_ANALYST = f"{DOCS}/user-guide/snowflake-cortex/cortex-analyst"
CORTEX_SEARCH = f"{DOCS}/user-guide/snowflake-cortex/cortex-search/cortex-search-overview"
CORTEX_REST_API = f"{DOCS}/user-guide/snowflake-cortex/cortex-rest-api"
COWORK = f"{DOCS}/user-guide/snowflake-cortex/snowflake-cowork"
AI_OBSERVABILITY = f"{DOCS}/user-guide/snowflake-cortex/ai-observability"
COCO = f"{DOCS}/user-guide/cortex-code/cortex-code"
COCO_CREDIT_LIMITS = f"{DOCS}/user-guide/cortex-code/credit-usage-limit"
COCO_AUTOMATIONS = f"{DOCS}/user-guide/cortex-code/cortex-code-automations"
PER_USER_QUOTAS = f"{DOCS}/user-guide/budgets/per-user-quotas"

# (term, meaning, docs URL or None for terms this app uses)
TERMS = {
    "Roles and privileges": [
        ("Role (account role)",
         "A set of privileges you grant to users or to other roles. Roles granted to a role pass their privileges "
         "on to it.", ACCESS_CONTROL),
        ("PUBLIC",
         "The account role every user and role has automatically. Anything granted to PUBLIC reaches everyone.",
         ACCESS_CONTROL),
        ("Account privilege",
         "A privilege on the account itself rather than an object, such as `USE AI FUNCTIONS` or "
         "`EXECUTE AGENT TASK`.", GRANT_PRIVILEGE),
        ("Database role (SNOWFLAKE database roles)",
         "A role that belongs to a database. Snowflake provides Cortex ones in the `SNOWFLAKE` database, such as "
         "`CORTEX_USER`. You grant them to account roles, not directly to users.", SNOWFLAKE_DB_ROLES),
        ("Application role",
         "A role that belongs to an application. The `SNOWFLAKE` application provides the model roles and some "
         "Cortex admin roles.", APPLICATION_ROLES),
        ("Direct grant",
         "A grant made to the role itself. Most tables in this app show direct grants; **Verify Role** also "
         "follows inherited ones.", None),
        ("EXPLAIN_PRIVILEGES",
         "A function that reports whether a role has the privileges a statement needs. **Verify Role** uses it.",
         EXPLAIN_PRIVILEGES),
    ],
    "Models": [
        ("Cortex base model",
         "An LLM or AI model that Snowflake provides for Cortex AI Functions and related features. "
         "`SHOW CORTEX BASE MODELS` lists them.", BASE_MODELS),
        ("Model application role (`CORTEX-MODEL-ROLE-<model>`)",
         "The `SNOWFLAKE` application role that grants access to one model. Granting it is how you allow a role to "
         "call that model.", AI_PRIVILEGES),
        ("`CORTEX-MODEL-ROLE-ALL`",
         "The model application role that covers every model, current and future. On PUBLIC by default.",
         AI_PRIVILEGES),
        ("`SNOWFLAKE.PUBLIC` application role",
         "The application role through which Snowflake grants `CORTEX-MODEL-ROLE-ALL` to everyone by default. "
         "It's separate from the PUBLIC account role.", AI_PRIVILEGES),
        ("Model RBAC",
         "Controlling which models each role can call by granting model application roles.", AI_PRIVILEGES),
        ("Lifecycle status",
         "A model's stage: **GA** (generally available, recommended for production), **PUPR** (public preview), "
         "**PRPR** (private preview, for accounts that have been enabled), **LEGACY** (only accounts that used it "
         "before its legacy date can keep using it, until end of life) and **EOL** (end of life: no longer "
         "available, and calls fail). `INTERNAL` also appears but isn't documented.", BASE_MODELS),
        ("No status",
         "This app's label for models Snowflake reports without a lifecycle status. They're very likely used by "
         "an AI function.", None),
        ("Cross-region inference (`CORTEX_ENABLED_CROSS_REGION`)",
         "Lets Snowflake process a request in another region when a model isn't available in the account's "
         "region. The value lists the region groups allowed.", CROSS_REGION),
        ("Regional availability",
         "Which regions run each model natively. The **Available here** column combines it with cross-region "
         "inference.", REGIONAL_AVAILABILITY),
        ("`CORTEX_MODELS_ALLOWLIST`",
         "A deprecated account parameter listing allowed models. Model RBAC replaces it.", BCR_2378),
    ],
    "Features": [
        ("Covered AI Features",
         "Snowflake's term for the AI features `CORTEX_USER` grants access to.", AI_PRIVILEGES),
        ("Cortex AI Functions",
         "SQL functions such as `AI_COMPLETE` and `AI_CLASSIFY`. They need `USE AI FUNCTIONS` and "
         "`CORTEX_USER` or `AI_FUNCTIONS_USER`.", AI_FUNCTIONS),
        ("`USE AI FUNCTIONS` / `USE AI FUNCTION <name>`",
         "The account privilege for AI functions, and its per-function form for finer control.", AI_PRIVILEGES),
        ("Cortex Agents",
         "Agents that answer questions using tools such as Cortex Analyst and Cortex Search. They run with each "
         "user's default role.", CORTEX_AGENTS),
        ("Default role",
         "The role a user's session starts with. Cortex Agents use it to decide permissions, not the role active "
         "in the session.", AGENTS_ACCESS),
        ("Cortex Analyst", "Answers questions in natural language by generating SQL over semantic views.",
         CORTEX_ANALYST),
        ("Cortex Search", "Search services over your text data, used directly or as an agent tool.", CORTEX_SEARCH),
        ("Cortex REST API", "HTTP access to Cortex models and features from outside SQL.", CORTEX_REST_API),
        ("Snowflake CoWork", "Snowflake's agent experience for business users (formerly Snowflake Intelligence).",
         COWORK),
        ("AI observability", "Traces, logs and evaluations for Cortex Agents and other AI applications.",
         AI_OBSERVABILITY),
    ],
    "CoCo": [
        ("CoCo (Snowflake CoCo)", "Snowflake's AI coding agent, formerly Cortex Code.", COCO),
        ("CoCo surfaces",
         "Where CoCo runs: **Snowsight**, the **CLI** and **CoCo Desktop**. Each has its own on/off parameter.",
         COCO),
        ("Daily credit usage limit (`CORTEX_CODE_*_DAILY_EST_CREDIT_LIMIT_PER_USER`)",
         "The per-surface parameter this app uses to turn CoCo on (`-1`) or off (`0`). Positive values are an "
         "estimated rolling 24-hour limit, which this app treats as legacy.", COCO_CREDIT_LIMITS),
        ("User-level override",
         "A user's own value for one of those parameters, which replaces the account value for that user.",
         COCO_CREDIT_LIMITS),
        ("Per-user quota",
         "Snowflake's recommended way to control each user's AI spend, with limits, scoping, blocking and "
         "notifications.", PER_USER_QUOTAS),
        ("CoCo automations (`EXECUTE AGENT TASK`)",
         "Scheduled CoCo runs. Creating them needs the `EXECUTE AGENT TASK` account privilege, on PUBLIC by "
         "default.", COCO_AUTOMATIONS),
    ],
    "Changes and releases": [
        ("Behavior change (BCR)",
         "A Snowflake change to existing behavior that may return different results or affect workloads. Each has "
         "a BCR number, such as BCR-2378 for the move from the allowlist to model RBAC. Most ship in monthly "
         "bundles named `YYYY_NN`, such as `2026_07`.",
         BEHAVIOR_CHANGES),
    ],
}

with tab_terms, st.columns([3, 1])[0]:
    st.markdown(
        "Terms used in this app. Links go to the official Snowflake documentation; terms marked "
        "*this app* are labels the app uses."
    )
    for group, terms in TERMS.items():
        st.subheader(group)
        rows = ["| Term | Meaning | Docs |", "|---|---|---|"]
        for term, meaning, url in terms:
            docs = f"[Docs]({url})" if url else "*this app*"
            rows.append(f"| **{term}** | {meaning} | {docs} |")
        st.markdown("\n".join(rows))
