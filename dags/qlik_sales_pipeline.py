"""Chaîne Ventes Qlik Cloud orchestrée par Airflow : extraction → 01 → 02 → porte qualité → 03.

Lecture rapide (équivalences Control-M entre parenthèses) :
- le DAG est un flux de jobs (folder / job flow), chaque tâche un job ;
- trigger_* déclenche un reload Qlik (job « command ») et rend son identifiant ;
- wait_* est un CAPTEUR en mode reschedule : il interroge le statut toutes les 15 s sans occuper
  de slot entre deux interrogations (condition / « wait for ») ;
- quality_gate compare les chiffres de Qlik aux totaux de la source : un reload SUCCEEDED ne
  prouve pas que les chiffres sont justes ;
- 03_Sales_Operations n'est rechargée que si la porte est verte (publication conditionnelle).

Scénarios de démonstration (Trigger DAG w/ config) :
  {"scenario": "clean"}                 chaîne nominale
  {"scenario": "duplicate"}             lot livré deux fois : reloads verts, porte qualité ROUGE
  {"transient_failures": 1}             panne passagère sur le dépôt, reprise automatique
  Variable Airflow qlik_lab_fault=missing_file : fichier renommé à la source, reload 01 FAILED,
  puis reprise sur incident (voir docs/SCENARIOS.md).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from airflow.sdk.exceptions import AirflowFailException
from airflow.sdk import Param, PokeReturnValue, Variable, dag, get_current_context, task, task_group

from qlik_lab import supervision
from qlik_lab.airflow_helpers import APPS, publier_volume, qlik_client
from qlik_lab.alerting import alerte_echec, alerte_essai, signaler_reprise
from qlik_lab.dq import blocking_failures, evaluate, parse_dq_markers
from qlik_lab.sales_data import generate_extract

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("QLIK_LAB_DATA", "/opt/airflow/data/qlik_lab"))
SOURCE_FILES = {
    "orders": "qal_sales_orders.csv",
    "customers": "qal_customers.csv",
    "products": "qal_products.csv",
}


class DataQualityError(AirflowFailException):
    """Porte qualité rouge. Non réessayée : rejouer ne corrige pas une source fausse."""

    def __init__(self, message: str, checks: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.checks = checks


def business_date(context: dict[str, Any]) -> date:
    forced = (context["params"] or {}).get("business_date")
    if forced:
        return date.fromisoformat(forced)
    logical = context.get("logical_date")
    return (logical or datetime.now()).date()


def error_lines(script_log: str, limit: int = 8) -> str:
    """Lignes utiles d'un log de script en échec (erreurs Qlik, marqueurs DQ_FATAL)."""
    pattern = re.compile(r"(error|erreur|cannot|impossible|DQ_FATAL|failed)", re.IGNORECASE)
    hits = [ln.strip() for ln in script_log.splitlines() if pattern.search(ln)]
    return "\n".join(hits[-limit:]) or script_log[-800:]


# Reprise automatique : 2 nouveaux essais, délai qui double (30 s puis 60 s).
# Les erreurs « déterministes » (script Qlik en échec, porte qualité) lèvent AirflowFailException
# et ne sont PAS réessayées : réessayer une erreur de logique ne fait que retarder l'alerte.
DEFAULT_ARGS = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": 2.0,
    "max_retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=15),
    "on_retry_callback": alerte_essai,
    "on_failure_callback": alerte_echec,
    "on_success_callback": signaler_reprise,
}


@dag(
    dag_id="qlik_sales_pipeline",
    description="Ventes : extraction ERP (synthétique) → reloads Qlik Cloud 01/02 → porte qualité → 03",
    schedule="0 6 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,  # deux runs en parallèle se marcheraient dessus (mêmes fichiers, mêmes apps)
    dagrun_timeout=timedelta(hours=2),
    default_args=DEFAULT_ARGS,
    tags=["qlik", "qlik-cloud", "ventes", "etl", "rejouable"],
    doc_md=(
        "Idempotent : l'extraction est déterministe pour une date de gestion, le dépôt REMPLACE les "
        "fichiers dans Qlik et chaque reload est complet. Un rejeu produit donc le même résultat, "
        "sans doublon. Rejeu autorisé depuis Pipeline Lens.\n\n" + (__doc__ or "")
    ),
    params={
        "scenario": Param(
            "clean",
            enum=["clean", "duplicate"],
            description="clean = nominal ; duplicate = le dernier lot est livré deux fois",
        ),
        "transient_failures": Param(
            0,
            type="integer",
            minimum=0,
            maximum=3,
            description="nombre d'essais du dépôt qui échouent (panne passagère)",
        ),
        "business_date": Param(None, type=["null", "string"], description="AAAA-MM-JJ ; vide = date du run"),
    },
)
def qlik_sales_pipeline() -> None:
    # ------------------------------------------------------------------ EXTRACT
    @task_group(group_id="extract")
    def extract() -> None:
        @task
        def generate_sales_extract() -> dict[str, Any]:
            ctx = get_current_context()
            day = business_date(ctx)
            ext = generate_extract(day, ctx["params"]["scenario"])
            out = DATA_DIR / day.isoformat()
            out.mkdir(parents=True, exist_ok=True)
            (out / SOURCE_FILES["orders"]).write_bytes(ext.orders_csv)
            (out / SOURCE_FILES["customers"]).write_bytes(ext.customers_csv)
            (out / SOURCE_FILES["products"]).write_bytes(ext.products_csv)
            log.info(
                "Extraction %s : %s lignes écrites dont %s rejouées ; totaux source %s",
                day,
                ext.rows_written,
                ext.duplicated_rows,
                ext.control,
            )
            publier_volume(ext.rows_written)
            # XCom = petites métadonnées seulement. Les fichiers restent sur disque.
            return {"dir": str(out), "control": ext.control.to_dict(), "duplicated_rows": ext.duplicated_rows}

        @task
        def upload_to_qlik(extract_info: dict[str, Any]) -> dict[str, Any]:
            ctx = get_current_context()
            if ctx["ti"].try_number <= int(ctx["params"]["transient_failures"]):
                raise ConnectionError(
                    f"Panne passagère SIMULÉE : tenant Qlik injoignable (essai {ctx['ti'].try_number})"
                )
            client = qlik_client()
            folder = Path(extract_info["dir"])
            fault = Variable.get("qlik_lab_fault", default="")
            uploaded = {}
            for key, name in SOURCE_FILES.items():
                target = name
                if key == "orders" and fault == "missing_file":
                    # Incident SIMULÉ côté source : le fichier arrive sous un autre nom. On retire
                    # l'ancien fichier pour que le reload 01 ne relise pas silencieusement celui de la
                    # veille (risque réel : des données « fraîches » qui ne le sont pas).
                    target = name.replace(".csv", "_v2.csv")
                    client.delete_data_file(name)
                    log.warning("Incident simulé actif (qlik_lab_fault=missing_file) : dépôt sous %s", target)
                uploaded[target] = client.upload_data_file(target, (folder / name).read_bytes())
            publier_volume(int(extract_info["control"]["rows"]) + int(extract_info["duplicated_rows"]))
            return {"files": sorted(uploaded)}

        upload_to_qlik(generate_sales_extract())

    # --------------------------------------------- reload : déclencher puis attendre
    @task
    def trigger_reload(app_key: str) -> dict[str, Any]:
        ctx = get_current_context()
        client = qlik_client()
        name = f"{client.settings.app_prefix}{APPS[app_key]}"
        app_id = client.resolve_app_id(name)  # par NOM : les identifiants diffèrent d'un tenant à l'autre
        reload_id = client.trigger_reload(app_id, variables={"vRunId": ctx["run_id"][:200]})
        log.info("Reload %s lancé pour %s (%s)", reload_id, name, app_id)
        return {"app_key": app_key, "app_name": name, "app_id": app_id, "reload_id": reload_id}

    @task.sensor(poke_interval=15, timeout=1800, mode="reschedule")
    def wait_reload(trig: dict[str, Any]) -> PokeReturnValue:
        ctx = get_current_context()
        client = qlik_client()
        run = client.get_status(trig["reload_id"])
        if not run.status.is_final:
            log.info("%s : %s", trig["app_name"], run.status.value)
            return PokeReturnValue(is_done=False)

        script_log = ""
        try:
            script_log = client.get_script_log(trig["app_id"], trig["reload_id"])
        except Exception:
            log.warning("Log de script indisponible pour %s", trig["reload_id"], exc_info=True)
        markers = parse_dq_markers(script_log, app=APPS[trig["app_key"]])
        rows = int(markers["rows"]) if markers.get("rows", "").isdigit() else None
        supervision.record_reload(
            run.to_record(),
            trig["app_name"],
            dag_id=ctx["dag"].dag_id,
            dag_run_id=ctx["run_id"],
            task_id=ctx["ti"].task_id,
            rows_loaded=rows,
        )
        if not run.status.is_success:
            raise AirflowFailException(
                f"Reload {trig['app_name']} {run.status.value} : {run.error_message or 'sans message'}\n"
                f"Extrait du log Qlik :\n{error_lines(script_log)}"
            )
        if rows is not None:
            publier_volume(rows)
        log.info("%s SUCCEEDED en %.0f s, marqueurs DQ : %s", trig["app_name"], run.duration_s or 0, markers)
        return PokeReturnValue(is_done=True, xcom_value={**trig, **run.to_record(), "dq": markers})

    # ---------------------------------------------------------------- TRANSFORM
    @task_group(group_id="transform")
    def transform() -> None:
        t1 = trigger_reload.override(task_id="trigger_ingestion")("ingestion")
        w1 = wait_reload.override(task_id="wait_ingestion")(t1)
        t2 = trigger_reload.override(task_id="trigger_transform")("transform")
        w2 = wait_reload.override(task_id="wait_transform")(t2)
        w1 >> t2

        @task(retries=0)
        def quality_gate(transform_result: dict[str, Any]) -> dict[str, Any]:
            ctx = get_current_context()
            metrics = transform_result.get("dq") or {}
            if "rows" not in metrics:
                raise AirflowFailException(
                    "Marqueur « TRACE DQ|… » absent du log de 02 : contrôle impossible"
                )
            info = ctx["ti"].xcom_pull(task_ids="extract.generate_sales_extract")
            control = info["control"]
            results = evaluate(metrics, control, supervision.revenue_history())
            checks = [c.to_dict() for c in results]
            ctx["ti"].xcom_push(key="quality_checks", value=checks)  # lu par Pipeline Lens
            supervision.record_checks(
                ctx["run_id"], control["business_date"], checks, float(metrics.get("revenue_last_day", 0))
            )
            ko = [c for c in checks if c["statut"] == "ko"]
            for c in ko:
                log.warning(
                    "Contrôle KO : %s = %s (seuil %s) — %s",
                    c["controle"],
                    c["valeur"],
                    c["seuil"],
                    c["detail"],
                )
            if blocking_failures(results):
                raise DataQualityError(
                    "Porte qualité ROUGE : chaîne technique verte mais chiffres faux. Publication de "
                    f"03 bloquée. {'; '.join(c['controle'] + ' = ' + str(c['valeur']) for c in ko)}",
                    checks,
                )
            return {"checks": len(checks), "ko_non_bloquants": len(ko)}

        quality_gate(w2)

    # --------------------------------------------------------------------- LOAD
    @task_group(group_id="load")
    def load() -> None:
        t3 = trigger_reload.override(task_id="trigger_operations")("operations")
        wait_reload.override(task_id="wait_operations")(t3)

    # Dépendances entre groupes : tout extract avant transform, la porte qualité avant load.
    extract() >> transform() >> load()


qlik_sales_pipeline()
