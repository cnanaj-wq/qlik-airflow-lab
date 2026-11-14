"""Porte qualité (« quality gate ») entre la transformation et la publication.

Le script Qlik écrit ses indicateurs dans son log avec TRACE, sur une ligne au format :

    DQ|app=02_TransformDataSales|run_id=...|rows=18234|distinct_keys=18234|revenue=4523110.25|...

Airflow lit le log du reload, extrait ces valeurs et les compare aux totaux annoncés par la source.
Un reload « SUCCEEDED » prouve que le script a tourné, pas que les chiffres sont justes.

Le format des contrôles (controle, valeur, seuil, statut) est celui que lit Pipeline Lens
(XCom « quality_checks »).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass
from typing import Any

MARKER = re.compile(r"DQ\|([^\r\n]+)")


def parse_dq_markers(log_text: str, app: str | None = None) -> dict[str, str]:
    """Renvoie le DERNIER marqueur DQ complet du log (variables développées).

    Le log Qlik peut contenir l'instruction (« TRACE DQ|... ») puis sa sortie : on ignore les lignes
    contenant encore « $( » (variable non développée) et on garde la dernière occurrence valide.
    """
    found: dict[str, str] = {}
    for m in MARKER.finditer(log_text):
        payload = m.group(1).strip().rstrip(";").strip()
        if "$(" in payload:
            continue
        pairs = dict(p.split("=", 1) for p in payload.split("|") if "=" in p)
        if app and pairs.get("app") != app:
            continue
        found = {k.strip(): v.strip() for k, v in pairs.items()}
    return found


@dataclass
class Check:
    controle: str
    valeur: float
    seuil: float
    bloquant: bool = True
    detail: str = ""

    @property
    def statut(self) -> str:
        return "ok" if self.valeur <= self.seuil else "ko"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "statut": self.statut}


def _pct(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else 100.0
    return round(abs(actual - expected) / expected * 100, 2)


def evaluate(
    metrics: dict[str, str],
    control: dict[str, Any],
    history_revenue_last_day: list[float] | None = None,
    revenue_tolerance_pct: float = 0.5,
    drift_tolerance_pct: float = 35.0,
) -> list[Check]:
    """Contrôles bloquants (doublons, rapprochement) + dérive (non bloquante, alerte)."""
    rows = int(float(metrics["rows"]))
    distinct = int(float(metrics["distinct_keys"]))
    revenue = float(metrics["revenue"])
    revenue_day = float(metrics.get("revenue_last_day", "0"))

    checks = [
        Check(
            "Lignes en double (OrderLineID)",
            rows - distinct,
            0,
            detail=f"{rows} lignes pour {distinct} identifiants distincts",
        ),
        Check(
            "Écart de lignes vs source",
            abs(rows - int(control["rows"])),
            0,
            detail=f"Qlik {rows} / source {control['rows']}",
        ),
        Check(
            "Écart de CA vs source (%)",
            _pct(revenue, float(control["revenue"])),
            revenue_tolerance_pct,
            detail=f"Qlik {revenue:,.2f} € / source {float(control['revenue']):,.2f} €",
        ),
        Check(
            "Écart de CA du jour vs source (%)",
            _pct(revenue_day, float(control["revenue_last_day"])),
            revenue_tolerance_pct,
            detail=f"Qlik {revenue_day:,.2f} € / source {float(control['revenue_last_day']):,.2f} €",
        ),
    ]
    hist = [h for h in (history_revenue_last_day or []) if h > 0]
    if len(hist) >= 3:
        median = statistics.median(hist)
        checks.append(
            Check(
                "Dérive du CA du jour vs médiane (%)",
                _pct(revenue_day, median),
                drift_tolerance_pct,
                bloquant=False,
                detail=f"CA du jour {revenue_day:,.2f} € / médiane {median:,.2f} € ({len(hist)} runs)",
            )
        )
    return checks


def blocking_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.bloquant and c.statut == "ko"]
