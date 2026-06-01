# simply-backend# simply-stats

Stats backend for Simply. Deploy checklist below — nothing else to configure.

---

## Deploy

### 1. Create a GitHub repo

Go to https://github.com/new, name it `simply-stats`, set it to private, click **Create repository**.

Then push:
```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/simply-stats.git
git push -u origin main
```

### 2. Create the Render service

1. Go to https://dashboard.render.com
2. Click **New → Web Service**
3. Click **Connect a repository** → select `simply-stats`
4. Render auto-detects `render.yaml` — click **Create Web Service**

That's it. Render handles the rest.

### 3. Get your URL

Once deployed, your API URL will be:
```
https://simply-stats.onrender.com
```

Paste that into `simply.html`:
```js
const STATS_API = "https://simply-stats.onrender.com";
```

### 4. Lock CORS to your frontend (optional but recommended)

In `main.py`, change:
```python
CORS_ORIGIN = "*"
```
to:
```python
CORS_ORIGIN = "https://your-frontend-url.com"
```
Then push again — Render auto-redeploys.

---

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/visit` | Track a page visit |
| POST | `/launch` | Track a game launch |
| GET | `/stats` | Get all stats |
| GET | `/docs` | Swagger UI |