<#
.SYNOPSIS
    Script de sauvegarde quotidienne automatique - BENCHTECH Gestion
.DESCRIPTION
    Exécute la sauvegarde atomique de la base SQLite et des médias,
    puis purge les sauvegardes de plus de 30 jours.
    Peut copier la sauvegarde vers un dossier Cloud (Google Drive, Dropbox, OneDrive).
.PARAMETER DestinationCloud
    Chemin optionnel vers un dossier Cloud ou disque dur externe.
    Exemple : -DestinationCloud "C:\Users\Hatime\OneDrive\Sauvegardes_BenchTech"
#>
param (
    [string]$DestinationCloud = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = "E:\BenchTech\Logiciels Devis Factures"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectDir "backups\backup_log.txt"

# Création du dossier backups s'il n'existe pas
$BackupDir = Join-Path $ProjectDir "backups"
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$Timestamp] Lancement de la sauvegarde quotidienne..."

try {
    Set-Location -Path $ProjectDir

    $cmdArgs = @("manage.py", "backup_data", "--keep-days", "30")
    if ($DestinationCloud -and (Test-Path $DestinationCloud)) {
        $cmdArgs += @("--destination", $DestinationCloud)
        Add-Content -Path $LogFile -Value "  Cible Cloud spécifiée : $DestinationCloud"
    }

    $output = & $PythonExe $cmdArgs 2>&1
    Add-Content -Path $LogFile -Value ($output -join "`n")
    Add-Content -Path $LogFile -Value "[$Timestamp] Sauvegarde terminée avec succès.`n"
    Write-Host "Sauvegarde BENCHTECH effectuée avec succès." -ForegroundColor Green
}
catch {
    $err = $_.Exception.Message
    Add-Content -Path $LogFile -Value "[$Timestamp] ERREUR : $err`n"
    Write-Error "Échec de la sauvegarde : $err"
}

