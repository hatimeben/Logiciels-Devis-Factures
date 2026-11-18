from django.core.management.base import BaseCommand
from apps.finance.backup_services import perform_backup


class Command(BaseCommand):
    help = "Effectue une sauvegarde complète et sécurisée de la base de données et des justificatifs médias dans un fichier .zip."

    def add_arguments(self, parser):
        parser.add_argument(
            "--destination",
            "--dest",
            type=str,
            default=None,
            help="Dossier cible pour la sauvegarde (ex: dossier Google Drive, OneDrive, Dropbox ou disque externe).",
        )
        parser.add_argument(
            "--keep-days",
            type=int,
            default=30,
            help="Nombre de jours de rétention des sauvegardes (défaut : 30 jours).",
        )

    def handle(self, *args, **options):
        dest = options.get("destination")
        keep_days = options.get("keep_days", 30)

        self.stdout.write(self.style.NOTICE("Démarrage de la sauvegarde BENCHTECH..."))
        result = perform_backup(destination_dir=dest, keep_days=keep_days)

        self.stdout.write(
            self.style.SUCCESS(
                f"[OK] Sauvegarde reussie : {result['filename']} ({result['file_size_human']})\n"
                f"  Emplacement : {result['archive_path']}\n"
                f"  Fichiers medias archives : {result['media_count']}\n"
                f"  Anciennes archives purgees (> {keep_days} jours) : {result['purged_count']}"
            )
        )
