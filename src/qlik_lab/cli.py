"""CLI du lab : python -m qlik_lab <commande>.

  list-apps                       apps visibles par la clé
  reload <app_id|nom>             déclenche un reload et attend son statut final
  history <app_id|nom>            derniers reloads d'une app
  deploy [--dry-run] [--reload]   pousse les scripts de qlik/apps.toml (CI/CD)
  extract --date AAAA-MM-JJ       génère l'extraction ERP synthétique en local (data/)

Codes retour (comme un job Control-M) : 0 succès, 1 échec du reload ou de l'API, 2 configuration.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

from qlik_lab.clients.qlik_cloud import QlikCloudClient
from qlik_lab.config import QlikSettings, load_dotenv
from qlik_lab.deploy import deploy, load_manifest
from qlik_lab.errors import QlikConfigError, QlikError
from qlik_lab.sales_data import generate_extract


def _client() -> QlikCloudClient:
    load_dotenv()
    return QlikCloudClient(QlikSettings.from_env())


def _resolve(client: QlikCloudClient, ref: str) -> str:
    # Un identifiant d'app Qlik Cloud est un UUID (36 caractères avec tirets).
    return ref if len(ref) == 36 and ref.count("-") == 4 else client.resolve_app_id(ref)


def _git_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip() or "local"
    except (OSError, subprocess.SubprocessError):
        return "local"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qlik_lab", description="Qlik × Airflow Lab")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-apps")
    p_reload = sub.add_parser("reload")
    p_reload.add_argument("app")
    p_reload.add_argument("--timeout", type=float, default=1800)
    p_hist = sub.add_parser("history")
    p_hist.add_argument("app")
    p_hist.add_argument("--limit", type=int, default=10)
    p_dep = sub.add_parser("deploy")
    p_dep.add_argument("--manifest", default="qlik/apps.toml")
    p_dep.add_argument("--version", default=None)
    p_dep.add_argument("--dry-run", action="store_true")
    p_dep.add_argument(
        "--reload", action="store_true", help="recharge la chaîne après déploiement (smoke test)"
    )
    p_ext = sub.add_parser("extract")
    p_ext.add_argument("--date", default=date.today().isoformat())
    p_ext.add_argument("--scenario", choices=["clean", "duplicate"], default="clean")
    p_ext.add_argument("--out", default="data")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        if args.cmd == "extract":
            ext = generate_extract(date.fromisoformat(args.date), args.scenario)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "qal_sales_orders.csv").write_bytes(ext.orders_csv)
            (out / "qal_customers.csv").write_bytes(ext.customers_csv)
            (out / "qal_products.csv").write_bytes(ext.products_csv)
            print(
                f"{ext.rows_written} lignes écrites ({ext.duplicated_rows} doublons) — contrôle : {ext.control}"
            )
            return 0

        client = _client()
        if args.cmd == "list-apps":
            for app in client.list_apps():
                print(f"{app.app_id}  {app.name}")
        elif args.cmd == "reload":
            app_id = _resolve(client, args.app)
            reload_id = client.trigger_reload(app_id, {"vRunId": "cli"})
            print(f"Reload {reload_id} lancé, attente du statut…")
            run = client.wait_for_reload(reload_id, timeout_s=args.timeout)
            print(f"{run.status.value} en {run.duration_s or 0:.0f} s")
        elif args.cmd == "history":
            for r in client.list_reloads(_resolve(client, args.app), limit=args.limit):
                print(f"{r.creation_time}  {r.status.value:<15} {r.type or '':<10} {r.error_message or ''}")
        elif args.cmd == "deploy":
            version = args.version or _git_version()
            specs = load_manifest(args.manifest)
            for res in deploy(client, specs, version, dry_run=args.dry_run):
                action = "créée" if res.created else "existante"
                print(f"{'[simulation] ' if args.dry_run else ''}{res.name} ({action}) -> script {version}")
            if args.reload and not args.dry_run:
                for spec in specs:
                    name = spec.full_name(client.settings.app_prefix)
                    reload_id = client.trigger_reload(
                        client.resolve_app_id(name), {"vRunId": f"cd-{version}"}
                    )
                    run = client.wait_for_reload(reload_id)
                    print(f"Reload {name} : {run.status.value}")
        return 0
    except QlikConfigError as e:
        print(f"Configuration : {e}", file=sys.stderr)
        return 2
    except QlikError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
