# Deployment and Hosting

Copy-paste steps for each hosting option. These steps assume Phases 0–7 of [BUILD_PLAN.md](BUILD_PLAN.md) are done, so the `Makefile`, `deploy/` and `.github/workflows/` files exist.

> **Golden rule (public repo):** secrets go in `.env` (gitignored) or the host's secret settings, never in a committed file. Before every push, run `git status` and check that `.env` isn't listed. CI runs gitleaks as a backstop.

## Options at a glance

| Option | Cost | Always on? | Who runs the daily refresh | Effort | Best for |
|--------|------|-----------|----------------------------|--------|----------|
| **A. Homelab (Docker Compose)** | $0 (your hardware) | Yes | Refresher container (cron) | Low | Primary, if you have a box that's always on |
| **B. GitHub Actions + Streamlit Community Cloud** | $0 | App sleeps when idle and wakes on visit (~30 s) | GitHub Actions cron | Lowest | Free public demo, zero servers |
| **C. GitHub Actions + Hugging Face Spaces (Docker)** | $0 | Sleeps after a period of inactivity | GitHub Actions cron | Low | Free alternative to B; runs the same Docker image |
| **D. Oracle Cloud "Always Free" VM** | $0 (card needed at signup) | Yes | Refresher container (cron) | Medium | Homelab-style hosting without your own hardware |
| **A+B hybrid** | $0 | Yes | GitHub Actions only | Low | Homelab serves the app; Actions does the refresh |

**Recommendation:** start with **B** (fastest to a working public URL, nothing to maintain). Move to or add **A** when you want it always on and private. Free-tier terms change, so check each provider's current limits when you sign up.

---

## Common setup (do once, on any machine)

```bash
git clone https://github.com/kevinofsydney/fight-analyser.git
cd fight-analyser
cp .env.example .env          # then edit .env; it is gitignored
make setup                    # installs uv deps
make check                    # lint + typecheck + tests should pass
```

First data load (local, about 5–10 min: downloads the ~150 MB fight-canon-db release and imports the UFC part):

```bash
make backfill                 # full history from fight-canon-db
# or from the fallback source:
make backfill ARGS="--source greco"
make refresh                  # adds upcoming cards from ESPN
make app                      # http://localhost:8501
```

---

## A. Homelab (Docker Compose)

**Needs:** any Linux box / NAS / Proxmox LXC/VM with Docker and the Compose plugin, about 1 GB RAM and 1 GB disk.

1. **Install Docker** (skip if already installed):
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
2. **Clone and configure:**
   ```bash
   git clone https://github.com/kevinofsydney/fight-analyser.git
   cd fight-analyser
   cp .env.example .env
   nano .env            # set REFRESH_CRON, optional DISCORD_WEBHOOK_URL, APP_PASSWORD
   ```
3. **Build and start:**
   ```bash
   make docker-up       # = docker compose -f deploy/docker-compose.yml up -d --build
   ```
4. **First data load** (one time; runs inside the refresher container):
   ```bash
   docker compose -f deploy/docker-compose.yml exec refresher fightdb backfill
   ```
   *Alternative:* if the GitHub Actions data release (option B) already exists, set `DATA_RELEASE_URL` in `.env` and the app downloads the finished DB instead.
5. **Open:** `http://<homelab-ip>:8501`
6. **Check the daily refresh:**
   ```bash
   docker compose -f deploy/docker-compose.yml logs -f refresher
   ```
   The Status page in the app shows each run.
7. **Update to the latest code:**
   ```bash
   git pull && make docker-up
   ```
8. **Back up the data** (optional; the DB can always be rebuilt with a backfill):
   ```bash
   docker run --rm -v fightdb-data:/data -v "$PWD":/backup alpine \
     tar czf /backup/fightdb-$(date +%F).tgz -C /data .
   ```

### Exposing the homelab instance outside your network (pick one)

| Method | Public? | Secret needed | Notes |
|--------|---------|---------------|-------|
| **Tailscale** | No, only your tailnet devices | None in repo | Simplest private access. Install Tailscale on the host and open `http://<tailscale-name>:8501`. |
| **Cloudflare Tunnel** | Yes, on your domain | `CLOUDFLARE_TUNNEL_TOKEN` in `.env` | No open ports. Add the `cloudflared` service via the compose profile `tunnel`. Put **Cloudflare Access** (free tier) in front to require a login. |
| Reverse proxy (Caddy / Traefik / NPM) | Yes | Depends | Use this if you already run one. Point it at `app:8501` and enable websockets (Streamlit needs them). |

### A+B hybrid (recommended if you use both)

Let GitHub Actions do the refresh (option B, steps B1–B3). On the homelab, set `DATA_RELEASE_URL` in `.env` and run only the `app` service:
```bash
docker compose -f deploy/docker-compose.yml up -d app
```
Your homelab never downloads upstream sources itself. It just pulls the finished DB from the release.

---

## B. Free: GitHub Actions (refresh) + Streamlit Community Cloud (app)

How it works: a daily GitHub Actions job downloads the upstream datasets and ESPN cards, builds `fightdb.duckdb`, and uploads it to a GitHub Release tagged `data-latest`. The Streamlit app downloads that file on startup and whenever its copy is older than `DB_MAX_AGE_HOURS`.

**B1. Allow Actions to publish releases**
GitHub repo → **Settings → Actions → General → Workflow permissions** → select **Read and write permissions** → Save.

**B2. (Optional) add secrets**
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `DISCORD_WEBHOOK_URL` (only if you want card-change notifications)

**B3. Run the first refresh manually**
Repo → **Actions → "Daily refresh" → Run workflow**. The first run does a full backfill (around 10 minutes). When it finishes, **Releases** should show `data-latest` with `fightdb.duckdb` attached.
After this, it runs every day on the cron in `.github/workflows/refresh.yml`.

**B4. Deploy the app**
1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app** → pick the repo `kevinofsydney/fight-analyser`, branch `main`, main file `src/fightdb/app/Home.py`.
3. **Advanced settings → Python version:** 3.12.
4. **Advanced settings → Secrets** (TOML format; this is stored by Streamlit, not in git):
   ```toml
   DATA_RELEASE_URL = "https://github.com/kevinofsydney/fight-analyser/releases/download/data-latest/fightdb.duckdb"
   # APP_PASSWORD = "optional-shared-password"
   ```
5. **Deploy.** You'll get a `*.streamlit.app` URL.

> Streamlit Community Cloud installs from `requirements.txt` or `pyproject.toml`. If it doesn't pick up the uv project correctly, T7.2's agent should add a generated `requirements.txt` (`uv export --no-dev --no-hashes > requirements.txt`) and a CI check that it's in sync.

**B5. Caveats**
- The app sleeps after a period with no visitors. The first visit wakes it (about 30 s).
- GitHub disables scheduled workflows in public repos after **60 days with no repo activity**. Build-plan task T9.3 adds a keepalive. You can also just push something occasionally.
- GitHub cron start times can drift by several minutes under load. That's fine for a daily job.
- The data release is **public**, because the repo is public. See DECISIONS.md D15.

---

## C. Free alternative: Hugging Face Spaces (Docker)

Uses the same Docker image as the homelab. The refresh still runs in GitHub Actions (B1–B3).

1. https://huggingface.co → **New Space** → SDK **Docker** → hardware **CPU basic (free)** → visibility of your choice.
2. In the Space's **Settings → Variables and secrets**, add `DATA_RELEASE_URL` (and `APP_PASSWORD` if wanted).
3. Push the app to the Space. Either:
   - add the Space as a second git remote and push `main` (the Space reads `deploy/huggingface/README.md` metadata; T8.3 sets this up), or
   - add a GitHub Action that syncs to the Space on push to `main` (needs an `HF_TOKEN` **GitHub secret**, never committed).
4. The Space must listen on port **7860** (set `STREAMLIT_SERVER_PORT=7860` in the Space variables) or declare `app_port: 8501` in its README metadata.

Caveat: free Spaces go to sleep after a stretch of inactivity and wake on visit.

---

## D. Oracle Cloud Always Free VM (homelab in the cloud)

1. Sign up at https://www.oracle.com/cloud/free/ (needs a card for verification; Always Free resources aren't charged).
2. Create a VM: **Ampere A1 (ARM)** shape, Ubuntu 24.04, 1–2 OCPU and 6–12 GB RAM is plenty. Add your SSH public key.
3. In the VM's subnet security list, open nothing extra if you use Tailscale or Cloudflare Tunnel (recommended). Otherwise open TCP 8501.
4. SSH in and follow **option A** exactly. The Docker image must support `linux/arm64` (python:3.12-slim does; T7.3 should verify with `docker buildx`).

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "Data is stale" banner | Status page → last run error. Homelab: `docker compose logs refresher`. Free path: Actions tab → latest "Daily refresh" run. |
| Refresh fails with validation errors | An upstream changed format. Check the `scrape_runs` error to see which source. Run `make fixtures`, then `make test`, and fix that adapter. History sources fail over automatically. ESPN failures only affect upcoming cards. |
| Upcoming fighter shows "no UFC history" but isn't a debutant | Identity match failed. Add a line to `data/overrides/fighter_identity.yaml` (ESPN ID → fighter ID) and commit it. |
| App can't find DB | Homelab: is the `fightdb-data` volume mounted at `/data`, with `DB_PATH=/data/fightdb.duckdb`? Free: is `DATA_RELEASE_URL` set, and does the release asset exist? |
| DuckDB "database is locked" | Only the refresher should open the DB read-write. The app must use `read_only=True`. Refresh writes to a temp file and renames it over the live one. |
| Accidentally committed a secret | **Rotate the secret immediately** (revoke the key/webhook). Then purge it from history (`git filter-repo`) and force-push. Assume anything pushed to a public repo has already been copied. |
