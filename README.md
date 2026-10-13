# AI Access Manager

A Snowflake Streamlit app that provides a single pane of glass to inspect, manage, and control access to Cortex AI features across your account.

> **Disclaimer:** This is a community project and is **not** an official Snowflake product or offering. It is not supported, endorsed, or maintained by Snowflake Inc. The software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. In no event shall the authors or contributors be liable for any claim, damages, or other liability arising from the use of this software. This app can modify grants, privileges, and account parameters — review all generated SQL carefully and test in a non-production account before use. Use at your own risk.

## What it does

- **Model-level RBAC** — View and manage which roles can call which Cortex AI models (Claude, Llama, etc.) via `CORTEX-MODEL-ROLE-*` application roles
- **Feature-level access** — Manage Cortex database roles (`CORTEX_USER`, `CORTEX_AGENT_USER`, `AI_FUNCTIONS_USER`, etc.) and account privileges (`USE AI FUNCTIONS`, `EXECUTE AGENT TASK`)
- **CoCo controls** — Enable or disable Cortex Code per surface (Snowsight, CLI, Desktop) at the account or per-user level
- **Role verification** — Check what a role can actually do using `EXPLAIN_PRIVILEGES`
- **Settings audit** — Compare current configuration against Snowflake defaults, spot non-default settings, and generate remediation SQL

All changes show a SQL preview in a confirmation drawer before executing — nothing runs without explicit confirmation.

## Pages

| Page | Description |
|---|---|
| **Home** | Dashboard with model metrics, settings audit, and one-click remediation |
| **Model Catalog** | Read-only catalog of all Cortex base models with lifecycle status and availability |
| **Model Access** | Grant and revoke model-level application roles |
| **Feature Access** | Manage Cortex database roles and account-level AI privileges |
| **CoCo On/Off** | Toggle Cortex Code per surface, per account or per user |
| **Verify Role** | Check effective model access for any role via `EXPLAIN_PRIVILEGES` |
| **Help** | FAQ, getting started guide, and glossary |

## Requirements

- **Role:** `ACCOUNTADMIN`. The app runs with its owner's rights, so it must be installed and owned by `ACCOUNTADMIN` to read all grants and run GRANT/REVOKE/ALTER across the account
- **Compute:** a compute pool for the Streamlit container runtime (defaults to Snowflake's built-in `SYSTEM_COMPUTE_POOL_CPU`) and a warehouse for the app's queries
- **Packages:** Python 3.11 and Streamlit ≥ 1.65.0, installed from Snowflake's shared PyPI artifact repository (`snowflake.snowpark.pypi_shared_repository`)

## Installation

The installer runs from a Snowflake Workspace and reads the app files from it, so first add this repo to a workspace:

- **Git-backed workspace (recommended):** in Snowsight, go to **Projects » Workspaces** and create a workspace from this Git repository. To update later, pull the latest changes and re-run the notebook.
- **Private workspace:** create a workspace and upload the repo's files, keeping `install.ipynb` at the root next to the `ai-access-manager/` folder.

Then install the app with the `install.ipynb` notebook:

1. **Open `install.ipynb`** in the workspace and connect it to a notebook service.
2. **Review the configuration cell** at the top and change any defaults you need:

   | Variable | Default | Description |
   |---|---|---|
   | `QUERY_WAREHOUSE` | `COMPUTE_WH` | Warehouse for the app's SQL queries |
   | `APP_DATABASE` | `AI_ACCESS_MANAGER` | Database for the app, created if it doesn't exist |
   | `APP_SCHEMA` | `AI_ACCESS_MANAGER_APP` | Schema for the app, created if it doesn't exist |
   | `APP_NAME` | `AI_ACCESS_MANAGER` | Name of the Streamlit object |
   | `APP_STAGE` | `APP_SOURCE` | Stage that holds a copy of the app's source files |
   | `COMPUTE_POOL` | `SYSTEM_COMPUTE_POOL_CPU` | Compute pool the app runs on |
   | `WORKSPACE_FQN` | Auto-detected | The workspace the notebook runs in |
   | `APP_FOLDER` | Auto-detected | The folder next to the notebook that contains `streamlit_app.py` and `snowflake.yml` |

3. **Run all cells as `ACCOUNTADMIN`.** The notebook:
   - switches to `ACCOUNTADMIN`
   - creates the database, schema and stage (`CREATE ... IF NOT EXISTS`)
   - copies the app's `.py` and `.toml` files from the workspace to the stage
   - grants `SNOWFLAKE.PYPI_REPOSITORY_USER` to `ACCOUNTADMIN` so the app can install its packages
   - creates the Streamlit app on the container runtime with the PyPI artifact repository attached, and publishes it
4. **Open the app** from **Projects » Streamlit » AI Access Manager**. The first start can take a minute or two while the container starts and installs packages.

**Updating:** edit or pull the code in the workspace, then re-run the notebook. Re-running is safe: it reuses the existing database, schema and stage, refreshes the staged files, and replaces the app.

> **Sharing:** Streamlit in Snowflake apps run with **owner's rights**, so this app acts as `ACCOUNTADMIN`. Granting `USAGE` on the app to another role lets that role make `ACCOUNTADMIN`-level grant, privilege and parameter changes through it. The installer doesn't grant access to any other role.

`ai-access-manager/snowflake.yml` is used when you deploy with the Workspace **Deploy** button or preview with **Run**. It has its own warehouse and compute pool settings (`ADHOC_WH`, `STREAMLIT_COMPUTE_POOL`), separate from the notebook's.

## Project structure

```
├── install.ipynb                 # Installer notebook (run as ACCOUNTADMIN)
├── README.md
└── ai-access-manager/
    ├── streamlit_app.py          # Entry point, navigation, sidebar, page-level error handling
    ├── data.py                   # Data layer: SQL queries, caching, GRANT/REVOKE builders, error helpers
    ├── app_pages/
    │   ├── home.py               # Dashboard and settings audit
    │   ├── model_catalog.py      # Read-only model catalog
    │   ├── model_access.py       # Model RBAC admin
    │   ├── feature_access.py     # Feature-level access admin
    │   ├── coco_on_off.py        # CoCo surface controls
    │   ├── verify_role.py        # Role verification
    │   └── help.py               # FAQ and glossary
    ├── snowflake.yml             # Workspace deployment manifest
    ├── pyproject.toml            # Python dependencies
    └── .streamlit/config.toml    # Streamlit theme config
```
