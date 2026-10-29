"""Runtime settings, read from environment variables and `.env` (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

FIGHT_CANON_REPO = "Jadevit/fight-canon-db"
FIGHT_CANON_ASSET = "canon.db"
GRECO_RAW_BASE = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main"
GRECO_FILES = (
    "ufc_event_details.csv",
    "ufc_fight_details.csv",
    "ufc_fight_results.csv",
    "ufc_fight_stats.csv",
    "ufc_fighter_details.csv",
    "ufc_fighter_tott.csv",
)
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_path: Path = Path("data/fightdb.duckdb")
    download_dir: Path = Path("data/sources")
    overrides_dir: Path = Path("data/overrides")
    hypotheses_dir: Path = Path("hypotheses")

    history_source: Literal["fight_canon", "greco"] = "fight_canon"
    history_stale_days: int = 7
    http_user_agent: str = "fight-analyser/0.1 (+https://github.com/kevinofsydney/fight-analyser)"

    refresh_cron: str = "0 11 * * *"

    units: Literal["metric", "imperial", "both"] = "both"
    stale_after_hours: int = 36
    db_max_age_hours: int = 12
    data_release_url: str = ""

    app_password: str = ""
    discord_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
