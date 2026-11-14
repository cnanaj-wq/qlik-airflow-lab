<#
.SYNOPSIS
  Démarre (ou vérifie, ou arrête) l'Airflow 3 du poste AVEC la greffe Qlik.
.EXAMPLE
  .\scripts\lab-up.ps1 -Config    # vérifie la fusion des fichiers, sans rien démarrer
  .\scripts\lab-up.ps1            # démarre / met à jour les conteneurs
  .\scripts\lab-up.ps1 -Down      # retire la greffe (Airflow repart sans le lab Qlik)
#>
param(
    [string]$AirflowDir = "C:\airflow",
    [switch]$Config,
    [switch]$Down,
    [switch]$NoMonitoring
)
$ErrorActionPreference = "Stop"
$LabDir = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$LabDir\.env")) { throw "Fichier $LabDir\.env absent : copiez .env.example en .env et remplissez-le." }

$files = @("-f", "$AirflowDir\docker-compose.yaml")
if (-not $NoMonitoring -and (Test-Path "$AirflowDir\docker-compose.monitoring.yaml")) {
    $files += @("-f", "$AirflowDir\docker-compose.monitoring.yaml")
}
$base = @("compose", "-p", "airflow", "--project-directory", $AirflowDir,
          "--env-file", "$AirflowDir\.env", "--env-file", "$LabDir\.env")

if ($Down) {
    # Recrée Airflow SANS la greffe ; la base de supervision est arrêtée, ses données conservées.
    & docker @base @files up -d --remove-orphans
    exit $LASTEXITCODE
}
$files += @("-f", "$LabDir\docker\compose.qlik.yaml")
if ($Config) {
    # --no-interpolate : vérifie la structure sans afficher la clé API dans la console.
    & docker @base @files config --no-interpolate --quiet
    if ($LASTEXITCODE -eq 0) { Write-Host "Configuration fusionnée valide." -ForegroundColor Green }
    exit $LASTEXITCODE
}
& docker @base @files up -d
