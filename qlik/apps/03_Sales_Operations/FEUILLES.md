# 03_Sales_Operations — feuilles à construire (10 minutes)

Le script (modèle + expressions) est déployé par `python -m qlik_lab deploy`. Les feuilles se construisent dans
l'interface Qlik : l'API ne sait pas les créer simplement, et c'est là que se joue le design.
Toutes les mesures utilisent les **variables** du script (`$(vCA)`…). Une règle change ? On modifie
le script dans Git, pas trente objets à la main.

## Feuille 1 — « Pilotage des ventes »

| Zone | Objet | Définition |
|---|---|---|
| Bandeau | Texte | `='Données au ' & Date(Max(OrderDate)) & ' · publiées le ' & [Publié le]` |
| KPI | CA 90 j | `$(vCA)` · format monétaire |
| KPI | CA du jour | `$(vCA_Jour)` |
| KPI | CA 7 j vs 7 j précédents | `$(vCA_J7)` · comparaison `$(vCA_J7) / $(vCA_J7_Prec) - 1` |
| KPI | Marge % | `$(vMargePct)` · format `0,0 %` |
| KPI | Panier moyen | `$(vPanierMoyen)` |
| Courbe | CA par jour | Dimension `OrderDate`, mesure `$(vCA)` ; ligne de référence = moyenne |
| Barres | CA par canal | `Channel` · `$(vCA)` |
| Carte ou barres | CA par région | `Region` · `$(vCA)` |
| Tableau | Top 10 produits | `ProductName`, `Category`, `$(vCA)`, `$(vMargePct)` · limite 10 |

## Feuille 2 — « Fraîcheur & traçabilité »

Tableau `[Exécution Airflow]`, `[Publié le]`, `[Version du script]` : le lien entre ce que voit
l'utilisateur, l'exécution Airflow et le commit Git.

## Ce qu'il faut regarder pendant la démo « doublons »

Après l'exécution `duplicate`, l'app 03 **n'a pas été rechargée** : le bandeau garde la date de
publication précédente et le CA du jour reste juste. Les apps 01 et 02, elles, contiennent les
doublons (ouvrez 02 et comparez `Count(OrderLineID)` et `Count(DISTINCT OrderLineID)`).
