# Deploying the DPDP Act Assistant: Vercel + Render + Groq + Neo4j AuraDB

Four free services, no local install needed for deployment itself. Read the
whole "Before you start" section first - there's one real uncertainty
(Render) worth knowing about before you invest time in the rest.

## Before you start

**Groq** (LLM) and **Neo4j AuraDB** (graph database) are confirmed free with
no card required, based on multiple independent, recent sources.

**Render** (backend hosting) is the one uncertain piece. Its own marketing
says free web services need no card, and most walkthroughs confirm that -
but there are also recent, credible user reports of hitting a card wall
specifically when creating a web service (not just any service). Since you
have no card at all, **step 3 below has an explicit checkpoint**: create the
account and get to the point of clicking "Create Web Service," and if it
asks for payment info before the service exists, STOP - don't enter
anything, and fall back to the GitHub Codespaces version we already have
working (message me and I'll help you switch back).

**Vercel** (frontend hosting) is well-established as free for static sites,
no card needed.

---

## 1. Push this code to GitHub

Create a new repository and push everything in this folder (`backend/` and
`frontend/` as subfolders, plus this file) to it. One repo is fine - both
Render and Vercel let you point at a specific subfolder.

## 2. Get a free Groq API key

1. Go to [console.groq.com](https://console.groq.com) and sign up (email or
   Google/GitHub login - no card).
2. Go to **API Keys** in the sidebar → **Create API Key**.
3. Copy the key somewhere safe - it's shown only once.

## 3. Create a free Neo4j AuraDB instance

1. Go to [console.neo4j.io](https://console.neo4j.io) and sign up (no card).
2. Click **New Instance** → choose **AuraDB Free**.
3. Give it a name (e.g. `dpdp-act`) and create it.
4. **Important**: Aura shows you the connection URI, username, and password
   exactly once, right after creation. Download or copy all three
   immediately - if you lose the password, you'll need to reset it (the
   instance itself isn't lost, just the credentials).
5. The URI looks like `neo4j+s://xxxxxxxx.databases.neo4j.io` - keep the
   `neo4j+s://` prefix, that's the encrypted connection scheme Aura requires.

## 4. Deploy the backend to Render — ⚠️ card-wall checkpoint here

1. Go to [render.com](https://render.com) and sign up with GitHub (no card
   needed to sign up itself).
2. Click **New +** → **Web Service**.
3. Connect your GitHub repo. Set:
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free
4. **Before clicking the final "Create Web Service" button**: check if
   Render has asked you for payment details anywhere in this flow.
   - **If it has NOT asked for a card**: continue to step 5.
   - **If it DOES ask for a card**: stop here, don't enter anything, and
     come back to me - we'll deploy this backend somewhere else instead
     (or fall back to the Codespaces version, which is fully working right
     now with no card anywhere).
5. In the **Environment** tab, add these (all as plain env vars, no
   quotes):
   | Key | Value |
   |---|---|
   | `GROQ_API_KEY` | the key from step 2 |
   | `NEO4J_URI` | the URI from step 3 |
   | `NEO4J_USER` | `neo4j` |
   | `NEO4J_PASSWORD` | the password from step 3 |
   | `ALLOWED_ORIGIN` | `*` (tighten this in step 7, after you have your Vercel URL) |
6. Save, deploy, and wait (first deploy can take a few minutes). Once live,
   copy the URL Render gives you - looks like
   `https://dpdp-act-backend.onrender.com`.
7. Test it directly: open `https://YOUR-RENDER-URL.onrender.com/health` in
   a browser. You should see JSON like `{"status": "ok", "ingested": true, ...}`.
   The first request after any period of inactivity takes 30-60 seconds
   (free tier cold start) - if it looks stuck, give it a minute and refresh.

## 5. Point the frontend at your backend

1. Open `frontend/app.js` and find this line near the top:
   ```js
   const API = "REPLACE_WITH_YOUR_RENDER_URL";
   ```
2. Replace it with your actual Render URL, no trailing slash:
   ```js
   const API = "https://dpdp-act-backend.onrender.com";
   ```
3. Commit and push this change.

## 6. Deploy the frontend to Vercel

1. Go to [vercel.com](https://vercel.com) and sign up with GitHub (no card).
2. **Add New** → **Project** → import your repo.
3. Set **Root Directory** to `frontend`.
4. Framework preset: choose "Other" (it's plain HTML/CSS/JS, no build step
   needed).
5. Deploy. Vercel gives you a URL like `https://your-project.vercel.app`.

## 7. (Optional but recommended) Lock down CORS

Right now the backend accepts requests from any origin (`ALLOWED_ORIGIN=*`),
which is fine for a demo but not something to leave open indefinitely. Once
you have your Vercel URL:

1. Go back to Render → your service → **Environment**.
2. Change `ALLOWED_ORIGIN` to your exact Vercel URL, e.g.
   `https://your-project.vercel.app`.
3. Save - Render will redeploy automatically.

## 8. Test it end to end

Open your Vercel URL and ask:
- A real DPDP Act question (e.g. "What is the right to correction and
  erasure?") - should answer with citations.
- "What is GDPR?" - should refuse cleanly, not hallucinate.
- A cross-border transfer question - should cite Section 16.

---

## Known limitations of this stack (read before you rely on it)

- **Render free tier sleeps after 15 minutes of no traffic.** The next
  request wakes it up but takes 30-60 seconds. If your sir opens the link
  after it's been idle, the first load will feel slow - that's expected,
  not broken.
- **Every cold start re-ingests the Act from scratch** (fetches the PDF,
  re-parses, re-tags). This is fast (a few seconds, no LLM calls involved)
  but it also means **any section you manually approved in the human
  review queue during a previous session is forgotten** on the next cold
  start - Render wipes the in-memory process state on sleep/wake, only the
  Neo4j graph data persists (Aura is a separate, always-on service). You'll
  need to re-approve sensitive/low-confidence sections again after any
  period of inactivity. This is a real trade-off of free, ephemeral
  compute - a paid "always-on" instance wouldn't have this issue.
- **TF-IDF retrieval is lexical, not semantic** (see the comments in
  `dpdp_config.py` and `tfidf_search.py`). It matches shared keywords, not
  meaning - it correctly refuses out-of-scope questions and correctly
  ranks direct questions, but a heavily paraphrased in-scope question that
  shares little vocabulary with the actual section text may score lower
  than you'd expect from an embedding-based system. If you see this happen
  in practice, the two thresholds in `dpdp_config.py`
  (`SIMILARITY_THRESHOLD`, `HARD_CUTOFF`) are the place to retune.
- **Groq's free tier**: 30 requests/minute, 14,400/day - more than enough
  for demos, but if you're doing rapid-fire testing you can hit the
  per-minute limit; the error will surface clearly in the chat rather than
  hanging.
- **AuraDB Free instances pause after extended inactivity** (multi-day, not
  the same 15-minute Render sleep) - if the graph connection ever shows as
  disconnected in `/health` after a long gap, log into
  console.neo4j.io and resume the instance manually.
