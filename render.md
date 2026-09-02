# Render.com Deployment Guide

## Step 1 — Sign up
Go to **https://render.com** and sign up with your GitHub account (`Dkpiec`). No credit card required for the free tier.

## Step 2 — Create the Web Service
1. Click **New +** → **Web Service**
2. Click **Connect GitHub** and authorize
3. Find and select **`Dkpiec/AI-Hedgefund-bot`**
4. Click **Connect**

## Step 3 — Configure
| Field            | Value                                  |
|------------------|----------------------------------------|
| Name             | `ai-hedgefund-api`                     |
| Region           | Oregon (or nearest)                    |
| Branch           | `main`                                 |
| Root Directory   | `src`                                  |
| Runtime          | `Python 3`                             |
| Build Command    | `pip install -r ../requirements.txt`   |
| Start Command    | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Plan             | **Free**                               |

> **Important:** Root Directory must be `src` so Render finds `main.py`.

## Step 4 — Environment Variables
Click **Environment** and add each as a secret:

| Key                  | Value                                 |
|----------------------|---------------------------------------|
| `OPENROUTER_API_KEY` | your OpenRouter key                   |
| `OPENROUTER_MODEL`   | `openrouter/free`                     |
| `MT5_ACCOUNT`        | your MT5 demo number                  |
| `MT5_PASSWORD`       | your MT5 password                     |
| `MT5_SERVER`         | `MetaQuotes-Demo`                     |
| `PAPER_MODE`         | `true` (keep true until tested live)  |

> **Note:** MT5 won't work on Render (no Windows). Leave MT5 creds blank or set `PAPER_MODE=true` so the bot still runs in paper mode for the AI brain and trade logging.

## Step 5 — Deploy
Click **Create Web Service**. First deploy takes ~3 minutes.

Once live, your URL is something like:
```
https://ai-hedgefund-api.onrender.com
```

Test it:
```bash
curl https://ai-hedgefund-api.onrender.com/api/status
```

## Step 6 — Connect Streamlit Cloud
1. Go to **https://share.streamlit.io** → New app
2. Repo: `Dkpiec/AI-Hedgefund-bot`
3. Branch: `main`
4. Main file: `src/dashboard/streamlit_app.py`
5. **Advanced settings → Secrets:**
   ```toml
   API_BASE = "https://ai-hedgefund-api.onrender.com"
   ```
6. Deploy

The Streamlit app will read the FastAPI backend's `/api/status` from the public URL.

## Free Tier Notes
- Render free services **spin down after 15 min of inactivity** — first request after that takes ~30s
- Streamlit Community Cloud is always-on for free public apps
- For production, upgrade to Render's $7/mo plan (no spin-down)
