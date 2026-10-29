---
title: Fight Analyser
emoji: 🥊
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8501
pinned: false
---

# Fight Analyser (Hugging Face Space)

This file is the Space card. To deploy (see docs/DEPLOYMENT.md §C):

1. Create a new **Docker** Space (CPU basic, free).
2. In the Space's *Settings → Variables and secrets*, add `DATA_RELEASE_URL` =
   `https://github.com/<you>/fight-analyser/releases/download/data-latest/fightdb.duckdb`
   and optionally `APP_PASSWORD` (as a secret).
3. Push this repository to the Space with `deploy/Dockerfile` copied to `Dockerfile` at the root and
   this file copied to `README.md` at the root:

```bash
git clone https://huggingface.co/spaces/<you>/fight-analyser hf-space
rsync -a --exclude .git --exclude data/sources --exclude .env ./ hf-space/
cp deploy/Dockerfile hf-space/Dockerfile
cp deploy/huggingface/README.md hf-space/README.md
cd hf-space && git add -A && git commit -m "Deploy" && git push
```

The daily data refresh keeps running in GitHub Actions; the Space downloads the new database itself.
