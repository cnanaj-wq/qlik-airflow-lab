# CI/CD — scripts Qlik « as code »

Même logique que [mon premier CI/CD](https://cnanaj-wq.github.io/mon-premier-ci-cd/cicd.html) :
**on vérifie, puis on publie**. Ici, publier veut dire pousser des scripts dans Qlik Cloud.

## 01 / CI — vérifier avant de publier

`.github/workflows/ci-cd.yml`, à chaque PR et à chaque push sur `main` :

| Job | Ce qu'il vérifie | Pourquoi |
|---|---|---|
| `tests` | `ruff`, `mypy`, `pytest` (client mocké, porte qualité, alertes) | Le code Python est juste |
| `tests` | `tests/test_qlik_scripts.py` | Les **règles maison des scripts Qlik** : pas de Section Access, chemins via `vLib`, `ErrorMode=1`, marqueur DQ présent, blocs `IF` fermés, pas d'`Exit Script` oublié |
| `dags` | Chargement des DAG dans `apache/airflow:3.4.0` | Même version qu'en exécution |
| `compose` | `docker compose config` | Pas de YAML cassé |

Il n'existe pas de linter officiel pour le script Qlik. Ces tests encodent des erreurs vues en
mission, et chaque nouvelle erreur rencontrée devient une règle.

## 02 / CD — publier seulement après les tests

Job `deploy` : `needs: [tests, dags, compose]` et `if:` push sur `main`. Il ne fait rien si
`qlik/` n'a pas changé.

1. retrouve chaque app **par son nom** et la crée si elle manque ;
2. pousse le script en ajoutant en tête `SET vScriptVersion = '<commit>';` : Qlik garde
   l'historique des versions, et l'app affiche le commit dont elle provient ;
3. en lancement manuel, `reload: true` recharge 01 → 02 → 03 (test de fumée), et `dry_run: true`
   affiche seulement le plan.

## Échecs et protection

- Une PR **ne déploie jamais** et n'a pas accès aux secrets.
- `concurrency: qlik-cloud-deploy` : jamais deux déploiements simultanés.
- Environnement GitHub `qlik-cloud` : c'est là que vivent les secrets, avec une approbation
  manuelle si vous l'activez.

## À configurer une fois sur GitHub (par vous)

1. **Settings → Environments → New environment** : `qlik-cloud`. Option : *Required reviewers*
   (vous-même).
2. Dans cet environnement, **Secrets** : `QLIK_TENANT_URL` et `QLIK_API_KEY`.
   **Variables** (option) : `QLIK_SPACE_ID`, `QLIK_APP_PREFIX`.
3. **Settings → Branches → Add rule** sur `main` : PR obligatoire, et vérifications `tests`,
   `dags` et `compose` requises.

## Ce que font les éditeurs (et pourquoi ce lab s'en approche)

- **Qlik** propose l'intégration Git native des apps, `qlik-cli` et l'API `apps/{id}/scripts` pour
  versionner et déployer. Ce lab utilise l'API directement, pour montrer le mécanisme sans outil
  supplémentaire.
- **Astronomer (Airflow)** recommande de tester les DAG dans l'image d'exécution, de ne faire
  aucun appel externe au parsing et de déployer le code des DAG séparément de l'infrastructure.
  Ce sont les jobs `dags` et `compose`.
- **dbt Labs** a popularisé le principe « write → audit → publish » : on écrit, on contrôle, puis
  on publie. La porte qualité en est la version Qlik.
