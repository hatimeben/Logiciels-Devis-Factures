from .models import CompanySettings


def company_settings(request):
    """
    Expose les paramètres de l'entreprise (raison sociale, identifiants légaux,
    coordonnées bancaires, logo) à l'ensemble des templates Django.
    """
    return {
        "company": CompanySettings.get_settings(),
    }

