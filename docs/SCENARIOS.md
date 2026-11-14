# Scénarios pas à pas — à copier-coller

Une étape à la fois : lancez, regardez le résultat, puis passez à la suivante.
Fenêtres à garder ouvertes :

| Où regarder | Adresse |
|---|---|
| Airflow (déclencher, journaux) | http://localhost:8085 |
| Pipeline Lens (vue métier, incidents, contrôles) | http://localhost:5173 |
| Mailpit (les mails d'alerte) | http://localhost:8025 |
| Qlik Cloud (historique des reloads de chaque app) | votre tenant |

---

## 0. Mise en place

**0.1 — Environnement Python du dépôt** (PowerShell, dans `C:\qlik-airflow-lab`)

```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"
```

**0.2 — Secrets.** Copiez le modèle, puis ouvrez `.env` **vous-même** pour y coller l'URL et la clé.

```powershell
Copy-Item .env.example .env; notepad .env
```

**0.3 — Vérifier la clé** (liste vos apps ; aucune clé n'est affichée)

```powershell
.\.venv\Scripts\python -m qlik_lab list-apps
```

**0.4 — Créer les 3 apps et y pousser les scripts** (déploiement lancé depuis le poste)

```powershell
.\.venv\Scripts\python -m qlik_lab deploy --dry-run
.\.venv\Scripts\python -m qlik_lab deploy
```

**0.5 — Greffer le lab sur votre Airflow** (vérifier, puis démarrer)

```powershell
.\scripts\lab-up.ps1 -Config
.\scripts\lab-up.ps1
```

Dans Airflow, les DAG `qlik_sales_pipeline` et `qlik_supervision_dag` apparaissent en 30 s environ
(tag `qlik`).

---

## 1. Chaîne nominale : « CA propre »

Airflow → `qlik_sales_pipeline` → **Trigger** → configuration :

```json
{"scenario": "clean"}
```

Attendu : 9 tâches vertes. Dans Qlik, chaque app a un reload **SUCCEEDED** déclenché par l'API.
Dans Lens, l'étape Transform affiche les **contrôles qualité**, tous OK.

**Construire un historique** (pour le contrôle de dérive, il faut au moins 3 runs verts) : relancez
trois fois avec une date de gestion passée.

```json
{"scenario": "clean", "business_date": "2026-11-09"}
```

Puis `2026-11-10` et `2026-11-11`.

---

## 2. « La chaîne est verte, mais le CA est faux » : doublons

```json
{"scenario": "duplicate"}
```

Ce qui se passe :

1. l'extraction livre deux fois la moitié du dernier lot du jour (même `OrderLineID`) ;
2. les reloads **01 et 02 réussissent** : statut **SUCCEEDED** dans Qlik ;
3. `quality_gate` lit le log du reload 02 et compare aux totaux de la source :
   lignes en double > 0, CA du jour environ +50 % → **rouge** ;
4. **03_Sales_Operations n'est pas rechargée** : les utilisateurs gardent les chiffres d'hier,
   qui sont justes ;
5. mail **« [Airflow] QUALITÉ — publication bloquée »** dans Mailpit, avec le tableau des contrôles.

À montrer : la colonne « Publié le » de l'app 03 n'a pas bougé, alors que 02 contient bien les
doublons (`Count(OrderLineID)` contre `Count(DISTINCT OrderLineID)`).

**Retour à la normale** : relancez `{"scenario": "clean"}`. Le DAG étant idempotent, tout se
recalcule proprement.

---

## 3. Reprise automatique : panne passagère

```json
{"scenario": "clean", "transient_failures": 1}
```

Le premier essai de `extract.upload_to_qlik` échoue (panne **simulée**). Airflow réessaie
environ 1 min plus tard (attente croissante) et réussit. Dans Mailpit : « Incident en cours, nouvel essai » puis « Rétabli »,
avec la durée de l'incident. Dans Lens, panneau Incidents : « repris sans intervention ».

---

## 4. Reprise sur incident, avec intervention : le fichier source a changé de nom

**4.1 — Provoquer l'incident** (incident simulé côté source, mais l'échec dans Qlik est bien réel)

```powershell
docker exec airflow-airflow-scheduler-1 airflow variables set qlik_lab_fault missing_file
```

Déclenchez `{"scenario": "clean"}`. Le dépôt envoie `qal_sales_orders_v2.csv` et retire
l'ancien fichier. Le reload 01 échoue **dans Qlik** (« Cannot open file »).
`transform.wait_ingestion` passe au rouge **sans nouvel essai**, car c'est une erreur de script :
réessayer ne la corrigerait pas. Mail **Échec**, avec l'extrait du log Qlik.

**4.2 — Diagnostiquer.** Journal de la tâche dans Airflow, ou historique des reloads de l'app 01
dans Qlik. Ensuite, côté supervision :

```powershell
docker exec airflow-qlik-supervision-db-1 psql -U qlik -d qlik_supervision -c "SELECT * FROM v_reload_failures LIMIT 5;"
```

**4.3 — Corriger la cause** (ici, retirer l'incident simulé)

```powershell
docker exec airflow-airflow-scheduler-1 airflow variables delete qlik_lab_fault
```

**4.4 — Reprendre au bon endroit.** Inutile de tout relancer, mais il faut repartir de l'étape qui
a produit le mauvais état : **le dépôt**, pas le reload. Dans Airflow, sur l'exécution en échec :
tâche `extract.upload_to_qlik` → **Clear** → cochez **Downstream** → confirmer.
(Ou dans Lens : **Rejouer** → une tâche et ses dépendantes → simuler → confirmer.)

Attendu : dépôt, puis 01, 02, porte qualité et 03, tous verts. Mail **« Rétabli »**, et l'incident
apparaît clos dans Lens, avec sa durée.

> L'équivalent Control-M : corriger, puis *Rerun* du job en échec avec ses successeurs.
> La différence : Airflow garde le même `run_id`, donc l'historique de l'incident reste attaché à
> l'exécution.

---

## 5. Historique des échecs

```powershell
docker exec airflow-qlik-supervision-db-1 psql -U qlik -d qlik_supervision -c "SELECT app_name, status, count(*) FROM reload_runs GROUP BY 1,2 ORDER BY 1,2;"
docker exec airflow-qlik-supervision-db-1 psql -U qlik -d qlik_supervision -c "SELECT business_date, check_name, value, status FROM dq_results WHERE status='ko' ORDER BY recorded_at DESC LIMIT 10;"
```

Le DAG `qlik_supervision_dag` (7 h chaque jour) rattrape aussi les reloads lancés hors Airflow,
depuis le hub par exemple.
