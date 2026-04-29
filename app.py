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
    """Detect round number from ESPN filename, including finals if ESPN continues numbering files."""
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
def load_round_files():
    files = glob.glob(str(DATA_DIR / "competition-Coho Footy Tipping-nrl-*.csv"))
    frames = []
    skipped = []

    for file in files:
        round_no = extract_round_number(os.path.basename(file))
        if round_no is None:
            skipped.append(os.path.basename(file))
            continue

        raw = pd.read_csv(file)
        raw.columns = [str(c).strip() for c in raw.columns]
        name_col = find_column(raw.columns, ["NAME", "Name", "Entrant", "Player", "Tipper"])
        rank_col = find_column(raw.columns, ["RANK", "Rank"])
        total_score_col = find_column(raw.columns, ["TOTAL SCORE", "Total Score", "Score", "Points"])
        total_margin_col = find_column(raw.columns, ["TOTAL MARGIN", "Total Margin"])
        round_tips_col = find_column(raw.columns, [f"ROUND {round_no}", f"Round {round_no}"])
        round_margin_col = find_column(raw.columns, [f"ROUND {round_no} MARGIN", f"Round {round_no} Margin"])

        if name_col is None:
            skipped.append(os.path.basename(file))
            continue

        out = pd.DataFrame()
        out["Name"] = raw[name_col].astype(str).str.strip()
        out["Round"] = round_no
        out["Source File"] = os.path.basename(file)

        out["Rank"] = pd.to_numeric(raw[rank_col], errors="coerce") if rank_col is not None else pd.NA

        if round_tips_col is not None:
            out["Round Tips"] = pd.to_numeric(raw[round_tips_col], errors="coerce").fillna(0)
        else:
            round_tip_cols = [c for c in raw.columns if re.fullmatch(r"ROUND \d+", str(c).strip(), re.I)]
            out["Round Tips"] = pd.to_numeric(raw[round_tip_cols[-1]], errors="coerce").fillna(0) if round_tip_cols else 0

        if round_margin_col is not None:
            out["Round Margin"] = pd.to_numeric(raw[round_margin_col], errors="coerce").fillna(999999)
        else:
            margin_cols = [c for c in raw.columns if re.fullmatch(r"ROUND \d+ MARGIN", str(c).strip(), re.I)]
            out["Round Margin"] = pd.to_numeric(raw[margin_cols[-1]], errors="coerce").fillna(999999) if margin_cols else 999999

        if total_score_col is not None:
            out["Total Score"] = pd.to_numeric(raw[total_score_col], errors="coerce").fillna(0)
        else:
            score_cols = [c for c in raw.columns if re.fullmatch(r"ROUND \d+", str(c).strip(), re.I)]
            out["Total Score"] = raw[score_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1) if score_cols else out["Round Tips"]

        if total_margin_col is not None:
            out["Total Margin"] = pd.to_numeric(raw[total_margin_col], errors="coerce").fillna(999999)
        else:
            margin_cols = [c for c in raw.columns if re.fullmatch(r"ROUND \d+ MARGIN", str(c).strip(), re.I)]
            out["Total Margin"] = raw[margin_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1) if margin_cols else out["Round Margin"]

        if out["Rank"].isna().all():
            out = out.sort_values(["Total Score", "Total Margin", "Name"], ascending=[False, True, True])
            out["Rank"] = range(1, len(out) + 1)

        frames.append(out)

    if not frames:
        return pd.DataFrame(), skipped

    df = pd.concat(frames, ignore_index=True)
    df["Round"] = df["Round"].astype(int)
    df["Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
    return df, skipped


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


raw_data, skipped_files = load_round_files()
teams = load_teams()

st.title("🏉 Coho Footy Tipping Dashboard")

st.markdown("""
<style>
    .block-container {padding-top: 1.2rem; padding-left: 1rem; padding-right: 1rem;}
    div[data-testid="stMetric"] {background: rgba(250,250,250,0.03); padding: 0.6rem; border-radius: 0.6rem;}
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
entrants = sorted(data["Name"].unique())
teams_available = sorted([t for t in data["Team"].dropna().unique()])

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
    max_value=40,
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
    chart_names = sorted(all_highlight_names)
else:
    chart_names = current.head(10)["Name"].tolist()
    st.info("Showing current top 10. Tick 'Show all entrants' or choose highlights in the sidebar.")

fig = go.Figure()
for name in chart_names:
    person = data[data["Name"] == name].sort_values("Round")
    is_highlight = name in all_highlight_names
    if all_highlight_names:
        width = 5 if is_highlight else 1
        opacity = 1.0 if is_highlight else 0.20
    else:
        width = 2
        opacity = 0.85
    fig.add_trace(go.Scatter(
        x=person["Round"],
        y=person["Rank"],
        mode="lines+markers",
        name=name,
        line={"width": width},
        marker={"size": 10 if is_highlight else 5, "line": {"width": 2 if is_highlight else 0}},
        opacity=opacity,
        customdata=person[["Round Tips", "Round Margin", "Team"]],
        hovertemplate="%{fullData.name}<br>Round %{x}<br>Rank %{y}<br>Tips %{customdata[0]}<br>Margin %{customdata[1]}<br>Team %{customdata[2]}<extra></extra>",
    ))
fig.update_yaxes(autorange="reversed", title="Rank")
fig.update_xaxes(dtick=1, title="Round")
fig.update_layout(height=650, legend_title_text="Entrant")
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
        hover_data=["Rank", "Previous Rank", "Round Tips", "Round Margin", "Team"],
        labels={"Movement": "Rank Change", "Name": ""},
    )
    bar.update_layout(height=max(650, 22 * len(movement_chart)), coloraxis_colorbar_title="Rank Change")
    st.plotly_chart(bar, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader(f"Round {selected_round} Leaderboard")
    display_current = current.sort_values("Rank")[["Rank", "Name", "Round Tips", "Round Margin", "Total Score", "Total Margin", "Movement", "Team"]]
    st.dataframe(display_current, use_container_width=True, hide_index=True)
with right:
    st.subheader(f"Round {selected_round} Team Leaderboard — Season Total Average")
    display_teams = team_stats[["Team Rank", "Team", "Average_Total_Tips", "Average_Total_Margin", "Participants"]].copy()
    display_teams = display_teams.rename(columns={
        "Average_Total_Tips": "Avg Total Tips",
        "Average_Total_Margin": "Avg Total Margin",
    })
    display_teams["Avg Total Tips"] = display_teams["Avg Total Tips"].round(2)
    display_teams["Avg Total Margin"] = display_teams["Avg Total Margin"].round(2)
    st.dataframe(display_teams, use_container_width=True, hide_index=True)
