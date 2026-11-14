from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import responses

from qlik_lab import alerting
from qlik_lab.clients import QlikCloudClient
from qlik_lab.config import QlikSettings
from qlik_lab.deploy import deploy, load_manifest

BASE = "https://lab.fr.qlikcloud.com"
ROOT = Path(__file__).resolve().parents[1]


@responses.activate
def test_deploy_creates_missing_app_and_pushes_script() -> None:
    client = QlikCloudClient(
        QlikSettings(tenant_url=BASE, api_key="k", app_prefix="DEV_"), sleep=lambda _: None
    )
    specs = load_manifest(ROOT / "qlik" / "apps.toml")[:1]
    responses.get(f"{BASE}/api/v1/items", json={"data": []})
    responses.post(f"{BASE}/api/v1/apps", json={"attributes": {"id": "new-app"}})
    responses.post(f"{BASE}/api/v1/apps/new-app/scripts", status=200)
    res = deploy(client, specs, "abc1234")
    assert res[0].created and res[0].name == "DEV_01_IngestiondataSales"
    pushed = json.loads(responses.calls[2].request.body or b"{}")
    assert "SET vScriptVersion = 'abc1234';" in pushed["script"]
    assert pushed["versionMessage"] == "deploy abc1234"


@responses.activate
def test_deploy_dry_run_writes_nothing() -> None:
    client = QlikCloudClient(QlikSettings(tenant_url=BASE, api_key="k"), sleep=lambda _: None)
    specs = load_manifest(ROOT / "qlik" / "apps.toml")
    responses.get(f"{BASE}/api/v1/items", json={"data": []})
    deploy(client, specs, "x", dry_run=True)
    assert all(c.request.method == "GET" for c in responses.calls)


def _ctx(exc: Exception | None = None) -> dict[str, object]:
    ti = SimpleNamespace(
        dag_id="qlik_sales_pipeline", task_id="transform.quality_gate", try_number=1, max_tries=0
    )
    return {"task_instance": ti, "run_id": "manual__2026-11-12", "exception": exc}


def test_quality_mail_says_chain_is_green_but_figures_wrong() -> None:
    checks = [
        {
            "controle": "Lignes en double (OrderLineID)",
            "valeur": 92,
            "seuil": 0,
            "statut": "ko",
            "detail": "x",
        }
    ]
    sujet, corps = alerting.composer_alerte(_ctx(), "http://localhost:5173", "qualite", checks=checks)
    assert "QUALITÉ" in sujet and "publication bloquée" in sujet
    assert "SUCCEEDED" in corps and "Lignes en double" in corps and "localhost:5173" in corps


def test_incident_history_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerting, "INCIDENTS", tmp_path / "incidents.jsonl")
    monkeypatch.delenv("ALERTE_EMAIL_A", raising=False)
    ctx = _ctx(ConnectionError("panne"))
    alerting.alerte_essai(ctx)
    assert alerting.incident_ouvert("qlik_sales_pipeline", "transform.quality_gate", "manual__2026-11-12")
    alerting.signaler_reprise(ctx)
    lines = [json.loads(x) for x in (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["evenement"] for e in lines] == ["echec_essai", "reprise"]
    assert (
        alerting.incident_ouvert("qlik_sales_pipeline", "transform.quality_gate", "manual__2026-11-12")
        is None
    )


def test_quality_incident_is_visible_to_lens_and_closed_by_next_green_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Porte rouge dans le run A, verte dans le run B : la reprise est écrite sous le run A."""
    monkeypatch.setattr(alerting, "INCIDENTS", tmp_path / "incidents.jsonl")
    monkeypatch.delenv("ALERTE_EMAIL_A", raising=False)
    exc = Exception("Porte qualité ROUGE")
    exc.checks = []  # type: ignore[attr-defined]
    ctx_a = _ctx(exc)
    alerting.alerte_echec(ctx_a)

    ctx_b = {**_ctx(), "run_id": "manual__2026-11-13"}
    alerting.signaler_reprise(ctx_b)
    events = [json.loads(x) for x in (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    # « echec » : le seul type d'échec définitif que lit le panneau Incidents de Lens.
    assert [(e["evenement"], e["run_id"]) for e in events] == [
        ("echec", "manual__2026-11-12"),
        ("reprise", "manual__2026-11-12"),
    ]
    assert events[0]["nature"] == "qualite" and events[1]["repris_par"] == "manual__2026-11-13"
    assert alerting.incidents_qualite_ouverts("qlik_sales_pipeline", "transform.quality_gate") == []

    alerting.signaler_reprise({**_ctx(), "run_id": "manual__2026-11-14"})  # rien à clore
    assert len((tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()) == 2
