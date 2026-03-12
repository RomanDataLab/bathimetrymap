# Deploy to Vercel

## Prerequisites

1. **Node.js** (v18+) — required for Vercel CLI
2. **Vercel account** — sign up free at https://vercel.com/signup
3. **Vercel CLI** — install globally:

```bash
npm install -g vercel
```

## Project structure

```
deploy/
  vercel.json          # Vercel configuration
  public/
    index.html         # Self-contained map (all data embedded, ~525 KB)
```

The map is a single self-contained HTML file with all GeoJSON data
embedded inline. No build step, no API calls, no external data files.

## Step-by-step deployment

### 1. Build the map (if not already built)

From the project root:

```bash
pip install -r requirements.txt

python fetch_bathymetry.py
python generate_isolines.py --min-depth -1300
python fetch_vessels.py
python create_map.py
```

### 2. Copy to deploy folder

Windows:
```cmd
copy /Y web\index.html deploy\public\index.html
```

Linux/Mac:
```bash
cp web/index.html deploy/public/index.html
```

Or use the provided script:
```bash
# Windows
deploy.bat

# Linux/Mac
bash deploy.sh
```

### 3. Login to Vercel

```bash
vercel login
```

Follow the prompts to authenticate (email, GitHub, GitLab, or Bitbucket).

### 4. Deploy

```bash
cd deploy
vercel
```

Vercel will ask a few questions on first deploy:

| Prompt | Answer |
|--------|--------|
| Set up and deploy? | **Y** |
| Which scope? | Select your account |
| Link to existing project? | **N** |
| Project name? | `gibraltar-maritime-map` (or your choice) |
| Directory with code? | `.` |
| Override settings? | **N** |

This creates a **preview deployment**. You'll get a URL like:
```
https://gibraltar-maritime-map-xxxx.vercel.app
```

### 5. Deploy to production

```bash
vercel --prod
```

This publishes to your production URL:
```
https://gibraltar-maritime-map.vercel.app
```

## Redeployment

After updating the map data, repeat steps 2 and 5:

```bash
# From project root
copy /Y web\index.html deploy\public\index.html
cd deploy
vercel --prod
```

## Alternative: Deploy via GitHub

1. Push the `deploy/` folder to a GitHub repo
2. Go to https://vercel.com/new
3. Import the repo
4. Set **Root Directory** to `deploy`
5. Set **Framework Preset** to `Other`
6. Click Deploy

Any push to `main` will auto-deploy.

## Alternative: One-command deploy (no deploy folder)

If you prefer deploying `web/` directly without the `deploy/` folder:

```bash
cd web
vercel --prod
```

This works but won't include the `vercel.json` caching headers.

## Custom domain

After deploying, add a custom domain in the Vercel dashboard:

1. Go to your project Settings > Domains
2. Add your domain (e.g., `map.example.com`)
3. Update DNS as instructed by Vercel

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `vercel: command not found` | Run `npm install -g vercel` |
| Login fails | Try `vercel login --github` |
| 404 after deploy | Verify `public/index.html` exists in the deploy folder |
| Map tiles don't load | External tile servers (Esri, OSM) must be reachable — not a Vercel issue |
| Large file warning | The HTML (~525 KB) is well within Vercel's limits |
