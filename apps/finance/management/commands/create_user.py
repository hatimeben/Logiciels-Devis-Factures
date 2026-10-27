from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crée rapidement un utilisateur avec le rôle spécifié (commercial, comptable ou admin)"

    def add_arguments(self, parser):
        parser.add_argument("username", type=str, help="Nom d'utilisateur")
        parser.add_argument("--password", type=str, required=True, help="Mot de passe de l'utilisateur")
        parser.add_argument("--email", type=str, default="", help="Adresse e-mail (optionnelle)")
        parser.add_argument(
            "--role",
            type=str,
            choices=["commercial", "comptable", "admin"],
            default="commercial",
            help="Rôle de l'utilisateur (commercial par défaut)",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        password = options["password"]
        email = options["email"]
        role = options["role"]

        if User.objects.filter(username=username).exists():
            raise CommandError(f"L'utilisateur '{username}' existe déjà.")

        is_superuser = role == "admin"
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            is_staff=True,
            is_superuser=is_superuser,
        )

        if role == "commercial":
            group, _ = Group.objects.get_or_create(name="Commercial / Utilisateur Normal")
            user.groups.add(group)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Utilisateur standard '{username}' créé avec succès dans le groupe '{group.name}' !"
                )
            )
        elif role == "comptable":
            group, _ = Group.objects.get_or_create(name="Comptable / Gestionnaire Financier")
            user.groups.add(group)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Utilisateur comptable '{username}' créé avec succès dans le groupe '{group.name}' !"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Super-administrateur '{username}' créé avec succès !"
                )
            )

