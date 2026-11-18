import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone


def get_backup_dir(custom_dest=None):
    if custom_dest:
        dest_path = Path(custom_dest)
    else:
        dest_path = Path(settings.BASE_DIR) / "backups"
    dest_path.mkdir(parents=True, exist_ok=True)
    return dest_path


def format_bytes_human(num_bytes):
    for unit in ["o", "Ko", "Mo", "Go"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} To"


def perform_backup(destination_dir=None, keep_days=30):
    """
    Effectue une sauvegarde complète et atomique :
    1. Base de données SQLite (via l'API backup de SQLite garantissant l'intégrité à chaud).
    2. Répertoire des médias / justificatifs (media/).
    3. Manifeste d'intégrité (backup_manifest.json).
    4. Compression dans une archive .zip horodatée.
    5. Rotation des anciennes sauvegardes (purge au-delà de keep_days).
    """
    backup_folder = get_backup_dir(destination_dir)
    now_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_filename = f"backup_benchtech_{now_str}.zip"
    zip_path = backup_folder / zip_filename

    # Dossier temporaire pour préparer la structure
    temp_dir = backup_folder / f"temp_{now_str}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Sauvegarde SQLite à chaud
        db_path = Path(settings.DATABASES["default"]["NAME"])
        temp_db_path = temp_dir / "db.sqlite3"
        if db_path.exists():
            # Connexion source
            src_conn = sqlite3.connect(str(db_path))
            # Connexion cible snapshot
            dst_conn = sqlite3.connect(str(temp_db_path))
            with dst_conn:
                src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()

        # 2. Copie des fichiers médias (justificatifs de dépenses, logos, etc.)
        media_root = Path(settings.MEDIA_ROOT)
        temp_media_dir = temp_dir / "media"
        media_files_count = 0
        if media_root.exists():
            shutil.copytree(str(media_root), str(temp_media_dir), dirs_exist_ok=True)
            for _ in temp_media_dir.rglob("*"):
                if _.is_file():
                    media_files_count += 1

        # 3. Création du manifeste
        manifest_data = {
            "application": "BENCHTECH Gestion",
            "backup_date": timezone.now().isoformat(),
            "database_engine": settings.DATABASES["default"]["ENGINE"],
            "media_files_count": media_files_count,
            "created_by": "System Backup Service",
        }
        with open(temp_dir / "backup_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)

        # 4. Compression dans le fichier ZIP final
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    full_file_path = Path(root) / file
                    rel_path = full_file_path.relative_to(temp_dir)
                    zipf.write(full_file_path, arcname=str(rel_path))

        file_size = zip_path.stat().st_size

        # 5. Rotation automatique (suppression des backups > keep_days)
        purged_count = 0
        if keep_days and keep_days > 0:
            cutoff = datetime.now() - timedelta(days=keep_days)
            for existing in backup_folder.glob("backup_benchtech_*.zip"):
                if existing != zip_path:
                    mtime = datetime.fromtimestamp(existing.stat().st_mtime)
                    if mtime < cutoff:
                        try:
                            existing.unlink()
                            purged_count += 1
                        except OSError:
                            pass

        return {
            "success": True,
            "filename": zip_filename,
            "archive_path": str(zip_path),
            "file_size": file_size,
            "file_size_human": format_bytes_human(file_size),
            "media_count": media_files_count,
            "purged_count": purged_count,
            "created_at": datetime.now(),
        }

    finally:
        # Nettoyage dossier temporaire
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir), ignore_errors=True)


def list_backups(destination_dir=None):
    """
    Renvoie la liste des sauvegardes disponibles triées de la plus récente à la plus ancienne.
    """
    backup_folder = get_backup_dir(destination_dir)
    backups = []
    for f in backup_folder.glob("backup_benchtech_*.zip"):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "path": str(f),
            "size": stat.st_size,
            "size_human": format_bytes_human(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime),
        })
    backups.sort(key=lambda x: x["modified_at"], reverse=True)
    return backups

