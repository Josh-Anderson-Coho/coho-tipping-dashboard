import base64
import glob
import os
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Coho Footy Tipping Dashboard", layout="wide")

DATA_DIR = Path("data")
UPLOAD_PASSWORD = "C@H@"


def extract_round_number(filename: str):
    """Detect round number from ESPN filename. For finals, continue with the next round number."""
    m = re.search(r"nrl-(\d+)", filename.lower())
    return int(m.group(1)) if m else None


def find_column(columns, exact_options=None, contains_options=None):
    exact_options = exact_options or []
    contains_options = contains_options or []
    lookup = {str(c).strip().lower(): c for c in columns}
    for opt in exact_options:
        if opt.lower() in lookup:
            return lookup[opt.lower()]
    for c in columns:
        c_low = str(c).strip().lower()
        for opt in contains_options:
            if opt.lower() in c_low:
                return c
    return None


def round_tip_columns(columns):
    found = []
    for c in columns:
        m = re.fullmatch(r"ROUND\s+(\d+)", str(c).strip(), re.I)
        if m:
            found.append((int(m.group(1)), c))
    return sorted(found)


def round_margin_columns(columns):
    found = []
    for c in columns:
        m = re.fullmatch(r"ROUND\s+(\d+)\s+MARGIN", str(c).strip(), re.I)
        if m:
            found.append((int(m.group(1)), c))
    return sorted(found)


@st.cache_data(ttl=60)
def load_teams():
    path = DATA_DIR / "Teams.csv"
    if not path.exists():
        return pd.DataFrame(columns=["Name", "Team"])

    teams = pd.read_csv(path)
    teams.columns = [str(c).strip() for c in teams.columns]

    name_col = find_column(teams.columns, ["Name", "NAME", "Entrant", "Player", "Tipper"])
    team_col = find_column(teams.columns, ["Team", "TEAM", "Group"])

    if name_col is None or team_col is None:
        return pd.DataFrame(columns=["Name", "Team"])

    teams = teams.rename(columns={name_col: "Name", team_col: "Team"})
    teams["Name"] = teams["Name"].astype(str).str.strip()
    teams["Team"] = teams["Team"].astype(str).str.strip()
    return teams[["Name", "Team"]].drop_duplicates()


@st.cache_data(ttl=60)
def load_all_rounds():
    """
    Loads every ESPN CSV in /data and builds one row per entrant per round.
    ESPN adds columns each week, so this does not rely on a fixed schema.
    """
    files = sorted(glob.glob(str(DATA_DIR / "competition-Coho Footy Tipping-nrl-*.csv")))
    weekly_rows = []
    skipped = []

    for file in files:
        filename = os.path.basename(file)
        file_round = extract_round_number(filename)
        if file_round is None:
            skipped.append(filename)
            continue

        raw = pd.read_csv(file)
        raw.columns = [str(c).strip() for c in raw.columns]
        name_col = find_column(raw.columns, ["NAME", "Name", "Entrant", "Player", "Tipper"])

        if name_col is None:
            skipped.append(filename)
            continue

        tips_col = find_column(raw.columns, [f"ROUND {file_round}", f"Round {file_round}"])
        margin_col = find_column(raw.columns, [f"ROUND {file_round} MARGIN", f"Round {file_round} Margin"])

        if tips_col is None:
            tips_found = round_tip_columns(raw.columns)
            tips_col = tips_found[-1][1] if tips_found else None
            file_round = tips_found[-1][0] if tips_found else file_round

        if margin_col is None:
            margin_found = round_margin_columns(raw.columns)
            margin_col = margin_found[-1][1] if margin_found else None

        if tips_col is None:
            skipped.append(filename)
            continue

        out = pd.DataFrame({
            "Name": raw[name_col].astype(str).str.strip(),
            "Round": int(file_round),
            "Round Tips": pd.to_numeric(raw[tips_col], errors="coerce").fillna(0),
            "Round Margin": pd.to_numeric(raw[margin_col], errors="coerce").fillna(0) if margin_col is not None else 0,
            "Source File": filename,
        })
        weekly_rows.append(out)

    if not weekly_rows:
        return pd.DataFrame(), skipped

    weekly = pd.concat(weekly_rows, ignore_index=True)
    weekly = weekly.drop_duplicates(subset=["Name", "Round"], keep="last")

    names = sorted(weekly["Name"].unique(), key=lambda x: str(x).lower())
    rounds = sorted(weekly["Round"].unique())
    grid = pd.MultiIndex.from_product([names, rounds], names=["Name", "Round"]).to_frame(index=False)
    full = grid.merge(weekly, on=["Name", "Round"], how="left")
    full["Round Tips"] = pd.to_numeric(full["Round Tips"], errors="coerce").fillna(0)
    full["Round Margin"] = pd.to_numeric(full["Round Margin"], errors="coerce").fillna(0)
    full["Source File"] = full["Source File"].fillna("")

    full = full.sort_values(["Name", "Round"])
    full["Total Score"] = full.groupby("Name")["Round Tips"].cumsum()
    full["Total Margin"] = full.groupby("Name")["Round Margin"].cumsum()

    ranked_frames = []
    for round_no in rounds:
        r = full[full["Round"] == round_no].copy()
        r = r.sort_values(["Total Score", "Total Margin", "Name"], ascending=[False, True, True])
        r["Rank"] = range(1, len(r) + 1)
        ranked_frames.append(r)

    data = pd.concat(ranked_frames, ignore_index=True)
    return data, skipped


def get_secret_value(*names, default=None):
    for name in names:
        try:
            cur = st.secrets
            for part in name.split('.'):
                cur = cur[part]
            if cur:
                return cur
        except Exception:
            pass
    return default


def upload_csv_to_github(uploaded_file, round_number: int):
    token = get_secret_value('GITHUB_TOKEN', 'github.token')
    repo = get_secret_value('REPO_NAME', 'github.repo')
    branch = get_secret_value('GITHUB_BRANCH', 'github.branch', default='main')
    data_path = get_secret_value('GITHUB_DATA_PATH', 'github.data_path', default='data')

    if not token or not repo:
        return False, 'GitHub upload is not configured. In Streamlit Secrets add GITHUB_TOKEN and REPO_NAME.'
    if uploaded_file is None:
        return False, 'Choose a CSV file first.'
    if not uploaded_file.name.lower().endswith('.csv'):
        return False, 'Only CSV files can be uploaded.'

    safe_round = int(round_number)
    filename = f'competition-Coho Footy Tipping-nrl-{safe_round}.csv'
    content_bytes = uploaded_file.getvalue()
    if not content_bytes:
        return False, 'The uploaded file looks empty.'

    encoded = base64.b64encode(content_bytes).decode('utf-8')
    repo_path = f'{data_path}/{filename}'
    url = f'https://api.github.com/repos/{repo}/contents/{repo_path}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    sha = None
    existing = requests.get(url, headers=headers, params={'ref': branch}, timeout=20)
    if existing.status_code == 200:
        sha = existing.json().get('sha')
    elif existing.status_code != 404:
        return False, f'Could not check existing file: {existing.status_code} - {existing.text[:300]}'

    payload = {
        'message': f'Add/update Coho tipping data for round {safe_round}',
        'content': encoded,
        'branch': branch,
    }
    if sha:
        payload['sha'] = sha

    response = requests.put(url, headers=headers, json=payload, timeout=30)
    if response.status_code in (200, 201):
        st.cache_data.clear()
        return True, f'Uploaded {filename} to GitHub. Streamlit will redeploy automatically. Refresh in about 30-60 seconds.'

    return False, f'GitHub upload failed: {response.status_code} - {response.text[:500]}'


raw_data, skipped_files = load_all_rounds()
teams = load_teams()

st.title("🏉 Coho Footy Tipping Dashboard")

st.markdown("""
<style>
    .block-container {padding-top: 1.2rem; padding-left: 1rem; padding-right: 1rem; max-width: 1500px;}
    div[data-testid="stMetric"] {background: rgba(250,250,250,0.03); padding: 0.6rem; border-radius: 0.6rem;}
    .stDataFrame {width: 100%;}
    @media (max-width: 900px) {
        section.main .block-container {padding-left: 0.5rem; padding-right: 0.5rem;}
        div[data-testid="column"] {width: 100% !important; flex: 1 1 100% !important; min-width: 100% !important;}
        div[data-testid="stHorizontalBlock"] {flex-wrap: wrap !important;}
        .stDataFrame {font-size: 0.82rem;}
        h1 {font-size: 1.55rem !important;}
        h2, h3 {font-size: 1.15rem !important;}
    }
</style>
""", unsafe_allow_html=True)

if raw_data.empty:
    st.warning("No ESPN round CSV files found in the data folder.")
    st.stop()

if not teams.empty:
    data = raw_data.merge(teams, on="Name", how="left")
else:
    data = raw_data.copy()
    data["Team"] = "Unassigned"

data["Team"] = data["Team"].fillna("Unassigned")
rounds = sorted(data["Round"].unique())
entrants = sorted(data["Name"].unique(), key=lambda x: str(x).lower())
teams_available = sorted([t for t in data["Team"].dropna().unique()], key=lambda x: str(x).lower())

st.sidebar.title("Controls")
selected_round = st.sidebar.selectbox("Select round", rounds, index=len(rounds) - 1)
highlight_names = st.sidebar.multiselect("Highlight entrants", entrants)
highlight_teams = st.sidebar.multiselect("Highlight teams", teams_available)
show_all = st.sidebar.checkbox("Show all entrants", value=True)

st.sidebar.divider()
st.sidebar.subheader("Admin upload")
st.sidebar.caption("Upload a new ESPN CSV and save it into the GitHub data folder.")
admin_password = st.sidebar.text_input("Upload password", type="password")
next_round = int(max(rounds) + 1) if rounds else 1
upload_round = st.sidebar.number_input(
    "Save as round number",
    min_value=1,
    max_value=60,
    value=next_round,
    step=1,
    help="Use the next NRL round number. For finals, continue numbering after the regular season.",
)
new_file = st.sidebar.file_uploader("Choose ESPN CSV", type=["csv"])
if st.sidebar.button("Upload CSV to GitHub", type="primary"):
    if admin_password != UPLOAD_PASSWORD:
        st.sidebar.error("Incorrect password.")
    else:
        ok, msg = upload_csv_to_github(new_file, upload_round)
        (st.sidebar.success if ok else st.sidebar.error)(msg)

if skipped_files:
    st.sidebar.warning("Some files were skipped: " + ", ".join(skipped_files))

current = data[data["Round"] == selected_round].copy().sort_values("Rank")
previous = data[data["Round"] == selected_round - 1][["Name", "Rank"]].rename(columns={"Rank": "Previous Rank"})
current = current.merge(previous, on="Name", how="left")
current["Movement"] = current["Previous Rank"] - current["Rank"]

tipster = current.sort_values(["Round Tips", "Round Margin", "Name"], ascending=[False, True, True]).iloc[0]
if current["Movement"].notna().any():
    mover = current.sort_values(["Movement", "Round Margin"], ascending=[False, True]).iloc[0]
    dropper = current.sort_values(["Movement", "Round Margin"], ascending=[True, True]).iloc[0]
else:
    mover = dropper = None
middlest = current[current["Rank"] == 26]

team_stats = current.groupby("Team", dropna=False).agg(
    Average_Total_Tips=("Total Score", "mean"),
    Average_Total_Margin=("Total Margin", "mean"),
    Participants=("Name", "count"),
).reset_index().sort_values(["Average_Total_Tips", "Average_Total_Margin", "Team"], ascending=[False, True, True])
team_stats["Team Rank"] = range(1, len(team_stats) + 1)

a, b, c, d = st.columns(4)
a.metric("Tipster of the Round", tipster["Name"], f'{int(tipster["Round Tips"])} tips / {int(tipster["Round Margin"])} margin')
if mover is not None:
    b.metric("Mover & Shaker", mover["Name"], f'+{int(mover["Movement"])} places')
else:
    b.metric("Mover & Shaker", "N/A")
if dropper is not None:
    c.metric("Shooting Star", dropper["Name"], f'{int(dropper["Movement"])} places')
else:
    c.metric("Shooting Star", "N/A")
if not middlest.empty:
    d.metric("Middlest Watch", middlest.iloc[0]["Name"], "26th place")
else:
    d.metric("Middlest Watch", "N/A")

st.divider()

st.subheader("Rank Tracking")
team_highlight_names = set(data[data["Team"].isin(highlight_teams)]["Name"].unique())
all_highlight_names = set(highlight_names) | team_highlight_names

if show_all:
    chart_names = entrants
elif all_highlight_names:
    chart_names = sorted(all_highlight_names, key=lambda x: str(x).lower())
else:
    chart_names = sorted(current.head(10)["Name"].tolist(), key=lambda x: str(x).lower())
    st.info("Showing current top 10. Tick 'Show all entrants' or choose highlights in the sidebar.")

fig = go.Figure()
for name in chart_names:
    person = data[data["Name"] == name].sort_values("Round")
    is_highlight = name in all_highlight_names
    if all_highlight_names:
        width = 6 if is_highlight else 1.2
        opacity = 1.0 if is_highlight else 0.22
        marker_size = 11 if is_highlight else 4
    else:
        width = 2
        opacity = 0.85
        marker_size = 5
    fig.add_trace(go.Scatter(
        x=person["Round"],
        y=person["Rank"],
        mode="lines+markers",
        name=name,
        line={"width": width},
        marker={"size": marker_size, "line": {"width": 2 if is_highlight else 0}},
        opacity=opacity,
        customdata=person[["Round Tips", "Round Margin", "Total Score", "Total Margin", "Team"]],
        hovertemplate=(
            "%{fullData.name}<br>Round %{x}<br>Rank %{y}"
            "<br>Round tips %{customdata[0]}<br>Round margin %{customdata[1]}"
            "<br>Total tips %{customdata[2]}<br>Total margin %{customdata[3]}"
            "<br>Team %{customdata[4]}<extra></extra>"
        ),
    ))
fig.update_yaxes(autorange="reversed", title="Rank")
fig.update_xaxes(dtick=1, title="Round")
fig.update_layout(height=650, legend_title_text="Entrant", legend_traceorder="normal")
st.plotly_chart(fig, use_container_width=True)

st.subheader("Round Movement — Biggest Climbers and Fallers")
movement_chart = current.dropna(subset=["Movement"]).copy()
if movement_chart.empty:
    st.info("Movement chart will appear from Round 2 onwards.")
else:
    movement_chart = movement_chart.sort_values("Movement", ascending=True)
    bar = px.bar(
        movement_chart,
        x="Movement",
        y="Name",
        orientation="h",
        color="Movement",
        color_continuous_scale="Plasma",
        hover_data=["Rank", "Previous Rank", "Round Tips", "Round Margin", "Total Score", "Total Margin", "Team"],
        labels={"Movement": "Rank Change", "Name": ""},
    )
    bar.update_layout(height=max(650, 22 * len(movement_chart)), coloraxis_colorbar_title="Rank Change")
    st.plotly_chart(bar, use_container_width=True)

st.subheader(f"Round {selected_round} Leaderboard")
display_current = current.sort_values("Rank")[[
    "Rank", "Name", "Round Tips", "Round Margin", "Total Score", "Total Margin", "Movement", "Team"
]].rename(columns={
    "Total Score": "Total Tips",
})
st.dataframe(display_current, use_container_width=True, hide_index=True, height=520)

st.subheader(f"Round {selected_round} Team Leaderboard — Season Total Average")
display_teams = team_stats[["Team Rank", "Team", "Average_Total_Tips", "Average_Total_Margin", "Participants"]].copy()
display_teams = display_teams.rename(columns={
    "Average_Total_Tips": "Avg Total Tips",
    "Average_Total_Margin": "Avg Total Margin",
})
display_teams["Avg Total Tips"] = display_teams["Avg Total Tips"].round(2)
display_teams["Avg Total Margin"] = display_teams["Avg Total Margin"].round(2)
st.dataframe(display_teams, use_container_width=True, hide_index=True, height=420)
