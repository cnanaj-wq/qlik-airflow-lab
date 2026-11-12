-- Schéma de supervision (exécuté au premier démarrage du conteneur).
-- Les DAG le recréent aussi (CREATE TABLE IF NOT EXISTS) : source unique = src/qlik_lab/supervision.py.

CREATE TABLE IF NOT EXISTS reload_runs (
    reload_id      TEXT PRIMARY KEY,
    app_id         TEXT NOT NULL,
    app_name       TEXT,
    status         TEXT NOT NULL,
    trigger_source TEXT NOT NULL DEFAULT 'airflow',
    dag_id         TEXT,
    dag_run_id     TEXT,
    task_id        TEXT,
    creation_time  TIMESTAMPTZ,
    start_time     TIMESTAMPTZ,
    end_time       TIMESTAMPTZ,
    duration_s     NUMERIC,
    rows_loaded    BIGINT,
    error_code     TEXT,
    error_message  TEXT,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dq_results (
    id               BIGSERIAL PRIMARY KEY,
    dag_run_id       TEXT NOT NULL,
    business_date    DATE,
    check_name       TEXT NOT NULL,
    value            NUMERIC,
    threshold        NUMERIC,
    status           TEXT NOT NULL,
    blocking         BOOLEAN NOT NULL,
    detail           TEXT,
    revenue_last_day NUMERIC,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vue prête pour l'historique des échecs (et pour une future app Qlik de supervision).
CREATE OR REPLACE VIEW v_reload_failures AS
SELECT app_name, status, trigger_source, dag_run_id, start_time, end_time, duration_s, error_message
FROM reload_runs
WHERE status <> 'SUCCEEDED'
ORDER BY coalesce(end_time, recorded_at) DESC;
