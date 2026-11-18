@echo off
REM ========================================================
REM Script de sauvegarde quotidienne automatique - BENCHTECH
REM ========================================================
cd /d "E:\BenchTech\Logiciels Devis Factures"
".venv\Scripts\python.exe" manage.py backup_data --keep-days 30 >> "backups\backup_log.txt" 2>&1
echo Sauvegarde terminee.

