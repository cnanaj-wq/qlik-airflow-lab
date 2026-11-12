"""Alertes et historique des incidents, compatibles avec Pipeline Lens.

Même format que le lab Airflow existant (C:\\airflow\\dags\\product_etl_dag.py) :
- chaque événement est une ligne JSON ajoutée à logs/incidents/incidents.jsonl (jamais réécrit),
  que Lens lit pour son panneau « Incidents » ;
- les mails partent par la connexion Airflow « smtp_default » (Mailpit en local).

Ajout propre au lab Qlik : le mode « qualite », pour une chaîne techniquement verte mais aux
chiffres faux. C'est l'alerte la plus importante et celle qu'un ordonnanceur seul ne sait pas lever.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

INCIDENTS = Path(os.environ.get("INCIDENTS_FICHIER", "/opt/airflow/logs/incidents/incidents.jsonl"))


def _raison(context: dict[str, Any]) -> str:
    e = context.get("exception")
    return f"{type(e).__name__} : {e}"[:500] if e else "non transmise par Airflow"


def historiser(evenement: str, context: dict[str, Any], **extra: Any) -> dict[str, Any]:
    ti = context["task_instance"]
    ligne = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "evenement": evenement,
        "dag_id": ti.dag_id,
        "task_id": ti.task_id,
        "run_id": context["run_id"],
        "essai": getattr(ti, "try_number", None),
        "essais_max": (getattr(ti, "max_tries", None) or 0) + 1,
        **extra,
    }
    try:
        INCIDENTS.parent.mkdir(parents=True, exist_ok=True)
        with INCIDENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Historisation de l'incident impossible")
    return ligne


def incident_ouvert(dag_id: str, task_id: str, run_id: str) -> dict[str, Any] | None:
    debut: dict[str, Any] | None = None
    try:
        lignes = INCIDENTS.read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return None
    for brute in lignes:
        try:
            e = json.loads(brute)
        except ValueError:
            continue
        if (e.get("dag_id"), e.get("task_id"), e.get("run_id")) != (dag_id, task_id, run_id):
            continue
        if e.get("evenement") in ("echec_essai", "echec", "qualite"):
            debut = debut or e
        elif e.get("evenement") == "reprise":
            debut = None
    return debut


def _duree(secondes: float) -> str:
    m, s = divmod(round(secondes), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s" if m else f"{s} s"


def composer_alerte(
    context: dict[str, Any],
    lens_url: str | None = None,
    mode: str = "echec",
    duree: float | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Objet et corps HTML. mode : echec | essai | reprise | qualite."""
    ti = context["task_instance"]
    run_id = context["run_id"]
    t, d = escape(ti.task_id), escape(ti.dag_id)
    if mode == "essai":
        sujet = f"[Airflow] Incident en cours, nouvel essai : {ti.dag_id} / {ti.task_id}"
        intro = f"L'essai de <b>{t}</b> (DAG <b>{d}</b>) a échoué. Airflow réessaie automatiquement."
        conseil = "Vous recevrez un mail de reprise, ou d'échec si le dernier essai échoue aussi."
    elif mode == "reprise":
        sujet = f"[Airflow] Rétabli : {ti.dag_id} / {ti.task_id}"
        intro = f"<b>{t}</b> (DAG <b>{d}</b>) a réussi après un incident."
        conseil = "Incident clos. Historique dans Pipeline Lens, volet Historique."
    elif mode == "qualite":
        sujet = f"[Airflow] QUALITÉ — publication bloquée : {ti.dag_id}"
        intro = (
            "Tous les reloads Qlik sont <b>SUCCEEDED</b> : la chaîne technique est verte. "
            "Mais les chiffres ne correspondent pas à la source. "
            "<b>L'app de pilotage n'a pas été rechargée</b> : les utilisateurs gardent les chiffres "
            "de la veille, justes."
        )
        conseil = "Corriger la source, puis rejouer depuis l'extraction (DAG idempotent)."
    else:
        sujet = f"[Airflow] Échec : {ti.dag_id} / {ti.task_id}"
        intro = f"<b>{t}</b> (DAG <b>{d}</b>) a échoué."
        conseil = "Avant un rejeu, vérifiez la cause dans le journal de la tâche."

    essai, essais_max = getattr(ti, "try_number", None), getattr(ti, "max_tries", None)
    lignes = [
        ("DAG", ti.dag_id),
        ("Tâche", ti.task_id),
        ("Exécution", run_id),
        ("Essai", f"{essai} sur {essais_max + 1}" if essai and essais_max is not None else str(essai)),
    ]
    if mode == "reprise":
        lignes.append(("Durée de l'incident", _duree(duree or 0)))
    elif mode != "qualite":
        lignes.append(("Erreur", str(context.get("exception") or "non transmise")[:2000]))
    tableau = "".join(
        f'<tr><th align="left" style="padding:4px 12px 4px 0">{escape(k)}</th>'
        f'<td style="padding:4px 0">{escape(str(v))}</td></tr>'
        for k, v in lignes
    )
    controles = ""
    if checks:
        rows = "".join(
            f'<tr><td style="padding:3px 10px 3px 0">{escape(c["controle"])}</td>'
            f'<td style="padding:3px 10px;color:{"#b00020" if c["statut"] == "ko" else "#1b7f3b"}">'
            f"<b>{escape(c['statut'].upper())}</b></td>"
            f'<td style="padding:3px 10px">{escape(str(c["valeur"]))} (seuil {escape(str(c["seuil"]))})</td>'
            f'<td style="padding:3px 0;color:#555">{escape(c.get("detail", ""))}</td></tr>'
            for c in checks
        )
        controles = f'<h3 style="font-family:sans-serif">Contrôles</h3><table style="font-family:sans-serif;font-size:13px">{rows}</table>'
    lien = ""
    if lens_url:
        url = f"{lens_url.rstrip('/')}/?dag={quote(ti.dag_id)}&run={quote(run_id, safe='')}"
        lien = f'<p><a href="{escape(url)}">Ouvrir l’exécution dans Pipeline Lens</a></p>'
    corps = (
        f'<p>{intro}</p><table style="font-family:sans-serif;font-size:14px">{tableau}</table>'
        f'{controles}{lien}<p style="color:#666;font-size:12px">Alerte automatique — Qlik × Airflow Lab. '
        f"{conseil}</p>"
    )
    return sujet, corps


def _envoyer(
    context: dict[str, Any], mode: str, duree: float | None = None, checks: list[dict[str, Any]] | None = None
) -> None:
    destinataires = [a.strip() for a in os.environ.get("ALERTE_EMAIL_A", "").split(",") if a.strip()]
    if not destinataires:
        return
    try:
        from airflow.providers.smtp.hooks.smtp import SmtpHook

        sujet, corps = composer_alerte(context, os.environ.get("ALERTE_LENS_URL"), mode, duree, checks)
        with SmtpHook(smtp_conn_id="smtp_default") as smtp:
            smtp.send_email_smtp(to=destinataires, subject=sujet, html_content=corps)
        log.info("Alerte « %s » envoyée à %s", mode, ", ".join(destinataires))
    except Exception:  # l'alerte ne doit jamais faire échouer autre chose
        log.exception("Envoi du mail d'alerte impossible")


def alerte_echec(context: dict[str, Any]) -> None:
    exc = context.get("exception")
    checks = getattr(exc, "checks", None)
    if checks is not None:  # échec de la porte qualité : mail dédié
        historiser("qualite", context, raison=_raison(context))
        _envoyer(context, "qualite", checks=checks)
        return
    historiser("echec", context, raison=_raison(context))
    _envoyer(context, "echec")


def alerte_essai(context: dict[str, Any]) -> None:
    historiser("echec_essai", context, raison=_raison(context))
    if os.environ.get("ALERTE_EMAIL_ESSAIS", "on").lower() != "off":
        _envoyer(context, "essai")


def signaler_reprise(context: dict[str, Any]) -> None:
    ti = context["task_instance"]
    debut = incident_ouvert(ti.dag_id, ti.task_id, context["run_id"])
    if not debut:
        return
    duree = (datetime.now(UTC) - datetime.fromisoformat(debut["ts"])).total_seconds()
    historiser("reprise", context, duree_s=round(duree), debut=debut["ts"])
    _envoyer(context, "reprise", duree)
