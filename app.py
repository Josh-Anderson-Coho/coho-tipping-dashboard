import os
import re
import glob
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="Coho Footy Tipping Dashboard",
    layout="wide"
)

DATA_DIR = "data"


# -----------------------------
# Helpers
# -----------------------------

def extract_round_number(filename):
    match = re.search(r"nrl-(\d+)", filename.lower())
    if match:
        return int(match.group(1))
    return None


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


@st.cache_data
def load_round_files():
    pattern = os.path.join(DATA_DIR, "competition-Coho Footy Tipping-nrl-*.csv")
    files = glob.glob(pattern)

    round_frames = []

    for file in files:
        round_no = extract_round_number(os.path.basename(file))
        if round_no is None:
            continue

        df = pd.read_csv(file)
        df.columns = [str(c).strip() for c in df.columns]
        df["Round"] = round_no
        df["Source File"] = os.path.basename(file)

        round_frames.append(df)

    if not round_frames:
        return pd.DataFrame()

    return pd.concat(round_frames, ignore_index=True)


@st.cache_data
def load_teams():
    team_path = os.path.join(DATA_DIR, "Teams.csv")

    if not os.path.exists(team_path):
        return pd.DataFrame(columns=["Name", "Team"])

    teams = pd.read_csv(team_path)
    teams.columns = [str(c).strip() for c in teams.columns]

    name_col = find_column(teams.columns, ["Name", "Entrant", "Player", "Tipper"])
    team_col = find_column(teams.columns, ["Team", "Group"])

    if name_col is None or team_col is None:
        return pd.DataFrame(columns=["Name", "Team"])

    teams = teams.rename(columns={
        name_col: "Name",
        team_col: "Team"
    })

    teams["Name"] = teams["Name"].astype(str).str.strip()
    teams["Team"] = teams["Team"].astype(str).str.strip()

    return teams[["Name", "Team"]]


def standardise_data(raw):
    if raw.empty:
        return raw

    name_col = find_column(raw.columns, [
        "Name", "Entrant", "Player", "Tipper", "User", "Username"
    ])

    tips_col = find_column(raw.columns, [
        "Tips", "Correct Tips", "Correct", "Score", "Points", "Total Tips"
    ])

    margin_col = find_column(raw.columns, [
        "Margin", "Total Margin", "Margin Total"
    ])

    if name_col is None:
        st.error("Could not find a name column in the ESPN CSV files.")
        st.stop()

    if tips_col is None:
        st.error("Could not find a tips/correct/points column in the ESPN CSV files.")
        st.stop()

    if margin_col is None:
        st.error("Could not find a margin column in the ESPN CSV files.")
        st.stop()

    df = raw.rename(columns={
        name_col: "Name",
        tips_col: "Tips",
        margin_col: "Margin"
    }).copy()

    df["Name"] = df["Name"].astype(str).str.strip()
    df["Tips"] = pd.to_numeric(df["Tips"], errors="coerce").fillna(0)
    df["Margin"] = pd.to_numeric(df["Margin"], errors="coerce").fillna(999999)
    df["Round"] = pd.to_numeric(df["Round"], errors="coerce")

    df = df.dropna(subset=["Round"])
    df["Round"] = df["Round"].astype(int)

    return df


def rank_round(df):
    ranked = df.sort_values(
        ["Tips", "Margin", "Name"],
        ascending=[False, True, True]
    ).copy()

    ranked["Rank"] = range(1, len(ranked) + 1)
    return ranked


def build_rank_history(df_all):
    frames = []

    for round_no in sorted(df_all["Round"].unique()):
        round_df = df_all[df_all["Round"] == round_no].copy()
        ranked = rank_round(round_df)
        frames.append(ranked)

    return pd.concat(frames, ignore_index=True)


# -----------------------------
# Load data
# -----------------------------

raw_data = load_round_files()
teams = load_teams()

if raw_data.empty:
    st.title("Coho Footy Tipping Dashboard")
    st.warning("No round CSV files found. Add your ESPN CSV files to the data folder.")
    st.stop()

df_all = standardise_data(raw_data)

if not teams.empty:
    df_all = df_all.merge(teams, on="Name", how="left")
else:
    df_all["Team"] = "Unassigned"

df_all["Team"] = df_all["Team"].fillna("Unassigned")

rank_history = build_rank_history(df_all)

available_rounds = sorted(rank_history["Round"].unique())


# -----------------------------
# Sidebar
# -----------------------------

st.sidebar.title("Controls")

selected_round = st.sidebar.selectbox(
    "Select round",
    available_rounds,
    index=len(available_rounds) - 1
)

selected_names = st.sidebar.multiselect(
    "Highlight/filter entrants",
    sorted(rank_history["Name"].unique())
)

show_all = st.sidebar.checkbox("Show all entrants on chart", value=False)

uploaded_file = st.sidebar.file_uploader(
    "Test upload a new ESPN CSV",
    type=["csv"]
)

if uploaded_file is not None:
    st.sidebar.success(
        "Upload received for testing. To make it permanent, add the CSV to the GitHub data folder."
    )


# -----------------------------
# Current round calculations
# -----------------------------

current = rank_history[rank_history["Round"] == selected_round].copy()

previous = rank_history[rank_history["Round"] == selected_round - 1][
    ["Name", "Rank"]
].rename(columns={"Rank": "Previous Rank"})

current = current.merge(previous, on="Name", how="left")
current["Movement"] = current["Previous Rank"] - current["Rank"]

tipster = current.sort_values(
    ["Tips", "Margin", "Name"],
    ascending=[False, True, True]
).iloc[0]

mover = None
dropper = None

if selected_round > min(available_rounds) and current["Movement"].notna().any():
    mover = current.sort_values(
        ["Movement", "Margin"],
        ascending=[False, True]
    ).iloc[0]

    dropper = current.sort_values(
        ["Movement", "Margin"],
        ascending=[True, True]
    ).iloc[0]

middlest = current[current["Rank"] == 26]


# -----------------------------
# Team leaderboard
# -----------------------------

team_stats = current.groupby("Team", dropna=False).agg(
    Average_Tips=("Tips", "mean"),
    Average_Margin=("Margin", "mean"),
    Participants=("Name", "count")
).reset_index()

team_stats = team_stats.sort_values(
    ["Average_Tips", "Average_Margin", "Team"],
    ascending=[False, True, True]
)

team_stats["Team Rank"] = range(1, len(team_stats) + 1)


# -----------------------------
# Dashboard
# -----------------------------

st.title("🏉 Coho Footy Tipping Dashboard")
st.caption("Auto-generated from ESPN Footy Tips CSV exports")

a, b, c, d = st.columns(4)

a.metric(
    "Tipster of the Round",
    tipster["Name"],
    f'{int(tipster["Tips"])} tips / {int(tipster["Margin"])} margin'
)

if mover is not None:
    b.metric(
        "Mover & Shaker",
        mover["Name"],
        f'+{int(mover["Movement"])} places'
    )
else:
    b.metric("Mover & Shaker", "N/A")

if dropper is not None:
    c.metric(
        "Shooting Star",
        dropper["Name"],
        f'{int(dropper["Movement"])} places'
    )
else:
    c.metric("Shooting Star", "N/A")

if not middlest.empty:
    d.metric("Middlest Watch", middlest.iloc[0]["Name"], "26th place")
else:
    d.metric("Middlest Watch", "N/A")


st.divider()

# -----------------------------
# Charts
# -----------------------------

st.subheader("Rank Tracking")

if show_all:
    chart_data = rank_history.copy()
elif selected_names:
    chart_data = rank_history[rank_history["Name"].isin(selected_names)].copy()
else:
    top_10_names = current.head(10)["Name"].tolist()
    chart_data = rank_history[rank_history["Name"].isin(top_10_names)].copy()
    st.info("Showing current top 10 by default. Select names in the sidebar or tick Show all entrants.")

fig = px.line(
    chart_data,
    x="Round",
    y="Rank",
    color="Name",
    markers=True,
    hover_data=["Tips", "Margin", "Team"]
)

fig.update_yaxes(autorange="reversed", title="Rank")
fig.update_xaxes(dtick=1)
fig.update_layout(height=600)

st.plotly_chart(fig, use_container_width=True)


# -----------------------------
# Tables
# -----------------------------

left, right = st.columns(2)

with left:
    st.subheader(f"Round {selected_round} Leaderboard")
    display_current = current.sort_values("Rank")[
        ["Rank", "Name", "Tips", "Margin", "Movement", "Team"]
    ]

    st.dataframe(display_current, use_container_width=True, hide_index=True)

with right:
    st.subheader(f"Round {selected_round} Team Leaderboard")
    display_teams = team_stats[
        ["Team Rank", "Team", "Average_Tips", "Average_Margin", "Participants"]
    ].copy()

    display_teams["Average_Tips"] = display_teams["Average_Tips"].round(2)
    display_teams["Average_Margin"] = display_teams["Average_Margin"].round(2)

    st.dataframe(display_teams, use_container_width=True, hide_index=True)


st.divider()

st.subheader("Weekly Email Summary")

summary_lines = [
    f"Round {selected_round} wrap-up:",
    f"🏆 Tipster of the Round: {tipster['Name']} with {int(tipster['Tips'])} tips and a margin of {int(tipster['Margin'])}.",
]

if mover is not None:
    summary_lines.append(
        f"🚀 Mover & Shaker: {mover['Name']} climbed {int(mover['Movement'])} places."
    )

if dropper is not None:
    summary_lines.append(
        f"💫 Shooting Star: {dropper['Name']} dropped {abs(int(dropper['Movement']))} places."
    )

if not middlest.empty:
    summary_lines.append(
        f"🎯 Middlest Watch: {middlest.iloc[0]['Name']} is currently sitting in 26th place."
    )

if not team_stats.empty:
    top_team = team_stats.iloc[0]
    summary_lines.append(
        f"🏉 Top Team: {top_team['Team']} leads the teams with an average of {top_team['Average_Tips']:.2f} tips."
    )

st.text_area(
    "Copy/paste summary",
    "\n".join(summary_lines),
    height=180
)
