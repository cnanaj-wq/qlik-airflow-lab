"""Contrat commun à tous les « adaptateurs » de reload.

Le DAG ne dépend que de ce contrat, implémenté ici pour Qlik Cloud. C'est la logique d'un
ordonnanceur d'entreprise (Control-M, OpCon) : déclencher, interroger le statut, rendre un
code retour.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qlik_lab.models import ReloadRun


@runtime_checkable
class QlikReloadClient(Protocol):
    def trigger_reload(self, app_id: str, variables: dict[str, str] | None = None) -> str:
        """Déclenche un reload et renvoie son identifiant."""
        ...

    def get_status(self, reload_id: str) -> ReloadRun:
        """Renvoie l'état courant du reload."""
        ...
