"""Intégrité des DAG : à lancer DANS l'image Airflow (job CI « dags » ou en local) :

docker run --rm -v "${PWD}:/lab" -w /lab -e PYTHONPATH=/lab/src apache/airflow:3.4.0 \
  bash -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests/dags"
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("airflow")
from airflow.models import DagBag

DAGS_DIR = Path(__file__).resolve().parents[2] / "dags"


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    start = time.monotonic()
    bag = DagBag(dag_folder=str(DAGS_DIR))
    bag.load_seconds = time.monotonic() - start  # type: ignore[attr-defined]
    return bag


def test_no_import_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}


def test_expected_dags(dagbag: DagBag) -> None:
    assert {"qlik_sales_pipeline", "qlik_supervision_dag"} <= set(dagbag.dag_ids)


def test_parsing_is_fast(dagbag: DagBag) -> None:
    """Le dag-processor relit les fichiers toutes les 30 s : aucun appel réseau à l'import."""
    assert dagbag.load_seconds < 10  # type: ignore[attr-defined]


@pytest.mark.parametrize("dag_id", ["qlik_sales_pipeline", "qlik_supervision_dag"])
def test_conventions(dagbag: DagBag, dag_id: str) -> None:
    dag = dagbag.dags[dag_id]
    assert dag.max_active_runs == 1
    assert not dag.catchup
    assert dag.tags and "qlik" in dag.tags
    if "rejouable" in dag.tags:  # règle Pipeline Lens : un DAG rejouable justifie son idempotence
        assert "dempotent" in (dag.doc_md or "")
    for t in dag.tasks:
        assert t.on_failure_callback, f"{t.task_id} sans alerte d'échec"
        assert t.task_id.split(".")[0] in {"extract", "transform", "load"}, t.task_id


def test_pipeline_order(dagbag: DagBag) -> None:
    dag = dagbag.dags["qlik_sales_pipeline"]
    gate = dag.get_task("transform.quality_gate")
    publish = dag.get_task("load.trigger_operations")
    assert gate.task_id in {u.task_id for u in publish.get_flat_relatives(upstream=True)}
    assert dag.get_task("transform.wait_transform").mode == "reschedule"
    assert gate.retries == 0, "une porte qualité rouge ne se réessaie pas"
