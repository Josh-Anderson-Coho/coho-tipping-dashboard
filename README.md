# Coho Footy Tipping Dashboard

Streamlit dashboard for Coho Footy Tipping.

## GitHub upload setup

Add these to Streamlit Secrets:

```toml
GITHUB_TOKEN = "your_token_here"
REPO_NAME = "Josh-Anderson-Coho/coho-tipping-dashboard"
GITHUB_BRANCH = "main"
GITHUB_DATA_PATH = "data"
```

The admin upload password in the app is `C@H@`.

Uploaded CSVs are saved as:

`data/competition-Coho Footy Tipping-nrl-<round>.csv`

For finals, continue the round numbering after the regular season.
