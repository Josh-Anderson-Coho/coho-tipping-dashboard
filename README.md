# Coho Tipping Dashboard

Streamlit prototype built from the Round 6 ESPN Footy Tips CSV.

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Weekly use
Open the dashboard and upload the latest ESPN CSV in the sidebar. The app recalculates the ladder, weekly highlights, rank movement and team rankings.

## Teams
Edit `data/team_mapping.csv` to assign real teams. Names must match the ESPN `NAME` column.
