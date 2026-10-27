from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render

from .company_forms import CompanySettingsForm
from .models import CompanySettings


@login_required
@permission_required("finance.change_companysettings", raise_exception=True)
def company_settings_view(request):
    settings_obj = CompanySettings.get_settings()
    form = CompanySettingsForm(
        request.POST or None,
        request.FILES or None,
        instance=settings_obj,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            "Les coordonnées, mentions légales et le logo de l'entreprise ont été enregistrés.",
        )
        return redirect("company-settings")

    return render(
        request,
        "finance/company/settings.html",
        {
            "form": form,
            "company": settings_obj,
        },
    )

