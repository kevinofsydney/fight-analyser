-- Fight Analyser core schema (DuckDB). Contract file: see AGENTS.md before changing.
-- IDs: UFCStats hex IDs where a source provides them ("ufcstats" alias), otherwise
-- "<source>:<id>" (e.g. "espn:401914105", "fc:372536").
-- Derived tables (fighter_snapshots, matchup_projections, division_baselines, ...) are
-- rebuilt by `fightdb build` and are defined by the code that creates them.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS events (
    event_id    VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    date        DATE,
    location    VARCHAR,
    status      VARCHAR NOT NULL,            -- 'upcoming' | 'completed' | 'cancelled' | 'past'
    source      VARCHAR NOT NULL,            -- 'fight_canon' | 'greco' | 'espn'
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS fighters (
    fighter_id  VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    nickname    VARCHAR,
    dob         DATE,
    height_cm   DOUBLE,
    reach_cm    DOUBLE,
    stance      VARCHAR,
    sex         VARCHAR,                     -- 'M' | 'F' | NULL
    pro_wins    INTEGER,
    pro_losses  INTEGER,
    pro_draws   INTEGER,
    nationality VARCHAR,
    source      VARCHAR NOT NULL,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS fighter_aliases (
    source      VARCHAR NOT NULL,            -- 'ufcstats' | 'fight_canon' | 'espn' | ...
    source_id   VARCHAR NOT NULL,
    fighter_id  VARCHAR NOT NULL,
    name        VARCHAR,
    method      VARCHAR,                     -- how the link was made: 'source' | 'exact' | 'fuzzy' | 'override'
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS bouts (
    bout_id          VARCHAR PRIMARY KEY,
    event_id         VARCHAR NOT NULL,
    weight_class     VARCHAR,
    division         VARCHAR,
    sex              VARCHAR,
    is_title         BOOLEAN NOT NULL DEFAULT FALSE,
    scheduled_rounds INTEGER,
    card_order       INTEGER,                -- 0 = main event, larger = earlier on the card
    fighter_a_id     VARCHAR NOT NULL,
    fighter_b_id     VARCHAR NOT NULL,
    status           VARCHAR NOT NULL,       -- 'scheduled' | 'completed' | 'cancelled' | 'past' (ESPN listing after the event)
    result_a         VARCHAR,                -- 'W' | 'L' | 'D' | 'NC' from fighter A's side
    winner_id        VARCHAR,
    method           VARCHAR,
    method_group     VARCHAR,                -- 'ko_tko' | 'sub' | 'dec' | 'dq' | 'nc' | 'other'
    details          VARCHAR,
    end_round        INTEGER,
    end_time_s       INTEGER,
    fight_seconds    INTEGER,                -- total elapsed fight time
    time_format      VARCHAR,
    referee          VARCHAR,
    replaces_bout_id VARCHAR,
    source           VARCHAR NOT NULL,
    first_seen_at    TIMESTAMP DEFAULT current_timestamp,
    last_seen_at     TIMESTAMP DEFAULT current_timestamp,
    updated_at       TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS round_stats (
    bout_id     VARCHAR NOT NULL,
    fighter_id  VARCHAR NOT NULL,
    round       INTEGER NOT NULL,
    kd          INTEGER,
    sig_l       INTEGER, sig_a     INTEGER,
    tot_l       INTEGER, tot_a     INTEGER,
    td_l        INTEGER, td_a      INTEGER,
    sub_att     INTEGER,
    rev         INTEGER,
    ctrl_s      INTEGER,
    head_l      INTEGER, head_a    INTEGER,
    body_l      INTEGER, body_a    INTEGER,
    leg_l       INTEGER, leg_a     INTEGER,
    dist_l      INTEGER, dist_a    INTEGER,
    clinch_l    INTEGER, clinch_a  INTEGER,
    ground_l    INTEGER, ground_a  INTEGER,
    PRIMARY KEY (bout_id, fighter_id, round)
);

CREATE TABLE IF NOT EXISTS odds (
    bout_id            VARCHAR NOT NULL,
    fighter_id         VARCHAR NOT NULL,
    open_american      INTEGER,
    close_low          INTEGER,
    close_high         INTEGER,
    implied_prob_close DOUBLE,               -- vig-free, from the midpoint of the closing range
    PRIMARY KEY (bout_id, fighter_id)
);

CREATE TABLE IF NOT EXISTS card_changes (
    change_id    VARCHAR PRIMARY KEY,
    detected_at  TIMESTAMP NOT NULL,
    run_id       VARCHAR,
    event_id     VARCHAR,
    bout_id      VARCHAR,
    change_type  VARCHAR NOT NULL,
    summary      VARCHAR NOT NULL,
    old_value    VARCHAR,                    -- JSON
    new_value    VARCHAR                     -- JSON
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id          VARCHAR PRIMARY KEY,
    kind            VARCHAR NOT NULL,        -- 'backfill' | 'refresh' | 'build'
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          VARCHAR NOT NULL,        -- 'running' | 'ok' | 'failed'
    source          VARCHAR,
    source_version  VARCHAR,
    max_event_date  DATE,
    counts          VARCHAR,                 -- JSON
    error           VARCHAR
);

CREATE TABLE IF NOT EXISTS model_evals (
    model_name        VARCHAR NOT NULL,
    test_year         INTEGER NOT NULL,
    n                 INTEGER,
    brier             DOUBLE,
    log_loss          DOUBLE,
    accuracy          DOUBLE,
    brier_baseline    DOUBLE,
    brier_market      DOUBLE,              -- closing line, on bouts that have odds
    brier_model_on_market_rows DOUBLE,     -- our model, on the same bouts
    n_market          INTEGER,
    reliability       VARCHAR,               -- JSON list of {bin, mean_pred, mean_obs, n}
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (model_name, test_year)
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
