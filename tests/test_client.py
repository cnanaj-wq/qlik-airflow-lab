"""Client Qlik Cloud contre une API mockée : aucun appel réseau réel."""

from __future__ import annotations

import json

import pytest
import responses

from qlik_lab.clients import QlikCloudClient, QlikReloadClient
from qlik_lab.config import QlikSettings
from qlik_lab.errors import (
    AppResolutionError,
    QlikApiError,
    QlikAuthError,
    QlikConfigError,
    QlikRedirectError,
    ReloadFailedError,
    ReloadTimeoutError,
)
from qlik_lab.models import ReloadStatus

BASE = "https://lab.fr.qlikcloud.com"
FAKE_KEY = "fake-test-key"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> QlikCloudClient:
    return QlikCloudClient(QlikSettings(tenant_url=BASE, api_key=FAKE_KEY), sleep=sleeps.append)


def reload_body(status: str, **extra: str) -> dict[str, str]:
    return {
        "id": "r1",
        "appId": "a1",
        "status": status,
        "startTime": "2026-11-12T06:00:00Z",
        "endTime": "2026-11-12T06:01:30Z",
        **extra,
    }


def test_settings_never_expose_key() -> None:
    s = QlikSettings(tenant_url="lab.fr.qlikcloud.com/", api_key="super-secret")
    assert "super-secret" not in repr(s)
    assert s.tenant_url == BASE


@pytest.mark.parametrize("url", ["http://lab.fr.qlikcloud.com", "https://lab.fr.qlikcloud.com/api/v1"])
def test_settings_reject_bad_url(url: str) -> None:
    with pytest.raises(QlikConfigError):
        QlikSettings(tenant_url=url, api_key="k")


def test_settings_require_key() -> None:
    with pytest.raises(QlikConfigError):
        QlikSettings.from_env({"QLIK_TENANT_URL": BASE})


def test_client_implements_protocol(client: QlikCloudClient) -> None:
    assert isinstance(client, QlikReloadClient)


@responses.activate
def test_redirect_is_never_followed(client: QlikCloudClient) -> None:
    """Le piège /users/me : 301 -> on lève une erreur explicite, sans suivre."""
    responses.get(f"{BASE}/api/v1/reloads/r1", status=301, headers={"Location": f"{BASE}/api/v1/users/123"})
    with pytest.raises(QlikRedirectError) as exc:
        client.get_status("r1")
    assert exc.value.location.endswith("/api/v1/users/123")
    assert len(responses.calls) == 1  # aucune seconde requête


@responses.activate
def test_bearer_header_sent(client: QlikCloudClient) -> None:
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("SUCCEEDED"))
    client.get_status("r1")
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {FAKE_KEY}"


@responses.activate
def test_401_raises_auth_error_without_leaking_key(client: QlikCloudClient) -> None:
    responses.get(f"{BASE}/api/v1/reloads/r1", status=401)
    with pytest.raises(QlikAuthError) as exc:
        client.get_status("r1")
    assert FAKE_KEY not in str(exc.value)


@responses.activate
def test_429_is_retried_with_retry_after(client: QlikCloudClient, sleeps: list[float]) -> None:
    responses.post(f"{BASE}/api/v1/reloads", status=429, headers={"Retry-After": "3"})
    responses.post(f"{BASE}/api/v1/reloads", json={"id": "r42"}, status=201)
    assert client.trigger_reload("a1") == "r42"
    assert sleeps == [3.0]


@responses.activate
def test_503_retried_for_get(client: QlikCloudClient, sleeps: list[float]) -> None:
    responses.get(f"{BASE}/api/v1/reloads/r1", status=503)
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("RELOADING"))
    assert client.get_status("r1").status is ReloadStatus.RELOADING
    assert sleeps == [1.0]


@responses.activate
def test_503_not_retried_for_post(client: QlikCloudClient) -> None:
    """Un POST /reloads rejoué pourrait lancer deux reloads : pas de retry sur 5xx."""
    responses.post(f"{BASE}/api/v1/reloads", status=503)
    with pytest.raises(QlikApiError):
        client.trigger_reload("a1")
    assert len(responses.calls) == 1


@responses.activate
def test_trigger_reload_sends_variables(client: QlikCloudClient) -> None:
    responses.post(f"{BASE}/api/v1/reloads", json={"id": "r1"}, status=201)
    client.trigger_reload("a1", {"vRunId": "scheduled__2026"})
    body = json.loads(responses.calls[0].request.body or b"{}")
    assert body == {"appId": "a1", "partial": False, "variables": {"vRunId": "scheduled__2026"}}


@responses.activate
def test_first_reload_is_sent_without_variables(client: QlikCloudClient) -> None:
    """Qlik refuse « variables » sur une app jamais rechargée (variable not found)."""
    responses.get(f"{BASE}/api/v1/reloads", json={"data": [reload_body("FAILED")]})
    assert client.reload_variables("a1", {"vRunId": "x"}) is None
    responses.replace(responses.GET, f"{BASE}/api/v1/reloads", json={"data": [reload_body("SUCCEEDED")]})
    assert client.reload_variables("a1", {"vRunId": "x"}) == {"vRunId": "x"}


@responses.activate
def test_wait_for_reload_success(client: QlikCloudClient, sleeps: list[float]) -> None:
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("QUEUED"))
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("RELOADING"))
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("SUCCEEDED"))
    run = client.wait_for_reload("r1", interval_s=5)
    assert run.status is ReloadStatus.SUCCEEDED
    assert run.duration_s == 90
    assert sleeps == [5, 5]


@responses.activate
def test_wait_for_reload_failure(client: QlikCloudClient) -> None:
    responses.get(
        f"{BASE}/api/v1/reloads/r1",
        json=reload_body("FAILED", errorMessage="Cannot open file qal_sales_orders.csv"),
    )
    with pytest.raises(ReloadFailedError, match="Cannot open file"):
        client.wait_for_reload("r1")


@responses.activate
def test_wait_for_reload_timeout(client: QlikCloudClient) -> None:
    responses.get(f"{BASE}/api/v1/reloads/r1", json=reload_body("RELOADING"))
    ticks = iter([0.0, 5.0, 11.0])
    with pytest.raises(ReloadTimeoutError):
        client.wait_for_reload("r1", timeout_s=10, interval_s=5, clock=lambda: next(ticks))


@responses.activate
def test_list_apps_follows_pagination_on_same_host(client: QlikCloudClient) -> None:
    responses.get(
        f"{BASE}/api/v1/items",
        json={
            "data": [{"resourceId": "a1", "name": "01_IngestiondataSales"}],
            "links": {"next": {"href": f"{BASE}/api/v1/items?next=abc"}},
        },
    )
    responses.get(
        f"{BASE}/api/v1/items", json={"data": [{"resourceId": "a2", "name": "02_TransformDataSales"}]}
    )
    assert [a.app_id for a in client.list_apps()] == ["a1", "a2"]


@responses.activate
def test_pagination_to_foreign_host_refused(client: QlikCloudClient) -> None:
    responses.get(
        f"{BASE}/api/v1/items", json={"data": [], "links": {"next": {"href": "https://evil.example/x"}}}
    )
    with pytest.raises(QlikApiError, match="hors du tenant"):
        client.list_apps()


@responses.activate
def test_resolve_app_by_exact_name(client: QlikCloudClient) -> None:
    responses.get(
        f"{BASE}/api/v1/items",
        json={
            "data": [
                {"resourceId": "a1", "name": "01_IngestiondataSales"},
                {"resourceId": "a9", "name": "01_IngestiondataSales (copie)"},
            ]
        },
    )
    assert client.resolve_app_id("01_IngestiondataSales") == "a1"


@responses.activate
def test_resolve_ambiguous_name(client: QlikCloudClient) -> None:
    responses.get(
        f"{BASE}/api/v1/items",
        json={"data": [{"resourceId": "a1", "name": "X"}, {"resourceId": "a2", "name": "X"}]},
    )
    with pytest.raises(AppResolutionError, match="ambigu"):
        client.resolve_app_id("X")


@responses.activate
def test_upload_replaces_existing_file(client: QlikCloudClient) -> None:
    responses.get(f"{BASE}/api/v1/data-files", json={"data": [{"id": "f1", "name": "qal_sales_orders.csv"}]})
    responses.put(f"{BASE}/api/v1/data-files/f1", json={"id": "f1"}, status=201)
    assert client.upload_data_file("qal_sales_orders.csv", b"a,b\n1,2\n") == "f1"
    assert responses.calls[1].request.method == "PUT"


@responses.activate
def test_upload_creates_new_file(client: QlikCloudClient) -> None:
    responses.get(f"{BASE}/api/v1/data-files", json={"data": []})
    responses.post(f"{BASE}/api/v1/data-files", json={"id": "f2"}, status=201)
    assert client.upload_data_file("qal_new.csv", b"a\n1\n") == "f2"
    body = responses.calls[1].request.body
    assert b'name="File"' in body and b'name="Json"' in body  # type: ignore[operator]
