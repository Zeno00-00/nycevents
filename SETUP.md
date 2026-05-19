# NYC Events — Setup

Step-by-step to get this running on GitHub for free, with a daily sync and morning email.

## 1. Create the GitHub repository

1. On github.com, click the **+** (top right) → **New repository**.
2. Name: `nycevents` (or whatever you want).
3. Set to **Private** if you don't want it public.
4. **Do not** initialize with a README — we already have files.
5. Click **Create repository**.

On the next page, GitHub shows commands to push existing code. From this folder (`nycevents-web`), in Terminal:

```sh
cd /Users/bben/Documents/Claude/nycevents-web
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin git@github.com:YOUR_USERNAME/nycevents.git
git push -u origin main
```

(Replace `YOUR_USERNAME` with your GitHub username.)

## 2. Enable GitHub Pages

1. In the repo on github.com, go to **Settings → Pages** (left sidebar).
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Save. Within ~1 minute, the `Deploy to GitHub Pages` workflow will run and publish the site.
4. The URL appears at the top of the Pages page — something like `https://YOUR_USERNAME.github.io/nycevents/`. Open it on your iPhone.

## 3. Set up the daily email (optional, recommended)

You'll need a Gmail "App Password" — separate from your normal Gmail login.

1. Go to https://myaccount.google.com/apppasswords (you must have 2-Step Verification enabled).
2. Generate a new app password named `nycevents`. Copy the 16-character password (no spaces).
3. In the repo on github.com, go to **Settings → Secrets and variables → Actions**.
4. Click **New repository secret** five times, adding each of:

| Name        | Value                                       |
|-------------|---------------------------------------------|
| `SMTP_HOST` | `smtp.gmail.com`                            |
| `SMTP_PORT` | `587`                                       |
| `SMTP_USER` | Your full Gmail address                     |
| `SMTP_PASS` | The 16-char app password from step 2        |
| `EMAIL_TO`  | The email where you want digests delivered  |

## 4. Trigger the first sync

The sync runs automatically every day at 5 AM ET. To test it now:

1. Go to the repo → **Actions** tab.
2. Click **Daily Sync** (left sidebar).
3. Click **Run workflow** (right side) → **Run workflow** (green button).
4. Wait ~30 seconds. You'll see it commit fresh data, and an email arrives if SMTP is configured.

## 5. Install to your iPhone

1. Open the Pages URL in Safari on your iPhone.
2. Tap the **Share** button (square with up-arrow).
3. **Add to Home Screen** → **Add**.

The icon now opens the app full-screen, no browser bars.

---

## What runs automatically

| When         | What happens                                                  |
|--------------|---------------------------------------------------------------|
| 5 AM ET daily| Sync script pulls from sources, writes `events.json`, emails digest |
| On push to `main` (web/) | Pages re-deploys within ~30s |

## What to edit by hand

| To do this | Edit this file |
|------------|----------------|
| Add a known annual event (parade, festival) | `scripts/tentpole_annuals.json` |
| Tighten the public-event filter | `scripts/sync.py` (search `INCLUDE_NAME_PATTERNS`) |
| Add a new data source | `scripts/sync.py` (add `fetch_xxx()` function + register in `ADAPTERS`) |
| Change neighborhood polygons | `web/data/neighborhoods.json` |
| Change interest quiz options | `web/data/interests-schema.json` |
| Change colors / layout | `web/styles.css` |

## Costs

- GitHub Pages hosting: $0
- GitHub Actions runtime (~3 minutes/day = ~90 minutes/month): $0 (free tier is 2,000 min/mo)
- Gmail SMTP (~30 emails/day max via app password): $0
- **Total: $0/month**

(If you want a custom domain like `nycevents.bben.com`, registration is ~$12/year via any registrar; point a CNAME at `YOUR_USERNAME.github.io`.)

## Troubleshooting

**"Daily Sync" workflow fails red in Actions** — open the failed run, click the failed step, read the error. Most common: a source site changed format. Adapters are independent, so other sources keep working. Fix the broken adapter in `scripts/sync.py` and commit.

**No email arrives** — verify the 5 SMTP secrets are set exactly as in step 3. Check the `Run sync` step's log; the script prints `email send failed:` if SMTP fails.

**Site loads but is empty** — the first sync hasn't committed `events.json` yet. Trigger a manual run from the Actions tab.
