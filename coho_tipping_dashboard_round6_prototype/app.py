
import re
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Coho Tipping Dashboard", layout="wide")

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_CSV = DATA_DIR / "competition-Coho Footy Tipping-nrl-6.csv"
TEAM_MAP = DATA_DIR / "team_mapping.csv"

st.title("Coho Footy Tipping Dashboard")
st.caption("Round-by-round ladder, weekly movement, team rankings and wrap-up callouts.")

@st.cache_data
def load_default_csv():
    return pd.read_csv(DEFAULT_CSV)

def normalise_columns(df):
    df = df.copy()
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df

def round_columns(df):
    rounds = []
    for c in df.columns:
        m = re.fullmatch(r"ROUND\s+(\d+)", c)
        if m:
            rounds.append((int(m.group(1)), c))
    return sorted(rounds)

def build_ladder(df):
    df = normalise_columns(df)
    rounds = round_columns(df)
    latest = rounds[-1][0]
    latest_col = f"ROUND {latest}"
    latest_margin_col = f"ROUND {latest} MARGIN"
    for c in ["RANK", "TOTAL SCORE", "TOTAL MARGIN", latest_col]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if latest_margin_col in df.columns:
        df[latest_margin_col] = pd.to_numeric(df[latest_margin_col], errors="coerce")
    else:
        df[latest_margin_col] = None

    ladder = df[["NAME", "RANK", "TOTAL SCORE", "TOTAL MARGIN", latest_col, latest_margin_col]].copy()
    ladder = ladder.rename(columns={
        "NAME": "Name",
        "RANK": "Rank",
        "TOTAL SCORE": "Total Score",
        "TOTAL MARGIN": "Total Margin",
        latest_col: "Round Score",
        latest_margin_col: "Round Margin",
    })

    prev_round_cols = [c for n, c in rounds if n < latest]
    if prev_round_cols:
        prev = df[["NAME"] + prev_round_cols].copy()
        for c in prev_round_cols:
            prev[c] = pd.to_numeric(prev[c], errors="coerce").fillna(0)
        prev["Prev Total"] = prev[prev_round_cols].sum(axis=1)
        prev = prev.sort_values(["Prev Total", "NAME"], ascending=[False, True]).reset_index(drop=True)
        prev["Previous Rank"] = prev.index + 1
        ladder = ladder.merge(prev[["NAME", "Previous Rank"]].rename(columns={"NAME": "Name"}), on="Name", how="left")
        ladder["Movement"] = ladder["Previous Rank"] - ladder["Rank"]
    else:
        ladder["Previous Rank"] = None
        ladder["Movement"] = 0

    ladder = ladder.sort_values("Rank").reset_index(drop=True)
    return ladder, latest

def build_rank_history(df):
    df = normalise_columns(df)
    rounds = round_columns(df)
    history = []
    cumulative = pd.Series([0] * len(df), index=df.index, dtype="float")
    for rnd, col in rounds:
        scores = pd.to_numeric(df[col], errors="coerce").fillna(0)
        cumulative = cumulative + scores
        temp = pd.DataFrame({"Name": df["NAME"], "Round": rnd, "Cumulative Score": cumulative})
        temp = temp.sort_values(["Cumulative Score", "Name"], ascending=[False, True]).reset_index(drop=True)
        temp["Rank"] = temp.index + 1
        history.append(temp)
    return pd.concat(history, ignore_index=True) if history else pd.DataFrame()

def load_team_map():
    if TEAM_MAP.exists():
        tm = pd.read_csv(TEAM_MAP)
        tm.columns = [str(c).strip().upper() for c in tm.columns]
        if "NAME" in tm.columns and "TEAM" in tm.columns:
            return tm.rename(columns={"NAME":"Name", "TEAM":"Team"})[["Name", "Team"]]
    return pd.DataFrame(columns=["Name", "Team"])

def get_callouts(ladder):
    tipster = ladder.sort_values(["Round Score", "Round Margin", "Rank"], ascending=[False, True, True]).iloc[0]
    mover = ladder.sort_values(["Movement", "Rank"], ascending=[False, True]).iloc[0]
    shooting = ladder.sort_values(["Movement", "Rank"], ascending=[True, True]).iloc[0]
    middle = ladder[ladder["Rank"] == 26]
    middle = middle.iloc[0] if not middle.empty else ladder.iloc[len(ladder)//2]
    return tipster, mover, shooting, middle

uploaded = st.sidebar.file_uploader("Upload latest ESPN CSV", type=["csv"])
if uploaded:
    raw = pd.read_csv(uploaded)
    st.sidebar.success("Using uploaded ESPN file")
else:
    raw = load_default_csv()
    st.sidebar.info("Using bundled Round 6 demo file")

ladder, latest = build_ladder(raw)
history = build_rank_history(raw)
team_map = load_team_map()
ladder_team = ladder.merge(team_map, on="Name", how="left")
ladder_team["Team"] = ladder_team["Team"].fillna("Unassigned")

tipster, mover, shooting, middle = get_callouts(ladder)

c1, c2, c3, c4 = st.columns(4)
round_margin_text = int(tipster["Round Margin"]) if pd.notna(tipster["Round Margin"]) else "n/a"
c1.metric("Tipster of the Round", tipster["Name"], f"{int(tipster['Round Score'])} tips, margin {round_margin_text}")
c2.metric("Mover & Shaker", mover["Name"], f"+{int(mover['Movement'])} places" if mover["Movement"] > 0 else f"{int(mover['Movement'])} places")
c3.metric("Shooting Star", shooting["Name"], f"{int(shooting['Movement'])} places")
c4.metric("Middlest Watch", middle["Name"], "26th place" if int(middle["Rank"]) == 26 else f"Rank {int(middle['Rank'])}")

st.subheader(f"Round {latest} Ladder")
show_cols = ["Rank", "Name", "Total Score", "Total Margin", "Round Score", "Round Margin", "Previous Rank", "Movement", "Team"]
st.dataframe(ladder_team[show_cols], use_container_width=True, hide_index=True)

st.subheader("Rank Tracker")
names = sorted(ladder["Name"].unique())
default_names = ladder.head(10)["Name"].tolist()
selected = st.multiselect("Choose entrants to display", names, default=default_names)
if selected:
    fig = px.line(history[history["Name"].isin(selected)], x="Round", y="Rank", color="Name", markers=True)
    fig.update_yaxes(autorange="reversed", title="Rank")
    fig.update_layout(height=550, legend_title_text="Entrant")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Select at least one entrant to show the rank tracker.")

st.subheader("Team Standings")
team_standings = ladder_team.groupby("Team", as_index=False).agg(
    Average_Rank=("Rank", "mean"),
    Total_Score=("Total Score", "sum"),
    Entrants=("Name", "count"),
)
team_standings = team_standings.sort_values(["Average_Rank", "Total_Score"], ascending=[True, False])
team_standings["Team Rank"] = range(1, len(team_standings) + 1)
st.dataframe(team_standings[["Team Rank", "Team", "Average_Rank", "Total_Score", "Entrants"]], use_container_width=True, hide_index=True)

if not team_standings.empty:
    fig_team = px.bar(team_standings, x="Team", y="Average_Rank", text="Average_Rank", title="Team Average Rank")
    fig_team.update_yaxes(autorange="reversed")
    st.plotly_chart(fig_team, use_container_width=True)

st.subheader("Email Wrap Helper")
shooting_places = abs(int(shooting["Movement"]))
wrap = f"""Round {latest} wrap-up:

🏆 Tipster of the Round: {tipster['Name']} with {int(tipster['Round Score'])} correct tips and a margin of {round_margin_text}.
🚀 Mover & Shaker: {mover['Name']} moved {int(mover['Movement'])} places.
💥 Shooting Star: {shooting['Name']} dropped {shooting_places} places.
🎯 Middlest Watch: {middle['Name']} is sitting in 26th place.
"""
st.text_area("Copy/paste starter text", wrap, height=180)

st.caption("Note: previous-round movement is estimated from cumulative round scores where historical margin data is not supplied by ESPN.")
