import base64
import glob
import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Coho Footy Tipping Dashboard", layout="wide")

DATA_DIR = "data"
ADMIN_PASSWORD = "C@H@"

st.markdown(
    """
    <style>
    .block-container {padding-top: 2.25rem; padding-bottom: 2rem; max-width: 1400px;}
    h1 {line-height: 1.15 !important; padding-top: 0.2rem; margin-top: 0 !important;}
    h2, h3 {line-height: 1.2 !important;}
    [data-testid="stMetricValue"] {font-size: 1.5rem;}
    .stDataFrame {width: 100%;}
    @media (max-width: 800px) {
      .block-container {padding-left: 0.75rem; padding-right: 0.75rem; padding-top: 1.5rem;}
      [data-testid="stMetricValue"] {font-size: 1.15rem;}
      h1 {font-size: 1.8rem !important;}
      h2 {font-size: 1.35rem !important;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Helpers
# -----------------------------

def extract_round_number(filename: str):
    match = re.search(r"nrl-(\d+)", filename.lower())
    return int(match.group(1)) if match else None


def find_column(columns, possible_names):
    cleaned = {str(c).strip().lower(): c for c in columns}
    for name in possible_names:
        if name.lower() in cleaned:
            return cleaned[name.lower()]
    for c in columns:
        c_lower = str(c).strip().lower()
        for name in possible_names:
            if name.lower() in c_lower:
                return c
    return None


def round_col(df, round_no, margin=False):
    target = f"ROUND {round_no} MARGIN" if margin else f"ROUND {round_no}"
    for c in df.columns:
        if str(c).strip().upper() == target:
            return c
    return None


@st.cache_data(ttl=30)
def load_round_files():
    pattern = os.path.join(DATA_DIR, "competition-Coho Footy Tipping-nrl-*.csv")
    files = glob.glob(pattern)
    by_round = {}
    for file in files:
        r = extract_round_number(os.path.basename(file))
        if r is None:
            continue
        # If duplicates exist, prefer the file without brackets in the name, otherwise the newest modified file.
        current = by_round.get(r)
        if current is None:
            by_round[r] = file
        else:
            cur_base = os.path.basename(current)
            new_base = os.path.basename(file)
            if "(" in cur_base and "(" not in new_base:
                by_round[r] = file
            elif os.path.getmtime(file) > os.path.getmtime(current) and ("(" in new_base) == ("(" in cur_base):
                by_round[r] = file

    snapshots = []
    for r, file in sorted(by_round.items()):
        df = pd.read_csv(file)
        df.columns = [str(c).strip() for c in df.columns]
        name_col = find_column(df.columns, ["NAME", "Name", "Entrant", "Player", "Tipper"])
        if name_col is None:
            continue
        df["Name"] = df[name_col].astype(str).str.strip()
        df["Round"] = r
        df["Source File"] = os.path.basename(file)

        rank_c = find_column(df.columns, ["RANK", "Rank"])
        total_score_c = find_column(df.columns, ["TOTAL SCORE", "Total Score", "Total Tips", "Score"])
        total_margin_c = find_column(df.columns, ["TOTAL MARGIN", "Total Margin"])
        wk_score_c = round_col(df, r, margin=False)
        wk_margin_c = round_col(df, r, margin=True)

        df["Official Rank"] = pd.to_numeric(df[rank_c], errors="coerce") if rank_c else None
        df["Round Tips"] = pd.to_numeric(df[wk_score_c], errors="coerce") if wk_score_c else 0
        df["Round Margin"] = pd.to_numeric(df[wk_margin_c], errors="coerce") if wk_margin_c else 0
        df["Total Tips File"] = pd.to_numeric(df[total_score_c], errors="coerce") if total_score_c else None
        df["Total Margin File"] = pd.to_numeric(df[total_margin_c], errors="coerce") if total_margin_c else None
        snapshots.append(df[["Name", "Round", "Source File", "Official Rank", "Round Tips", "Round Margin", "Total Tips File", "Total Margin File"]])

    if not snapshots:
        return pd.DataFrame()
    return pd.concat(snapshots, ignore_index=True)


@st.cache_data(ttl=30)
def load_teams():
    team_path = os.path.join(DATA_DIR, "Teams.csv")
    if not os.path.exists(team_path):
        return pd.DataFrame(columns=["Name", "Team"])
    teams = pd.read_csv(team_path)
    teams.columns = [str(c).strip() for c in teams.columns]
    name_col = find_column(teams.columns, ["Name", "NAME", "Entrant", "Player", "Tipper"])
    team_col = find_column(teams.columns, ["Team", "TEAM", "Group"])
    if name_col is None or team_col is None:
        return pd.DataFrame(columns=["Name", "Team"])
    teams = teams.rename(columns={name_col: "Name", team_col: "Team"})
    teams["Name"] = teams["Name"].astype(str).str.strip()
    teams["Team"] = teams["Team"].astype(str).str.strip()
    return teams[["Name", "Team"]].drop_duplicates()


def build_history(raw):
    if raw.empty:
        return raw
    rows = []
    names = sorted(raw["Name"].unique(), key=lambda x: x.lower())
    rounds = sorted(raw["Round"].unique())
    running = {name: {"tips": 0.0, "margin": 0.0} for name in names}

    for r in rounds:
        rd = raw[raw["Round"] == r].copy()
        for _, row in rd.iterrows():
            name = row["Name"]
            wk_tips = pd.to_numeric(row["Round Tips"], errors="coerce")
            wk_margin = pd.to_numeric(row["Round Margin"], errors="coerce")
            wk_tips = 0 if pd.isna(wk_tips) else float(wk_tips)
            wk_margin = 0 if pd.isna(wk_margin) else float(wk_margin)

            running.setdefault(name, {"tips": 0.0, "margin": 0.0})
            running[name]["tips"] += wk_tips
            running[name]["margin"] += wk_margin

            total_tips_file = row.get("Total Tips File")
            total_margin_file = row.get("Total Margin File")
            total_tips = running[name]["tips"] if pd.isna(total_tips_file) else float(total_tips_file)
            total_margin = running[name]["margin"] if pd.isna(total_margin_file) else float(total_margin_file)
            # Keep the running values aligned to official totals where available.
            running[name]["tips"] = total_tips
            running[name]["margin"] = total_margin

            rows.append({
                "Name": name,
                "Round": int(r),
                "Round Tips": wk_tips,
                "Round Margin": wk_margin,
                "Total Tips": total_tips,
                "Total Margin": total_margin,
                "Source File": row.get("Source File", ""),
            })
    hist = pd.DataFrame(rows)
    return rank_all_rounds(hist)


def rank_all_rounds(hist):
    frames = []
    for r in sorted(hist["Round"].unique()):
        rd = hist[hist["Round"] == r].copy()
        rd = rd.sort_values(["Total Tips", "Total Margin", "Name"], ascending=[False, True, True])
        rd["Rank"] = range(1, len(rd) + 1)
        frames.append(rd)
    return pd.concat(frames, ignore_index=True)


def commit_file_to_github(uploaded_file, target_name):
    token = st.secrets.get("GITHUB_TOKEN", None)
    repo_name = st.secrets.get("REPO_NAME", None)
    if not token or not repo_name:
        raise RuntimeError("Missing GITHUB_TOKEN or REPO_NAME in Streamlit Secrets.")

    content_bytes = uploaded_file.getvalue()
    encoded = base64.b64encode(content_bytes).decode("utf-8")
    path = f"data/{target_name}"
    url = f"https://api.github.com/repos/{repo_name}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    sha = None
    existing = requests.get(url, headers=headers, timeout=20)
    if existing.status_code == 200:
        sha = existing.json().get("sha")
    elif existing.status_code not in (404,):
        raise RuntimeError(f"GitHub lookup failed: {existing.status_code} {existing.text}")

    payload = {
        "message": f"Add/update tipping data {target_name}",
        "content": encoded,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    response = requests.put(url, headers=headers, json=payload, timeout=30)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"GitHub upload failed: {response.status_code} {response.text}")
    return response.json()


def fmt_int(v):
    try:
        return f"{int(round(float(v)))}"
    except Exception:
        return "0"

# -----------------------------
# Load and prepare data
# -----------------------------
raw = load_round_files()
if raw.empty:
    st.title("🏉 Coho Footy Tipping Dashboard")
    st.warning("No round CSV files found. Add your ESPN CSV files to the data folder.")
    st.stop()

teams = load_teams()
history = build_history(raw)
if not teams.empty:
    history = history.merge(teams, on="Name", how="left")
history["Team"] = history.get("Team", "Unassigned").fillna("Unassigned")

available_rounds = sorted(history["Round"].unique())
latest_round = max(available_rounds)
entrant_count = history[history["Round"] == latest_round]["Name"].nunique()

# -----------------------------
# Sidebar controls
# -----------------------------
st.sidebar.title("Controls")
selected_round = st.sidebar.select_slider("Round", options=available_rounds, value=latest_round)

all_names = sorted(history["Name"].unique(), key=lambda x: x.lower())
selected_names = st.sidebar.multiselect("Highlight entrants", all_names, default=[])
team_options = sorted([t for t in history["Team"].dropna().unique() if t != "Unassigned"], key=lambda x: x.lower())
selected_teams = st.sidebar.multiselect("Highlight team members", team_options, default=[])

with st.sidebar.expander("Admin: upload new round CSV", expanded=False):
    password = st.text_input("Admin password", type="password", key="admin_password")
    uploaded_file = st.file_uploader("Upload ESPN CSV to GitHub data folder", type=["csv"], key="github_upload")
    if uploaded_file is not None:
        if password != ADMIN_PASSWORD:
            st.error("Incorrect password.")
        else:
            safe_name = os.path.basename(uploaded_file.name).replace(" ", " ")
            if not re.search(r"nrl-\d+", safe_name.lower()):
                st.error("Filename must include the round number, for example competition-Coho Footy Tipping-nrl-9.csv")
            else:
                if st.button("Commit uploaded CSV to GitHub", type="primary"):
                    try:
                        commit_file_to_github(uploaded_file, safe_name)
                        st.success(f"Uploaded {safe_name} to GitHub. Streamlit should redeploy shortly.")
                        st.cache_data.clear()
                    except Exception as exc:
                        st.error(str(exc))

# -----------------------------
# Current round data
# -----------------------------
current = history[history["Round"] == selected_round].copy().sort_values("Rank")
prev = history[history["Round"] == selected_round - 1][["Name", "Rank"]].rename(columns={"Rank": "Previous Rank"})
current = current.merge(prev, on="Name", how="left")
current["Movement"] = current["Previous Rank"] - current["Rank"]

tipster = current.sort_values(["Round Tips", "Round Margin", "Name"], ascending=[False, True, True]).iloc[0]
mover = current[current["Movement"].notna()].sort_values(["Movement", "Round Margin"], ascending=[False, True]).head(1)
dropper = current[current["Movement"].notna()].sort_values(["Movement", "Round Margin"], ascending=[True, True]).head(1)
middlest = current[current["Rank"] == 26]

team_stats = current.groupby("Team", dropna=False).agg(
    Avg_Total_Tips=("Total Tips", "mean"),
    Avg_Total_Margin=("Total Margin", "mean"),
    Participants=("Name", "count"),
).reset_index()
team_stats = team_stats.sort_values(["Avg_Total_Tips", "Avg_Total_Margin", "Team"], ascending=[False, True, True])
team_stats["Team Rank"] = range(1, len(team_stats) + 1)

# -----------------------------
# Header and metrics
# -----------------------------
st.title("🏉 Coho Footy Tipping Dashboard")

metric_cols = st.columns(4)
metric_cols[0].metric("Tipster of the Round", tipster["Name"], f"{fmt_int(tipster['Round Tips'])} tips / {fmt_int(tipster['Round Margin'])} margin")
if not mover.empty:
    metric_cols[1].metric("Mover & Shaker", mover.iloc[0]["Name"], f"+{fmt_int(mover.iloc[0]['Movement'])} places")
else:
    metric_cols[1].metric("Mover & Shaker", "N/A")
if not dropper.empty:
    metric_cols[2].metric("Shooting Star", dropper.iloc[0]["Name"], f"{fmt_int(dropper.iloc[0]['Movement'])} places")
else:
    metric_cols[2].metric("Shooting Star", "N/A")
if not middlest.empty:
    metric_cols[3].metric("Middlest Watch", middlest.iloc[0]["Name"], "26th place")
else:
    metric_cols[3].metric("Middlest Watch", "N/A")

st.divider()

# -----------------------------
# Rank chart with highlighting
# -----------------------------
st.subheader("Rank Tracking")
team_highlight_names = set(history[history["Team"].isin(selected_teams)]["Name"].unique())
highlight_names = set(selected_names) | team_highlight_names

fig = go.Figure()
for name in all_names:
    person = history[history["Name"] == name].sort_values("Round")
    is_highlighted = name in highlight_names
    fig.add_trace(go.Scatter(
        x=person["Round"],
        y=person["Rank"],
        mode="lines+markers",
        name=name,
        line=dict(width=5 if is_highlighted else 1.5),
        marker=dict(size=8 if is_highlighted else 4),
        opacity=1.0 if (not highlight_names or is_highlighted) else 0.22,
        hovertemplate="%{fullData.name}<br>Round %{x}<br>Rank %{y}<extra></extra>",
    ))
fig.update_yaxes(title="Rank", range=[entrant_count + 1, 0], fixedrange=True, autorange=False)
fig.update_xaxes(title="Round", dtick=1, fixedrange=True)
fig.update_layout(height=640, legend_title="Entrant", hovermode="closest")
st.plotly_chart(fig, use_container_width=True)

# -----------------------------
# Movement bar chart
# -----------------------------
st.subheader("Round Movement — Biggest Climbers and Fallers")
movement_data = current[current["Movement"].notna()].copy()
if not movement_data.empty:
    climbers = movement_data.sort_values("Movement", ascending=False).head(10)
    fallers = movement_data.sort_values("Movement", ascending=True).head(10)
    movement_plot = pd.concat([climbers, fallers]).drop_duplicates(subset=["Name"])
    movement_plot = movement_plot.sort_values("Movement", ascending=True)
    bar = px.bar(
        movement_plot,
        x="Movement",
        y="Name",
        orientation="h",
        color="Movement",
        color_continuous_scale="Plasma",
        labels={"Movement": "Rank Change", "Name": ""},
        hover_data=["Rank", "Previous Rank", "Total Tips", "Total Margin"],
    )
    bar.update_layout(height=max(500, 24 * len(movement_plot)), coloraxis_colorbar_title="Rank Change")
    st.plotly_chart(bar, use_container_width=True)
else:
    st.info("Movement data starts from Round 2.")

# -----------------------------
# Leaderboards stacked vertically
# -----------------------------
st.subheader(f"Round {selected_round} Leaderboard")
leaderboard = current.sort_values("Rank")[["Rank", "Name", "Round Tips", "Round Margin", "Total Tips", "Total Margin", "Movement", "Team"]].copy()
for c in ["Round Tips", "Round Margin", "Total Tips", "Total Margin", "Movement"]:
    leaderboard[c] = leaderboard[c].round(0).astype("Int64")
st.dataframe(leaderboard, use_container_width=True, hide_index=True, height=420)

st.subheader(f"Round {selected_round} Team Leaderboard — Season Total Average")
team_display = team_stats[["Team Rank", "Team", "Avg_Total_Tips", "Avg_Total_Margin", "Participants"]].copy()
team_display["Avg_Total_Tips"] = team_display["Avg_Total_Tips"].round(2)
team_display["Avg_Total_Margin"] = team_display["Avg_Total_Margin"].round(2)
st.dataframe(team_display, use_container_width=True, hide_index=True)

st.divider()

# -----------------------------
# Added insights
# -----------------------------
st.header("Season Insights")

st.subheader("Weeks Spent in the Lead")
leaders = history.loc[history.groupby("Round")["Rank"].idxmin()].copy()
weeks_lead = leaders.groupby("Name").size().reset_index(name="Weeks in Lead").sort_values(["Weeks in Lead", "Name"], ascending=[False, True])
lead_fig = px.bar(weeks_lead, x="Weeks in Lead", y="Name", orientation="h", labels={"Name": "", "Weeks in Lead": "Weeks in 1st"})
lead_fig.update_layout(height=max(340, 34 * len(weeks_lead)))
st.plotly_chart(lead_fig, use_container_width=True)

st.subheader("Consistency Ladder")
cons = history.sort_values(["Name", "Round"]).copy()
cons["Rank Change Abs"] = cons.groupby("Name")["Rank"].diff().abs()
consistency = cons.groupby("Name").agg(
    Avg_Rank_Movement=("Rank Change Abs", "mean"),
    Best_Rank=("Rank", "min"),
    Worst_Rank=("Rank", "max"),
).reset_index()
consistency["Avg_Rank_Movement"] = consistency["Avg_Rank_Movement"].fillna(0).round(2)
consistency = consistency.sort_values(["Avg_Rank_Movement", "Best_Rank", "Name"]).head(20)
cons_fig = px.bar(consistency.sort_values("Avg_Rank_Movement", ascending=False), x="Avg_Rank_Movement", y="Name", orientation="h", labels={"Avg_Rank_Movement": "Average rank movement per round", "Name": ""})
cons_fig.update_layout(height=max(500, 24 * len(consistency)))
st.plotly_chart(cons_fig, use_container_width=True)

st.subheader("Form Guide — Last 3 Rounds")
last_rounds = [r for r in available_rounds if r <= selected_round][-3:]
form = history[history["Round"].isin(last_rounds)].groupby("Name").agg(
    Last_3_Tips=("Round Tips", "sum"),
    Last_3_Margin=("Round Margin", "sum"),
).reset_index()
form = form.sort_values(["Last_3_Tips", "Last_3_Margin", "Name"], ascending=[False, True, True]).head(20)
form_fig = px.bar(form.sort_values("Last_3_Tips", ascending=True), x="Last_3_Tips", y="Name", orientation="h", hover_data=["Last_3_Margin"], labels={"Last_3_Tips": "Tips in last 3 rounds", "Name": ""})
form_fig.update_layout(height=max(500, 24 * len(form)))
st.plotly_chart(form_fig, use_container_width=True)

st.subheader("Team Momentum — Average Total Tips by Round")
team_round = history.groupby(["Round", "Team"], dropna=False).agg(Avg_Total_Tips=("Total Tips", "mean")).reset_index()
team_round = team_round.sort_values(["Team", "Round"])
team_momentum = px.line(team_round, x="Round", y="Avg_Total_Tips", color="Team", markers=True, labels={"Avg_Total_Tips": "Average total tips"})
team_momentum.update_xaxes(dtick=1)
team_momentum.update_layout(height=520)
st.plotly_chart(team_momentum, use_container_width=True)
