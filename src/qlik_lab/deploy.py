"""Déploiement continu des scripts Qlik (« scripts as code »).

Le dépôt Git est la source de vérité : chaque app est décrite dans qlik/apps.toml et son script
dans qlik/apps/<nom>/script.qvs. Le déploiement est IDEMPOTENT :
- l'app est retrouvée par son nom exact, créée seulement si elle manque ;
- le script poussé porte en tête la version Git (vScriptVersion), visible dans Qlik et dans le log ;
- l'ordre du manifeste est respecté (01 → 02 → 03), car 02 lit les QVD de 01, etc.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from qlik_lab.clients.qlik_cloud import QlikCloudClient

VERSION_HEADER = "// --- Injecté par le déploiement (ne pas modifier dans Qlik) ---\n"


@dataclass(frozen=True)
class AppSpec:
    key: str
    name: str
    description: str
    script_path: Path

    def full_name(self, prefix: str = "") -> str:
        return f"{prefix}{self.name}"


def load_manifest(path: Path | str) -> list[AppSpec]:
    p = Path(path)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    specs = []
    for app in data["apps"]:
        script = (p.parent / app["script"]).resolve()
        if not script.is_file():
            raise FileNotFoundError(f"Script introuvable : {script}")
        specs.append(
            AppSpec(
                key=app["key"], name=app["name"], description=app.get("description", ""), script_path=script
            )
        )
    return specs


def render_script(spec: AppSpec, version: str) -> str:
    body = spec.script_path.read_text(encoding="utf-8")
    header = f"{VERSION_HEADER}SET vScriptVersion = '{version}';\n\n"
    return header + body


@dataclass(frozen=True)
class DeployResult:
    name: str
    app_id: str | None
    created: bool
    pushed: bool


def deploy(
    client: QlikCloudClient, specs: list[AppSpec], version: str, dry_run: bool = False
) -> list[DeployResult]:
    prefix = client.settings.app_prefix
    results = []
    for spec in specs:
        name = spec.full_name(prefix)
        app_id = client.find_app_id(name)
        created = False
        if dry_run:
            results.append(DeployResult(name, app_id, created=app_id is None, pushed=True))
            continue
        if app_id is None:
            app_id = client.create_app(name, spec.description)
            created = True
        client.set_script(app_id, render_script(spec, version), f"deploy {version}")
        results.append(DeployResult(name, app_id, created, pushed=True))
    return results
