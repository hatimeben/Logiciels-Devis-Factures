import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .backup_services import get_backup_dir, perform_backup


@require_POST
@login_required
@permission_required("finance.change_companysettings", raise_exception=True)
def backup_create(request):
    """Déclenche manuellement une sauvegarde complète depuis l'interface."""
    try:
        res = perform_backup()
        messages.success(
            request,
            f"Sauvegarde créée avec succès : {res['filename']} ({res['file_size_human']}). "
            f"{res['media_count']} pièces jointes et médias sauvegardés."
        )
    except Exception as e:
        messages.error(request, f"Échec de la sauvegarde : {e}")
    return redirect("company-settings")


@login_required
@permission_required("finance.change_companysettings", raise_exception=True)
def backup_download(request, filename):
    """Téléchargement sécurisé d'une archive de sauvegarde existante."""
    # Protection anti-traversal
    if not re.match(r"^backup_benchtech_[\w\-]+\.zip$", filename):
        return HttpResponseForbidden("Nom de fichier de sauvegarde invalide.")

    backup_folder = get_backup_dir()
    file_path = backup_folder / filename
    if not file_path.exists() or not file_path.is_file():
        raise Http404("Le fichier de sauvegarde demandé n'existe pas.")

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=filename,
    )

