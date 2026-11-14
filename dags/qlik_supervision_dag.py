"""Collecte quotidienne de l'historique des reloads Qlik Cloud (tous déclencheurs confondus).

Le DAG de pipeline historise ses propres reloads. Celui-ci rattrape ceux lancés AILLEURS (hub,
planification Qlik, collègue) : la supervision doit voir tout ce qui touche aux apps, pas
seulement ce qu'Airflow a lancé. Écriture idempotente (upsert sur reload_id).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.sdk import dag, get_current_context, task, task_group

from qlik_lab import supervision
from qlik_lab.airflow_helpers import APPS, publier_volume, qlik_client
from qlik_lab.alerting import alerte_echec, alerte_essai, signaler_reprise

log = logging.getLogger(__name__)


@dag(
    dag_id="qlik_supervision_dag",
    description="Historique des reloads Qlik Cloud → Postgres qlik_supervision",
    schedule="0 7 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    tags=["qlik", "supervision", "rejouable"],
    doc_md="Idempotent : upsert par reload_id, un rejeu ne crée aucun doublon. Rejeu autorisé.",
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
        "on_retry_callback": alerte_essai,
        "on_failure_callback": alerte_echec,
        "on_success_callback": signaler_reprise,
    },
)
def qlik_supervision_dag() -> None:
    @task_group(group_id="extract")
    def extract() -> None:
        @task
        def collect_reload_history() -> int:
            ctx = get_current_context()
            client = qlik_client()
            total = 0
            for app_name in APPS.values():
                name = f"{client.settings.app_prefix}{app_name}"
                app_id = client.find_app_id(name)
                if not app_id:
                    log.warning("App %s absente : ignorée", name)
                    continue
                for run in client.list_reloads(app_id, limit=100):
                    supervision.record_reload(
                        run.to_record(), name, source=run.type or "qlik", dag_id=ctx["dag"].dag_id
                    )
                    total += 1
            return publier_volume(total)

        collect_reload_history()

    extract()


qlik_supervision_dag()
