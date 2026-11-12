"""Extraction ERP SYNTHÉTIQUE (aucune donnée réelle).

Chaque exécution produit un instantané « 90 jours glissants » des lignes de commande, comme
l'extraction complète quotidienne d'un ERP. Les données sont DÉTERMINISTES : une date de gestion
donnée produit toujours le même fichier. Rejouer le DAG redonne donc exactement le même résultat
(idempotence), condition pour qu'un rejeu soit sans risque.

Le scénario « duplicate » simule un incident classique d'interface : le dernier lot du jour est
livré deux fois (pagination rejouée, fichier concaténé). Rien ne plante, mais le CA grossit.
Les totaux de contrôle (« ce que l'ERP annonce ») sont toujours calculés sur le lot SAIN :
c'est la référence de rapprochement.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta

DAYS = 90
CHANNELS = ("Magasin", "Web", "Marketplace", "B2B")
REGIONS = (
    "Île-de-France",
    "Auvergne-Rhône-Alpes",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Hauts-de-France",
    "Grand Est",
    "Provence-Alpes-Côte d'Azur",
    "Bretagne",
)
CATEGORIES = {
    "Vélos": (350.0, 2400.0),
    "Accessoires": (8.0, 120.0),
    "Vêtements": (15.0, 180.0),
    "Pièces": (5.0, 260.0),
}

ORDER_FIELDS = [
    "OrderLineID",
    "OrderID",
    "OrderDate",
    "CustomerID",
    "ProductID",
    "Quantity",
    "UnitPrice",
    "Discount",
    "Channel",
]


@dataclass(frozen=True)
class ControlTotals:
    """Totaux annoncés par la source (équivalent d'un enregistrement de fin de fichier)."""

    business_date: str
    rows: int
    orders: int
    revenue: float
    rows_last_day: int
    revenue_last_day: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SalesExtract:
    orders_csv: bytes
    customers_csv: bytes
    products_csv: bytes
    control: ControlTotals
    rows_written: int
    duplicated_rows: int


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))  # noqa: S311 — données synthétiques, pas de cryptographie


def _to_csv(fields: list[str], rows: list[dict[str, object]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_customers(n: int = 400) -> list[dict[str, object]]:
    r = _rng("customers")
    return [
        {
            "CustomerID": f"C{i:05d}",
            "CustomerName": f"Client {i:05d}",
            "Region": r.choice(REGIONS),
            "Segment": r.choices(["Particulier", "Professionnel"], [0.8, 0.2])[0],
        }
        for i in range(1, n + 1)
    ]


def build_products() -> list[dict[str, object]]:
    r = _rng("products")
    rows: list[dict[str, object]] = []
    pid = 0
    for cat, (lo, hi) in CATEGORIES.items():
        for k in range(1, 16):
            pid += 1
            price = round(r.uniform(lo, hi), 2)
            rows.append(
                {
                    "ProductID": f"P{pid:04d}",
                    "ProductName": f"{cat} {k:02d}",
                    "Category": cat,
                    "ListPrice": f"{price:.2f}",
                    "UnitCost": f"{price * r.uniform(0.45, 0.7):.2f}",
                }
            )
    return rows


def _day_lines(
    day: date, customers: list[dict[str, object]], products: list[dict[str, object]]
) -> list[dict[str, object]]:
    r = _rng("orders", day.isoformat())
    weekend = day.weekday() >= 5
    trend = 1 + (day.toordinal() % 365) / 900  # légère croissance
    n_orders = int(r.randint(55, 80) * (1.35 if weekend else 1.0) * trend)
    lines: list[dict[str, object]] = []
    seq = 0
    for o in range(1, n_orders + 1):
        order_id = f"O{day:%Y%m%d}-{o:04d}"
        cust = r.choice(customers)
        channel = r.choices(CHANNELS, [0.45, 0.35, 0.12, 0.08])[0]
        for _ in range(r.randint(1, 4)):
            seq += 1
            prod = r.choice(products)
            lines.append(
                {
                    "OrderLineID": f"L{day:%Y%m%d}-{seq:05d}",
                    "OrderID": order_id,
                    "OrderDate": day.isoformat(),
                    "CustomerID": cust["CustomerID"],
                    "ProductID": prod["ProductID"],
                    "Quantity": r.choices([1, 2, 3, 5], [0.7, 0.18, 0.08, 0.04])[0],
                    "UnitPrice": prod["ListPrice"],
                    "Discount": r.choices(["0.00", "0.05", "0.10", "0.20"], [0.7, 0.15, 0.1, 0.05])[0],
                    "Channel": channel,
                }
            )
    return lines


def line_revenue(line: dict[str, object]) -> float:
    """Même règle que l'app 02 : Quantity × UnitPrice × (1 − Discount), arrondi au centime."""
    return round(
        int(str(line["Quantity"])) * float(str(line["UnitPrice"])) * (1 - float(str(line["Discount"]))), 2
    )


def generate_extract(
    business_date: date, scenario: str = "clean", duplicate_ratio: float = 0.5
) -> SalesExtract:
    if scenario not in ("clean", "duplicate"):
        raise ValueError(f"Scénario inconnu : {scenario}")
    customers, products = build_customers(), build_products()
    lines: list[dict[str, object]] = []
    for offset in range(DAYS - 1, -1, -1):
        lines.extend(_day_lines(business_date - timedelta(days=offset), customers, products))

    last_day = business_date.isoformat()
    day_lines = [ln for ln in lines if ln["OrderDate"] == last_day]
    control = ControlTotals(
        business_date=last_day,
        rows=len(lines),
        orders=len({ln["OrderID"] for ln in lines}),
        revenue=round(sum(line_revenue(ln) for ln in lines), 2),
        rows_last_day=len(day_lines),
        revenue_last_day=round(sum(line_revenue(ln) for ln in day_lines), 2),
    )

    duplicated: list[dict[str, object]] = []
    if scenario == "duplicate":
        # Le dernier lot du jour est rejoué : mêmes lignes, mêmes identifiants.
        duplicated = day_lines[-max(1, int(len(day_lines) * duplicate_ratio)) :]
    written = lines + duplicated

    return SalesExtract(
        orders_csv=_to_csv(ORDER_FIELDS, written),
        customers_csv=_to_csv(list(customers[0].keys()), customers),
        products_csv=_to_csv(list(products[0].keys()), products),
        control=control,
        rows_written=len(written),
        duplicated_rows=len(duplicated),
    )
