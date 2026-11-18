"""
Module utilitaire pour formater les noms de fichiers lors du téléchargement des Devis et Factures.
Format demandé :
  - Devis : '<numéro> <désignation>' (ex: 'DEV-2026-0004 power bi.pdf' / '.docx')
  - Facture : 'Facture-<séquence> <désignation>' ou '<numéro> <désignation>' (ex: 'Facture-2026-0004 power bi.pdf' / '.docx')
  - Brouillons : 'devis-brouillon-<id>' / 'facture-brouillon-<id>'
"""
import re


def clean_designation_for_filename(text: str, max_length: int = 60) -> str:
    """Nettoie une désignation pour être valide dans un nom de fichier Windows/Linux/macOS."""
    if not text:
        return ""
    # Remplace les retours à la ligne et tabulations
    cleaned = re.sub(r"[\r\n\t]+", " ", str(text)).strip()
    # Supprime les caractères interdits : \ / : * ? " < > |
    cleaned = re.sub(r'[\\/*?:"<>|]', "", cleaned)
    # Réduit les espaces multiples
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


def get_document_download_filename(doc, doc_type: str = "devis") -> str:
    """
    Retourne le nom de fichier sans extension.
    Exemples :
      - 'DEV-2026-0004 power bi'
      - 'Facture-2026-0004 power bi'
      - 'devis-brouillon-1' (si pas encore émis)
      - 'facture-brouillon-1' (si pas encore émise)
    """
    number = getattr(doc, "number", None)
    if not number:
        return f"{doc_type}-brouillon-{doc.pk}"

    # Récupération de la première désignation significative
    designation = ""
    first_line = doc.lines.first()
    if first_line and first_line.description:
        designation = clean_designation_for_filename(first_line.description)
    elif getattr(doc, "project", None) and doc.project and doc.project.name:
        designation = clean_designation_for_filename(doc.project.name)

    # Pour les factures, convertir "FAC-2026-0004" en "Facture-2026-0004"
    if doc_type == "facture" and number.startswith("FAC-"):
        display_num = "Facture-" + number[4:]
    else:
        display_num = number

    if designation:
        return f"{display_num} {designation}"
    return display_num

