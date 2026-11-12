"""Historique des reloads et des contrôles qualité dans Postgres (base qlik_supervision).

Règle : la supervision ne doit JAMAIS faire échouer la production. Toute erreur d'écriture est
journalisée puis ignorée.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

CONN_ID = "qlik_supervision"

SCHEMA_SQL = """
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
    id             BIGSERIAL PRIMARY KEY,
    dag_run_id     TEXT NOT NULL,
    business_date  DATE,
    check_name     TEXT NOT NULL,
    value          NUMERIC,
    threshold      NUMERIC,
    status         TEXT NOT NULL,
    blocking       BOOLEAN NOT NULL,
    detail         TEXT,
    revenue_last_day NUMERIC,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _hook() -> Any:
    from airflow.providers.postgres.hooks.postgres import PostgresHook

    return PostgresHook(postgres_conn_id=CONN_ID)


def _execute(sql: str, rows: list[tuple[Any, ...]]) -> None:
    try:
        hook = _hook()
        with hook.get_conn() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            for r in rows:
                cur.execute(sql, r)
            conn.commit()
    except Exception:
        log.exception("Supervision indisponible : écriture ignorée")


def record_reload(
    record: dict[str, Any],
    app_name: str,
    source: str = "airflow",
    dag_id: str | None = None,
    dag_run_id: str | None = None,
    task_id: str | None = None,
    rows_loaded: int | None = None,
) -> None:
    sql = """
    INSERT INTO reload_runs (reload_id, app_id, app_name, status, trigger_source, dag_id, dag_run_id, task_id,
        creation_time, start_time, end_time, duration_s, rows_loaded, error_code, error_message)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (reload_id) DO UPDATE SET status = EXCLUDED.status, end_time = EXCLUDED.end_time,
        duration_s = EXCLUDED.duration_s, error_code = EXCLUDED.error_code,
        error_message = EXCLUDED.error_message,
        rows_loaded = COALESCE(EXCLUDED.rows_loaded, reload_runs.rows_loaded),
        dag_run_id = COALESCE(reload_runs.dag_run_id, EXCLUDED.dag_run_id),
        trigger_source = CASE WHEN reload_runs.trigger_source = 'airflow' THEN 'airflow'
                              ELSE EXCLUDED.trigger_source END
    """
    _execute(
        sql,
        [
            (
                record["reload_id"],
                record["app_id"],
                app_name,
                record["status"],
                source,
                dag_id,
                dag_run_id,
                task_id,
                record.get("creation_time"),
                record.get("start_time"),
                record.get("end_time"),
                record.get("duration_s"),
                rows_loaded,
                record.get("error_code"),
                record.get("error_message"),
            )
        ],
    )


def record_checks(
    dag_run_id: str, business_date: str, checks: list[dict[str, Any]], revenue_last_day: float | None
) -> None:
    sql = """
    INSERT INTO dq_results (dag_run_id, business_date, check_name, value, threshold, status, blocking, detail,
        revenue_last_day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    _execute(
        sql,
        [
            (
                dag_run_id,
                business_date,
                c["controle"],
                c["valeur"],
                c["seuil"],
                c["statut"],
                c["bloquant"],
                c.get("detail"),
                revenue_last_day,
            )
            for c in checks
        ],
    )


def revenue_history(limit: int = 7) -> list[float]:
    """CA du jour des derniers runs dont la porte qualité était verte (une valeur par date)."""
    sql = """
    SELECT DISTINCT ON (business_date) revenue_last_day FROM dq_results r
    WHERE revenue_last_day IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM dq_results k WHERE k.dag_run_id = r.dag_run_id AND k.blocking AND k.status = 'ko')
    ORDER BY business_date DESC, recorded_at DESC LIMIT %s
    """
    try:
        hook = _hook()
        with hook.get_conn() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            conn.commit()
            cur.execute(sql, (limit,))
            return [float(r[0]) for r in cur.fetchall()]
    except Exception:
        log.exception("Historique de CA indisponible : contrôle de dérive non évalué")
        return []


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
