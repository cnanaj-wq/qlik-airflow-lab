"""Fonctions partagées par les DAG. Bonne pratique : un DAG n'importe JAMAIS un autre fichier de DAG
(le DAG importé serait enregistré deux fois) ; le code commun vit dans un paquet."""

from __future__ import annotations

import os

from qlik_lab.clients.qlik_cloud import QlikCloudClient
from qlik_lab.config import QlikSettings

APPS = {
    "ingestion": "01_IngestiondataSales",
    "transform": "02_TransformDataSales",
    "operations": "03_Sales_Operations",
}
QLIK_CONN_ID = "qlik_cloud"


def qlik_client() -> QlikCloudClient:
    """Client construit depuis la connexion Airflow « qlik_cloud » (jamais de clé en dur)."""
    from airflow.sdk import BaseHook

    conn = BaseHook.get_connection(QLIK_CONN_ID)
    return QlikCloudClient(
        QlikSettings(
            tenant_url=conn.host or "",
            api_key=conn.password or "",
            space_id=os.environ.get("QLIK_SPACE_ID") or None,
            app_prefix=os.environ.get("QLIK_APP_PREFIX", ""),
        )
    )


def publier_volume(lignes: int) -> int:
    """XCom « row_count » : volume affiché par Pipeline Lens."""
    from airflow.sdk import get_current_context

    get_current_context()["ti"].xcom_push(key="row_count", value=lignes)
    return lignes
