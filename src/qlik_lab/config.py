"""Lecture et validation de la configuration. La clé API n'est jamais affichée."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from qlik_lab.errors import QlikConfigError


def load_dotenv(path: Path | str = ".env") -> None:
    """Charge un fichier .env minimal (CLE=valeur) sans écraser l'environnement existant."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def normalize_tenant_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise QlikConfigError("QLIK_TENANT_URL doit être de la forme https://<alias>.<région>.qlikcloud.com")
    if parsed.path not in ("", "/"):
        raise QlikConfigError("QLIK_TENANT_URL ne doit pas contenir de chemin (pas de /api/...)")
    return f"https://{parsed.netloc}"


@dataclass(frozen=True)
class QlikSettings:
    tenant_url: str
    # repr=False : la clé n'apparaît jamais dans un log ou une trace d'erreur.
    api_key: str = field(repr=False)
    timeout_s: float = 30.0
    space_id: str | None = None
    app_prefix: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_url", normalize_tenant_url(self.tenant_url))
        if not self.api_key:
            raise QlikConfigError("Clé API absente (QLIK_API_KEY ou connexion Airflow qlik_cloud)")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> QlikSettings:
        env = os.environ if env is None else env
        url = env.get("QLIK_TENANT_URL", "")
        if not url:
            raise QlikConfigError("QLIK_TENANT_URL absente : copiez .env.example en .env")
        return cls(
            tenant_url=url,
            api_key=env.get("QLIK_API_KEY", ""),
            timeout_s=float(env.get("QLIK_TIMEOUT_S", "30")),
            space_id=env.get("QLIK_SPACE_ID") or None,
            app_prefix=env.get("QLIK_APP_PREFIX", ""),
        )
