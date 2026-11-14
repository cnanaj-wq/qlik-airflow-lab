from __future__ import annotations

from datetime import date

from qlik_lab.dq import blocking_failures, evaluate, parse_dq_markers
from qlik_lab.sales_data import generate_extract, line_revenue

LOG = """\
2026-11-12 06:01:02      0042  TRACE DQ|app=02_TransformDataSales|run_id=$(vRunId)|rows=$(vDQ_Rows)
2026-11-12 06:01:02      0043  TRACE DQ|app=02_TransformDataSales|run_id=r1|rows=100|distinct_keys=100|revenue=1000.00|revenue_last_day=50.00
2026-11-12 06:01:02      DQ|app=02_TransformDataSales|run_id=r1|rows=100|distinct_keys=100|revenue=1000.00|revenue_last_day=50.00
2026-11-12 06:01:03      DQ|app=03_Sales_Operations|run_id=r1|rows=100
"""


def test_parse_ignores_unexpanded_and_filters_app() -> None:
    m = parse_dq_markers(LOG, app="02_TransformDataSales")
    assert m["rows"] == "100" and m["revenue"] == "1000.00"
    assert parse_dq_markers(LOG, app="03_Sales_Operations")["rows"] == "100"
    assert parse_dq_markers("rien ici") == {}


def _metrics_from_csv(csv_bytes: bytes, last_day: str) -> dict[str, str]:
    """Rejoue en Python ce que calcule le script 02 (même règle de CA)."""
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
    day = [r for r in rows if r["OrderDate"] == last_day]
    return {
        "rows": str(len(rows)),
        "distinct_keys": str(len({r["OrderLineID"] for r in rows})),
        "revenue": f"{sum(line_revenue(r) for r in rows):.2f}",  # type: ignore[arg-type]
        "revenue_last_day": f"{sum(line_revenue(r) for r in day):.2f}",  # type: ignore[arg-type]
    }


def test_clean_extract_passes_the_gate() -> None:
    ext = generate_extract(date(2026, 11, 12), "clean")
    checks = evaluate(_metrics_from_csv(ext.orders_csv, "2026-11-12"), ext.control.to_dict())
    assert blocking_failures(checks) == []


def test_duplicate_extract_is_blocked_although_nothing_crashed() -> None:
    """Le cœur de la démo : rien ne plante, mais le CA grossit et la porte se ferme."""
    ext = generate_extract(date(2026, 11, 12), "duplicate")
    assert ext.duplicated_rows > 0
    metrics = _metrics_from_csv(ext.orders_csv, "2026-11-12")
    checks = {c.controle: c for c in evaluate(metrics, ext.control.to_dict())}
    assert checks["Lignes en double (OrderLineID)"].valeur == ext.duplicated_rows
    assert checks["Écart de CA du jour vs source (%)"].statut == "ko"
    assert blocking_failures(list(checks.values()))


def test_drift_check_is_not_blocking() -> None:
    ext = generate_extract(date(2026, 11, 12), "clean")
    metrics = _metrics_from_csv(ext.orders_csv, "2026-11-12")
    checks = evaluate(metrics, ext.control.to_dict(), history_revenue_last_day=[1.0, 1.0, 1.0])
    drift = [c for c in checks if c.controle.startswith("Dérive")]
    assert drift and drift[0].statut == "ko" and not drift[0].bloquant
    assert blocking_failures(checks) == []


def test_extract_is_deterministic() -> None:
    a = generate_extract(date(2026, 11, 12))
    b = generate_extract(date(2026, 11, 12))
    assert a.orders_csv == b.orders_csv and a.control == b.control
