# Apprendre Airflow avec ce lab — pour un consultant Qlik

Chaque notion renvoie au code qui l'utilise. Lisez une section, ouvrez le fichier,
puis faites l'exercice.

## 1. Le vocabulaire, traduit

| Airflow | Control-M / OpCon | Qlik | Dans ce lab |
|---|---|---|---|
| DAG | Flux de jobs (folder) | Chaîne de tâches QMC | `qlik_sales_pipeline` |
| Task | Job | Tâche de reload | `trigger_ingestion` |
| Sensor | Condition / Wait for | (aucun équivalent) | `wait_ingestion` |
| Schedule (cron) | Calendrier | Déclencheur planifié | `0 6 * * *` |
| Retries + backoff | Rerun automatique | Nombre de tentatives | `DEFAULT_ARGS` |
| Callback | Action On/Do | Notification de tâche | `alerte_echec` |
| Connection | Compte de connexion | Connexion de données | `qlik_cloud` |
| Variable | Variable globale | Variable | `qlik_lab_fault` |
| Params (conf) | Paramètres de commande | Variables de reload | `scenario` |
| XCom | Variables de job transmises | (aucun équivalent) | `row_count`, `quality_checks` |
| Clear (+ downstream) | Rerun depuis un job | Relancer la chaîne | scénario 4 |
| Task group | Sous-folder | — | `extract` / `transform` / `load` |

## 2. Les composants d'Airflow 3

- **api-server** : interface web et API REST, sur le port 8085 de l'Airflow du poste (8090 avec le
  `docker-compose.yml` autonome). C'est elle que lit Pipeline Lens.
- **scheduler** : décide quoi lancer et quand. Avec LocalExecutor, il exécute aussi les tâches.
- **dag-processor** : relit les fichiers `dags/` toutes les 30 s. D'où la règle d'or : **aucun
  appel réseau au niveau du module**. Un `list_apps()` en haut du fichier interrogerait Qlik
  2 880 fois par jour. Le test `test_parsing_is_fast` le vérifie.
- **Postgres** : base de métadonnées (états, XCom, historique). Ne pas y stocker vos données.

## 3. Les bonnes pratiques appliquées

1. **Idempotence.** Un rejeu produit le même résultat : extraction déterministe, dépôt qui
   *remplace* le fichier, reloads complets. C'est la condition pour cliquer sans risque sur
   « Clear ». Le tag `rejouable` et `doc_md` le déclarent (règle de Pipeline Lens).
2. **Déclencher puis attendre, en deux tâches.** Si l'attente échoue, on ne relance pas un
   second reload. Le capteur est en `mode="reschedule"` : entre deux interrogations, il libère
   son slot (en mode `poke`, il le monopoliserait pendant 30 min).
3. **Réessayer seulement ce qui peut guérir.** Un 429 ou un 503 se réessaie, une erreur de script
   non : `AirflowFailException` coupe les retries. Côté client, un POST n'est pas rejoué sur
   un 5xx, pour éviter un double reload.
4. **Secrets hors du code.** La clé devient une connexion Airflow par variable d'environnement
   (`AIRFLOW_CONN_QLIK_CLOUD`) : rien dans la base, rien dans Git, `repr=False` dans les
   réglages. La clé expire le 12/12/2026 : créez la nouvelle, remplacez-la dans `.env`, puis
   `.\scripts\lab-up.ps1`.
5. **Petits XCom.** On transmet des identifiants et des totaux, jamais les fichiers.
6. **`max_active_runs=1`.** Deux exécutions simultanées écriraient les mêmes fichiers et
   rechargeraient les mêmes apps.
7. **Résoudre les apps par nom, pas par ID.** Les ID changent d'un tenant à l'autre (dev / prod) ;
   le nom, lui, est versionné dans `qlik/apps.toml`.
8. **La supervision ne casse jamais la production.** Une base indisponible produit un
   avertissement, pas un échec.
9. **Un DAG n'importe jamais un autre fichier de DAG.** Le code commun vit dans
   `src/qlik_lab/airflow_helpers.py`.
10. **Tester les DAG dans la vraie image** (`tests/dags`). La version d'Airflow est la même qu'en
    exécution : la 3.4 a changé `DagBag` et l'import d'`AirflowFailException`, et seule l'image
    réelle l'a révélé.

## 4. Le contrat Qlik ↔ Airflow

| Sens | Moyen | Exemple |
|---|---|---|
| Airflow → Qlik | `variables` du `POST /api/v1/reloads` | `vRunId` = `run_id` Airflow, affiché dans l'app 03 |
| Qlik → Airflow | statut du reload | `SUCCEEDED` / `FAILED` |
| Qlik → Airflow | `TRACE DQ\|clé=valeur…` dans le log du script | lignes, clés distinctes, CA |
| Git → Qlik | `POST /api/v1/apps/{id}/scripts` | `vScriptVersion` = commit |

Piège évité : les nombres du marqueur DQ sont écrits avec `Num(x, '0.00', '.', '')`. En
paramètres régionaux français, un `1 234,56` serait illisible pour Python.

## 5. Exercices

1. Lancez le scénario 1 et retrouvez votre `run_id` dans le bandeau de l'app 03.
2. Passez `poke_interval` de 15 à 60 s : combien de lignes dans le journal de `wait_transform` ?
3. Ajoutez un contrôle « CA négatif » dans `dq.evaluate` et son test dans `tests/test_dq.py`.
4. Créez un **pool** `qlik_reloads` de 2 slots (Admin → Pools) et ajoutez `pool="qlik_reloads"`
   aux tâches `trigger_*` : c'est ainsi qu'on protège un tenant qui tolère peu de reloads
   simultanés.
5. Ajoutez une SLA de fraîcheur : si `wait_operations` n'est pas fini à 7 h, envoyer un mail
   (indice : `Deadline Alerts` d'Airflow 3).
6. Lisez `docs/CICD.md`, cassez volontairement une règle des scripts Qlik, lancez `pytest -q` et
   observez le test `tests/test_qlik_scripts.py` échouer.
