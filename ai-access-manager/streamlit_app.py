"""Entry point for the AI Access Manager Streamlit app.

Sets up the page, opens the Snowflake connection once so every page reuses it, and builds the sidebar
navigation:

* **Overview**: Home and Model Catalog (read-mostly).
* **Admin**: Model Access, Feature Access, Verify Role and CoCo On/Off.
* **Help**: Help & FAQ.

The sidebar's Refresh data button clears every cached loader in ``data``. Each page's URL comes from its
file name in ``app_pages/``, so renaming a page file changes its URL.

The app runs as the Streamlit object's owner role, which needs ACCOUNTADMIN-level privileges to read all
grants and run the GRANT, REVOKE and ALTER statements the Admin pages offer.

Errors that escape a page are caught around ``page.run()`` and shown with ``data.show_error``, so a failed
query shows a readable message and a Retry button instead of a traceback, and the sidebar keeps working.
``st.rerun()`` and ``st.stop()`` raise ``BaseException`` subclasses, so ``except Exception`` doesn't catch
them.

The app needs Snowflake's shared PyPI artifact repository attached to install the packages in
``pyproject.toml``. Without it, importing the app's own ``data`` module fails at startup, so that import is
guarded and shows how to attach the repository instead of a traceback.
"""

import os
import streamlit as st

APP_NAME = "AI Access Manager"
APP_ICON = ":material/security:"
# Where install.ipynb creates the app by default, for the fix shown if dependencies are missing
DEFAULT_APP_FQN = "AI_ACCESS_MANAGER.AI_ACCESS_MANAGER_APP.AI_ACCESS_MANAGER"

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
)

# This import fails when the app has no artifact repository to install its dependencies from. Explain the
# fix here, without anything from data, since that's the module that couldn't be imported.
try:
    from data import clear_all_caches, show_error
except ImportError as e:
    st.markdown(f"{APP_ICON} **{APP_NAME}**")
    st.error(
        "**The app couldn't start because its Python dependencies aren't installed.** This happens when the "
        "app has no artifact repository attached, so Snowflake can't install the packages in "
        f"`pyproject.toml` (the app is running with Streamlit {st.__version__}).\n\n"
        "**To fix it**, attach Snowflake's shared PyPI repository to the app, as `ACCOUNTADMIN`. Replace the "
        f"app name if you didn't install it as `{DEFAULT_APP_FQN}`:",
        icon=":material/error:",
    )
    st.code(
        "GRANT DATABASE ROLE SNOWFLAKE.PYPI_REPOSITORY_USER TO ROLE ACCOUNTADMIN;\n"
        f"ALTER STREAMLIT {DEFAULT_APP_FQN}\n"
        "  SET ARTIFACT_REPOSITORIES = (snowflake.snowpark.pypi_shared_repository);",
        language="sql",
    )
    st.markdown(
        "Or re-run `install.ipynb`, which sets this up. The app restarts automatically once the repository "
        "is attached.\n\n"
        "**Previewing the app in a Workspace?** Open the app's **Settings** and choose "
        "`snowflake.snowpark.pypi_shared_repository` as the artifact repository instead."
    )
    with st.expander("Error details"):
        st.code(f"{type(e).__name__}: {e}", language="text")
    st.stop()

# Initialize connection before page.run() so pages can reuse it
if "_connected" not in st.session_state:
    st.toast("Connecting to Snowflake...")
try:
    st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))
except Exception as e:
    st.markdown(f"{APP_ICON} **{APP_NAME}**")
    show_error("Couldn't connect to Snowflake", e, retry_key="_retry_connect")
    st.stop()
st.session_state["_connected"] = True

page = st.navigation(
    {
        "Overview": [
            st.Page("app_pages/home.py", title="Home", icon=":material/home:", default=True),
            st.Page("app_pages/model_catalog.py", title="Model Catalog", icon=":material/view_list:"),
        ],
        "Admin": [
            st.Page("app_pages/model_access.py", title="Model Access", icon=":material/edit:"),
            st.Page("app_pages/feature_access.py", title="Feature Access", icon=":material/badge:"),
            st.Page("app_pages/verify_role.py", title="Verify Role", icon=":material/verified:"),
            st.Page("app_pages/coco_on_off.py", title="CoCo On/Off", icon=":material/terminal:"),
        ],
        "Help": [
            st.Page("app_pages/help.py", title="Help & FAQ", icon=":material/help:"),
        ],
    },
    position="sidebar",
)

with st.sidebar:
    st.button(
        ":material/refresh: Refresh data",
        on_click=clear_all_caches,
        width="stretch",
        help="Clear cached results and reload grants, models, and settings from Snowflake.",
    )

# App name above every page's own title
st.markdown(f"{APP_ICON} **{APP_NAME}**")

try:
    page.run()
except Exception as e:
    # Anything the page rendered before failing stays visible above this
    show_error(f"{page.title} couldn't load", e, retry_key="_retry_page")
