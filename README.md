# Coho Footy Tipping Dashboard

## Files
Upload this whole folder to GitHub. Keep ESPN files in `/data` using filenames like:

`competition-Coho Footy Tipping-nrl-9.csv`

The app detects the round number from `nrl-9` and will work for any future NRL round/finals file as long as the filename contains that number.

## GitHub upload from dashboard
The sidebar uploader is password protected with:

`C@H@`

To let the dashboard commit uploaded CSVs back to GitHub, add these Streamlit secrets:

```toml
[github]
token = "YOUR_GITHUB_FINE_GRAINED_TOKEN_WITH_CONTENTS_READ_WRITE"
repo = "Josh-Anderson-Coho/coho-tipping-dashboard"
branch = "main"
data_path = "data"
```

Create the token in GitHub with access to this repository and Contents: Read and write.

After a successful upload, Streamlit Cloud should redeploy from the GitHub commit.
