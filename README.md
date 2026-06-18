# Scoreboard Combo

Scoreboard Combo is a Streamlit workflow for public-finance scorecard work:
source issuer inputs, run methodology formulas, enter manual qualitative
scores, produce an indicative rating, and export an audit trail.

The app is intended for model replication and analyst workflow automation. It
does not produce a rating action.

## What It Supports

- Moody's CCD GO
- Moody's K-12
- S&P Local Gov / K-12 GO
- S&P Water / Sewer Utility
- S&P Community College GO

Core workflow pages:

- `Workflow`: deal setup, source data, formula calculation, manual scores, and
  scoreboard output.
- `Data Confirmation`: evidence queue, ACFR/API checks, AI-assisted field
  extraction, and approved-value application.
- `Developer Tools`: methodology audits, clean-data simulation, session exports,
  and diagnostic tables.

## Project Layout

- `streamlit_app.py`: main Workflow page.
- `pages/`: secondary Streamlit pages.
- `engine/`: formula, factor, rating, audit, validation, and regression logic.
- `utils/`: Streamlit UI helpers, source workflow, manual-score controls, and
  data-confirmation workflows.
- `connectors/`: Census, BEA, IPEDS, and CreditScope workbook connectors.
- `config/`: formula library, thresholds, mappings, source registry, and
  validation fixtures.
- `templates/`: methodology factor templates.
- `methodologies/`: methodology reference notes and metadata.

## Local Setup

Use Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The local app usually opens at <http://localhost:8501>.

## Optional Secrets

The app can run without API keys, but some source and AI extraction workflows
will be disabled.

Create `.streamlit/secrets.toml` locally when needed:

```toml
OPENAI_API_KEY = "..."
OPENAI_MODEL = "gpt-4.1-mini"
CENSUS_API_KEY = "..."
BEA_API_KEY = "..."
```

`.streamlit/secrets.toml` is intentionally ignored by git.

## Verification

Run syntax/import compilation:

```bash
python -m compileall -q streamlit_app.py pages engine utils connectors
```

Run the regression baseline tests:

```bash
python -m unittest discover -s tests -v
```

You can also run the regression helpers directly:

```bash
python -c "from engine.regression_engine import run_synthetic_clean_data_regression; print(run_synthetic_clean_data_regression().to_string(index=False))"
python -c "from engine.regression_engine import run_raw_validation_regression; print(run_raw_validation_regression().to_string(index=False))"
```

## Current Regression Baseline

The clean-data regression currently passes for Moody's CCD GO, Moody's K-12, and
S&P Water / Sewer Utility. It intentionally tracks known gaps for:

- `sp_local_gov_k12`
- `sp_community_college_go`

Those known gaps are captured in `tests/test_regression_baselines.py` so CI can
catch new regressions while the remaining methodology gaps are tightened.

## Deployment Notes

For Streamlit Community Cloud, set the app entry point to `streamlit_app.py` and
configure any optional API keys in app secrets. Keep uploaded issuer files and
session output out of git unless they are intentionally converted into
sanitized fixtures.
