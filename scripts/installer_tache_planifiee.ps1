<#
.SYNOPSIS
    Installe une tâche planifiée Windows pour exécuter la sauvegarde chaque jour à 23h00.
.NOTES
    Exécuter dans une console PowerShell.
#>
param(
    [string]$Heure = "23:00",
    [string]$NomTache = "BENCHTECH_Sauvegarde_Quotidienne"
)

$ScriptBat = "E:\BenchTech\Logiciels Devis Factures\scripts\sauvegarde_quotidienne.bat"

if (-not (Test-Path $ScriptBat)) {
    Write-Error "Le fichier $ScriptBat est introuvable."
    exit 1
}

Write-Host "Création de la tâche planifiée '$NomTache' programmée chaque jour à $Heure..." -ForegroundColor Cyan

# Commande schtasks
$cmd = "schtasks /Create /SC DAILY /TN `"$NomTache`" /TR `"`"$ScriptBat`"`" /ST $Heure /F"
Invoke-Expression $cmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Tâche planifiée '$NomTache' installée avec succès ! La sauvegarde s'exécutera tous les jours à $Heure." -ForegroundColor Green
} else {
    Write-Warning "Attention : Si l'accès est refusé, relancez PowerShell en mode 'Exécuter en tant qu'administrateur'."
}

