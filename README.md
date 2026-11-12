# Qlik × Airflow Lab

**Orchestrer les reloads Qlik Cloud comme le ferait un ordonnanceur d'entreprise, et refuser de
publier des chiffres faux, même quand tout est vert.**

Lab personnel d'un consultant BI senior (Qlik, plus de 15 ans) en route vers le Data Engineering.
Ce n'est pas une mission client : les données sont **100 % synthétiques**.

| Voir | Lien |
|---|---|
| Schéma interactif de la CI/CD | [cicd.html](https://cnanaj-wq.github.io/qlik-airflow-lab/cicd.html) · [source](docs/cicd.html) |
| Schéma interactif de la chaîne (nominal, vert mais faux, reprise) | [workflow.html](https://cnanaj-wq.github.io/qlik-airflow-lab/workflow.html) · [source](docs/workflow.html) |
| Plan de reprise sur incident (décideurs, astreinte) | [docs/PLAN-REPRISE.md](docs/PLAN-REPRISE.md) |

> **Réel / simulé**
> - **Réel** : tenant Qlik Cloud, API REST (reloads, data files, scripts), Airflow 3.4,
>   Postgres, mails d'alerte (boîte de test Mailpit), CI/CD GitHub Actions.
> - **Simulé** : l'ERP source (générateur déterministe), l'incident de doublons, la panne
>   passagère et le renommage de fichier. Ces incidents sont simulés, mais leurs effets dans
>   Qlik sont réels (reload FAILED, CA gonflé). La phase 4 (Qlik Sense on-premise via QRS)
>   utilisera une API **simulée**.

## Le problème

Chez un client Qlik, l'ordonnanceur (Control-M, OpCon, VTOM) sait dire si un reload a **planté**.
Il ne sait pas dire si le chiffre d'affaires publié est **juste**. Un lot livré deux fois donne
un reload `SUCCEEDED`, un tableau de bord frais… et un CA faux le lundi matin en comité.

## La chaîne en une image

```mermaid
flowchart LR
  A[ERP synthétique<br/>extraction + totaux de contrôle] --> B[Dépôt Qlik DataFiles]
  B --> C[01_IngestiondataSales<br/>brut, fidèle à la source]
  C --> D[02_TransformDataSales<br/>étoile + règle de CA<br/>TRACE DQ]
  D --> G{Porte qualité<br/>doublons, rapprochement,<br/>dérive}
  G -- vert --> E[03_Sales_Operations<br/>publiée]
  G -- rouge --> X[Publication bloquée<br/>mail QUALITÉ<br/>la veille reste affichée]
  C -. FAILED .-> R[Mail Échec<br/>reprise depuis l'étape fautive]

  classDef ok fill:#e3f5f1,stroke:#0f8a75,color:#073b33
  classDef gate fill:#eef2f6,stroke:#5b6b7f,color:#1f2a37
  classDef stop fill:#fde7ea,stroke:#b00020,color:#5c0010
  class B,C,D,E ok
  class G gate
  class X,R stop
```

En vocabulaire « médaillon » : 01 = bronze, 02 = silver, 03 = gold. La porte qualité se place
**avant** le gold : c'est le principe *write → audit → publish*.

## Qui parle à qui : Airflow pilote, Qlik exécute

```mermaid
sequenceDiagram
    autonumber
    participant AF as Airflow (DAG 6 h)
    participant QC as Qlik Cloud (API REST)
    participant AP as Apps 01 / 02 / 03
    AF->>QC: Dépose les fichiers source (data-files)
    AF->>QC: POST /reloads (app 01)
    QC->>AP: Reload 01
    loop toutes les 15 s, sans bloquer de worker
        AF->>QC: GET /reloads/{id}
        QC-->>AF: QUEUED, RELOADING… puis SUCCEEDED
    end
    AF->>QC: POST /reloads (app 02), puis attente
    AF->>QC: GET log du reload 02
    QC-->>AF: TRACE DQ|rows=…|revenue=…
    Note over AF: Porte qualité : Qlik comparé aux totaux de la source
    alt chiffres conformes
        AF->>QC: POST /reloads (app 03) : publication
    else chiffres faux
        Note over AF,AP: 03 n'est pas rechargée. Mail QUALITÉ.
    end
```

## Ce que montre le lab (mesuré le 12 novembre 2026, données synthétiques)

| Scénario | Ce qui se passe | Résultat observé |
|---|---|---|
| **Nominal** | 01 → 02 → porte → 03 | 18 189 lignes de bout en bout, 4 contrôles conformes, environ 1 min |
| **Vert mais faux** | Un lot livré deux fois : 100 lignes en double | Tous les reloads `SUCCEEDED`. CA total **+0,69 %**, CA du jour **+53 %**. Porte rouge, 03 non publiée, mail QUALITÉ |
| **Reprise qualité** | Source corrigée, nouveau run | Porte verte, 03 republiée, incident clos en 5 min 26 s |
| **Erreur technique** | Reload 01 `FAILED` | Arrêt sans nouvel essai, mail Échec, reprise depuis l'étape fautive, mail Rétabli |

Le point clé : **l'écart sur le total détecte le problème ; l'écart sur le jour montre son
ampleur réelle et désigne le lot en cause.**

## Quand quelque chose casse

```mermaid
flowchart TD
    A([Alerte]) --> B{Type d'incident}
    B -- Panne passagère --> C[Airflow réessaie seul<br/>30 s puis 60 s]
    C --> OK([Mail Rétabli])
    B -- Erreur technique --> D[Corriger la cause<br/>rejouer le MÊME run<br/>depuis l'étape fautive]
    D --> OK
    B -- Chiffres faux --> E[Corriger la source<br/>lancer un NOUVEAU run]
    E --> OK2([Mail Rétabli<br/>publication reprise])

    classDef auto fill:#e3f5f1,stroke:#0f8a75,color:#073b33
    classDef human fill:#fff4e5,stroke:#b26a00,color:#4a2c00
    class C,OK,OK2 auto
    class D,E human
```

Détail, rôles et fiches réflexes : [docs/PLAN-REPRISE.md](docs/PLAN-REPRISE.md).

## La CI/CD des scripts Qlik

```mermaid
flowchart LR
    A[PR ou push] --> B{CI<br/>tests · dags · compose}
    B -- rouge --> X[Rien n'est publié]
    B -- vert, main --> C{Environnement<br/>qlik-cloud}
    C -- approuvé --> D[Scripts poussés<br/>par l'API Qlik]
    D --> E[3 apps à jour<br/>vScriptVersion = commit]
    B -- vert, main --> P[Schémas sur<br/>GitHub Pages]
    E -. régression .-> R[git revert<br/>le CD redéploie N-1]

    classDef ci fill:#eef2f6,stroke:#5b6b7f,color:#1f2a37
    classDef cd fill:#e3f5f1,stroke:#0f8a75,color:#073b33
    classDef stop fill:#fde7ea,stroke:#b00020,color:#5c0010
    class A,B ci
    class C,D,E,P cd
    class X,R stop
```

Explication pour décideurs et réglages GitHub : [docs/CICD.md](docs/CICD.md).

## Avancement

| Phase | Contenu | Statut |
|---|---|---|
| 0 — Socle | Structure, pytest / ruff / mypy, CI | ✅ |
| 1 — Client Python | Apps, reloads, polling, **aucune redirection suivie** (piège du 301), retries 429/5xx | ✅ testé en réel |
| 2 — DAG Airflow | Déclencher → capteur reschedule → porte qualité → publication ; retries ; alertes mail | ✅ testé en réel |
| 3 — Supervision | Tables `reload_runs`, `dq_results`, vue des échecs, panneau Incidents (Pipeline Lens) | ✅ base ; dbt et app de supervision à venir |
| CI/CD | Lint des scripts Qlik, DAG, déploiement vers Qlik Cloud, schémas sur GitHub Pages | ✅ écrit ; à activer sur GitHub |
| 4 — On-premise (simulé) | Mock QRS, même DAG | 🔜 |
| 5 — V2 | QVD temporaires promus si la porte est verte, alerte de fraîcheur, webhooks, OAuth M2M | 🔜 documenté |

## Démarrer

Tout est dans [docs/SCENARIOS.md](docs/SCENARIOS.md), étape par étape : installation, chaîne
nominale, doublons, panne passagère, reprise sur incident, historique des échecs.

| Pour… | Lire |
|---|---|
| Apprendre Airflow avec ce lab (vocabulaire Control-M, bonnes pratiques, exercices) | [docs/GUIDE-AIRFLOW.md](docs/GUIDE-AIRFLOW.md) |
| Comprendre la CI/CD des scripts Qlik | [docs/CICD.md](docs/CICD.md) |
| Savoir quoi faire en cas d'incident | [docs/PLAN-REPRISE.md](docs/PLAN-REPRISE.md) |
| Construire les feuilles de l'app 03 | [qlik/apps/03_Sales_Operations/FEUILLES.md](qlik/apps/03_Sales_Operations/FEUILLES.md) |

## Arborescence

```
src/qlik_lab/          client Qlik Cloud, porte qualité, alertes, supervision, déploiement, CLI
dags/                  qlik_sales_pipeline.py, qlik_supervision_dag.py
qlik/apps.toml         manifeste des 3 apps (l'ordre compte)
qlik/apps/*/script.qvs scripts Qlik versionnés
docker/compose.qlik.yaml   greffe sur un Airflow 3 existant (Mailpit, Pipeline Lens)
docker-compose.yml     Airflow 3 autonome pour un autre poste
docs/                  schémas interactifs (archify), CI/CD, plan de reprise, scénarios
tests/                 unitaires + tests/dags (dans l'image Airflow)
.github/workflows/     ci-cd.yml : tests → dags → compose → deploy, et pages
```

## Sécurité

La clé API Qlik ne figure ni dans Git, ni dans les logs, ni dans la base Airflow : elle est dans
`.env` (exclu de Git), puis dans une connexion Airflow par variable d'environnement, et dans un
secret d'environnement GitHub. Le nom du tenant n'apparaît pas dans le dépôt. Pas de Section
Access dans ce lab.
