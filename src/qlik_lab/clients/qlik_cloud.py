"""Adaptateur RÉEL : API REST Qlik Cloud.

Bonnes pratiques appliquées :
- timeout sur chaque appel (jamais de requête qui pend indéfiniment) ;
- AUCUN suivi de redirection (voir QlikRedirectError) ;
- 429 : on réessaie toujours, en respectant l'en-tête Retry-After ;
- 5xx : on réessaie seulement les méthodes idempotentes (GET, PUT, DELETE). Un POST /reloads
  rejoué après un 502 pourrait lancer deux reloads : on laisse l'orchestrateur décider ;
- la clé ne sort jamais : pas de log des en-têtes, pas de clé dans les messages d'erreur.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import requests

from qlik_lab.config import QlikSettings
from qlik_lab.errors import (
    AppResolutionError,
    QlikApiError,
    QlikAuthError,
    QlikNotFoundError,
    QlikRedirectError,
    ReloadFailedError,
    ReloadTimeoutError,
)
from qlik_lab.models import AppInfo, ReloadRun

log = logging.getLogger(__name__)

IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE", "HEAD"})
RETRYABLE_5XX = frozenset({500, 502, 503, 504})


class QlikCloudClient:
    def __init__(
        self,
        settings: QlikSettings,
        session: requests.Session | None = None,
        max_retries: int = 4,
        backoff_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.base_url = settings.tenant_url
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {settings.api_key}",
                "Accept": "application/json",
                "User-Agent": "qlik-airflow-lab/0.1",
            }
        )
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep

    # ------------------------------------------------------------------ HTTP
    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            # Liens de pagination : on refuse de partir sur un autre hôte avec la clé.
            if urlparse(path_or_url).netloc != urlparse(self.base_url).netloc:
                raise QlikApiError(0, "lien de pagination hors du tenant refusé", path_or_url)
            return path_or_url
        return f"{self.base_url}{path_or_url}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        method = method.upper()
        url = self._url(path)
        attempt = 0
        while True:
            attempt += 1
            resp = self.session.request(
                method, url, timeout=self.settings.timeout_s, allow_redirects=False, **kwargs
            )
            status = resp.status_code
            if 300 <= status < 400:
                raise QlikRedirectError(status, resp.headers.get("Location", ""), path)
            retryable = status == 429 or (status in RETRYABLE_5XX and method in IDEMPOTENT_METHODS)
            if retryable and attempt <= self.max_retries:
                delay = self._retry_delay(resp, attempt)
                log.warning(
                    "HTTP %s sur %s %s : nouvel essai %s dans %.1f s", status, method, path, attempt, delay
                )
                self._sleep(delay)
                continue
            if status in (401, 403):
                raise QlikAuthError(status, "authentification refusée (clé absente, expirée ou droits)", path)
            if status == 404:
                raise QlikNotFoundError(status, "ressource introuvable", path)
            if status >= 400:
                raise QlikApiError(status, _error_text(resp), path)
            return resp

    def _retry_delay(self, resp: requests.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return float(self.backoff_s * 2 ** (attempt - 1))

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._request(method, path, **kwargs)
        return resp.json() if resp.content else {}

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page: dict[str, Any] = self._json("GET", path, params=params)
        while True:
            yield from page.get("data", [])
            nxt = (page.get("links") or {}).get("next") or {}
            href = nxt.get("href") if isinstance(nxt, dict) else None
            if not href:
                return
            page = self._json("GET", href)

    # ------------------------------------------------------------------ Apps
    def list_apps(self, name: str | None = None) -> list[AppInfo]:
        """Apps visibles par la clé. GET /api/v1/items?resourceType=app."""
        params: dict[str, Any] = {"resourceType": "app", "limit": 100}
        if name:
            params["name"] = name
        return [
            AppInfo(app_id=str(i["resourceId"]), name=str(i.get("name", "")), space_id=i.get("spaceId"))
            for i in self._paginate("/api/v1/items", params)
        ]

    def find_app_id(self, name: str) -> str | None:
        """Identifiant de l'app au nom EXACT ; None si absente ; erreur si ambigu."""
        matches = [a for a in self.list_apps(name=name) if a.name == name]
        if self.settings.space_id:
            matches = [a for a in matches if a.space_id == self.settings.space_id]
        if len(matches) > 1:
            raise AppResolutionError(f"{len(matches)} apps nommées « {name} » : nom ambigu")
        return matches[0].app_id if matches else None

    def resolve_app_id(self, name: str) -> str:
        app_id = self.find_app_id(name)
        if not app_id:
            raise AppResolutionError(f"App « {name} » introuvable : lancez d'abord le déploiement (CD)")
        return app_id

    def create_app(self, name: str, description: str = "") -> str:
        attributes: dict[str, Any] = {"name": name, "description": description}
        if self.settings.space_id:
            attributes["spaceId"] = self.settings.space_id
        data = self._json("POST", "/api/v1/apps", json={"attributes": attributes})
        return str(data["attributes"]["id"])

    def set_script(self, app_id: str, script: str, version_message: str) -> None:
        """Pousse une nouvelle version du script (historisée dans Qlik Cloud)."""
        self._request(
            "POST",
            f"/api/v1/apps/{app_id}/scripts",
            json={"script": script, "versionMessage": version_message[:256]},
        )

    # --------------------------------------------------------------- Reloads
    def trigger_reload(self, app_id: str, variables: dict[str, str] | None = None) -> str:
        body: dict[str, Any] = {"appId": app_id, "partial": False}
        if variables:
            body["variables"] = variables
        data = self._json("POST", "/api/v1/reloads", json=body)
        return str(data["id"])

    def get_status(self, reload_id: str) -> ReloadRun:
        return ReloadRun.from_api(self._json("GET", f"/api/v1/reloads/{reload_id}"))

    def list_reloads(self, app_id: str, limit: int = 100) -> list[ReloadRun]:
        params = {"appId": app_id, "limit": min(limit, 100)}
        out: list[ReloadRun] = []
        for item in self._paginate("/api/v1/reloads", params):
            out.append(ReloadRun.from_api(item))
            if len(out) >= limit:
                break
        return out

    def get_script_log(self, app_id: str, reload_id: str) -> str:
        """Log complet du script (TRACE compris). Disponible une fois le reload terminé."""
        resp = self._request(
            "GET", f"/api/v1/apps/{app_id}/reloads/logs/{reload_id}", headers={"Accept": "text/plain"}
        )
        return resp.text

    def wait_for_reload(
        self,
        reload_id: str,
        timeout_s: float = 1800,
        interval_s: float = 10,
        raise_on_failure: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> ReloadRun:
        """Polling du statut jusqu'à un état final (hors Airflow : CLI, tests)."""
        deadline = clock() + timeout_s
        while True:
            run = self.get_status(reload_id)
            if run.status.is_final:
                if raise_on_failure and not run.status.is_success:
                    raise ReloadFailedError(
                        f"Reload {reload_id} : {run.status.value} — {run.error_message or 'sans message'}"
                    )
                return run
            if clock() >= deadline:
                raise ReloadTimeoutError(f"Reload {reload_id} toujours {run.status.value} après {timeout_s}s")
            self._sleep(interval_s)

    # ------------------------------------------------------------ Data files
    def find_data_file_id(self, name: str) -> str | None:
        params: dict[str, Any] = {"name": name, "limit": 100}
        for f in self._paginate("/api/v1/data-files", params):
            if f.get("name") == name:
                return str(f["id"])
        return None

    def upload_data_file(self, name: str, content: bytes) -> str:
        """Dépose un fichier dans DataFiles (espace personnel). Idempotent : remplace s'il existe."""
        meta = json.dumps({"name": name})
        files = {"File": (name, content, "text/csv"), "Json": (None, meta, "application/json")}
        existing = self.find_data_file_id(name)
        if existing:
            data = self._json("PUT", f"/api/v1/data-files/{existing}", files=files)
        else:
            data = self._json("POST", "/api/v1/data-files", files=files)
        return str(data.get("id", existing))

    def delete_data_file(self, name: str) -> bool:
        file_id = self.find_data_file_id(name)
        if not file_id:
            return False
        self._request("DELETE", f"/api/v1/data-files/{file_id}")
        return True


def _error_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors and isinstance(errors, list):
        return "; ".join(str(e.get("title") or e.get("detail") or e) for e in errors)[:300]
    return str(body)[:300]
