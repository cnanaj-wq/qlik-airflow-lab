"""Contrat commun à tous les « adaptateurs » de reload.

Qlik Cloud (réel) aujourd'hui, Qlik Sense Client-Managed via QRS (simulé) en phase 4 : le DAG
ne dépend que de ce contrat. C'est exactement la logique d'un ordonnanceur d'entreprise
(Control-M, OpCon) : déclencher, interroger le statut, rendre un code retour.
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
