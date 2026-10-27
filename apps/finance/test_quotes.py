from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer

from .models import DocumentHistory, Quote, QuoteLine, QuoteNumberingSettings
from .quote_calculations import calculate_quote_line, calculate_quote_totals
from .quote_services import change_quote_status, send_quote


class QuoteModuleTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client de formation",
            email="client@example.test",
            address="10 rue des Écoles",
            city="Rabat",
        )
        self.category = ServiceCategory.objects.create(name="Formation")
        self.service = Service.objects.create(
            category=self.category,
            name="Formation Excel",
            default_unit_price=Decimal("100.00"),
        )
        self.tax_rate = TaxRate.objects.create(
            name="Taxe paramétrée",
            percentage=Decimal("20.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="quote-user",
            email="quote-user@example.test",
            password="safe-test-password",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="finance",
            codename__in=["view_quote", "add_quote", "change_quote"],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)

    def _line_data(
        self,
        index,
        *,
        line_id="",
        service_id="",
        description="",
        quantity="1.000",
        unit_price="100.00",
        discount_type="percentage",
        discount_value="0.00",
        tax_rate_id="",
    ):
        prefix = f"lines-{index}-"
        return {
            prefix + "id": line_id,
            prefix + "service": service_id,
            prefix + "description": description,
            prefix + "quantity": quantity,
            prefix + "unit_price": unit_price,
            prefix + "discount_type": discount_type,
            prefix + "discount_value": discount_value,
            prefix + "tax_rate": tax_rate_id,
            prefix + "DELETE": "",
        }

    def _quote_payload(self, lines, *, customer_id=None, initial_forms=0):
        payload = {
            "customer": customer_id if customer_id is not None else self.customer.pk,
            "project": "",
            "valid_until": "31/12/2026",
            "notes": "Conditions de la prestation",
            "lines-TOTAL_FORMS": str(len(lines)),
            "lines-INITIAL_FORMS": str(initial_forms),
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for index, line in enumerate(lines):
            payload.update(line)
        return payload

    def _create_quote(self, lines=None):
        lines = lines or [
            self._line_data(
                0,
                service_id=str(self.service.pk),
                description=self.service.name,
                unit_price="100.00",
            )
        ]
        response = self.client.post(
            reverse("quote-create"),
            self._quote_payload(lines),
        )
        self.assertEqual(response.status_code, 302, response.content.decode())
        return Quote.objects.get(pk=response.url.rstrip("/").split("/")[-1])

    def _create_single_line_quote(self, **line_options):
        line = {
            "service_id": str(self.service.pk),
            "description": self.service.name,
            "unit_price": "100.00",
        }
        line.update(line_options)
        return self._create_quote(
            [
                self._line_data(
                    0,
                    **line,
                )
            ]
        )

    def test_create_quote_with_one_catalog_line_and_calculates_tax_discount(self):
        quote = self._create_single_line_quote(
            quantity="2.000",
            discount_value="10.00",
            tax_rate_id=str(self.tax_rate.pk),
        )
        line = quote.lines.get()

        self.assertEqual(quote.status, Quote.Status.DRAFT)
        self.assertIsNone(quote.number)
        self.assertEqual(line.description, "Formation Excel")
        self.assertEqual(line.unit_price, Decimal("100.00"))
        self.assertEqual(line.tax_name, "Taxe paramétrée")
        self.assertEqual(line.tax_percentage, Decimal("20.00"))
        self.assertEqual(
            calculate_quote_totals([line]),
            {
                "gross": Decimal("200.00"),
                "discount": Decimal("20.00"),
                "net": Decimal("180.00"),
                "tax": Decimal("36.00"),
                "total": Decimal("216.00"),
            },
        )

    def test_create_quote_with_multiple_lines_and_fixed_discount(self):
        lines = [
            self._line_data(
                0,
                service_id=str(self.service.pk),
                description="Formation Excel",
                quantity="2.000",
                unit_price="50.00",
                discount_type="fixed",
                discount_value="5.00",
                tax_rate_id=str(self.tax_rate.pk),
            ),
            self._line_data(
                1,
                description="Assistance personnalisée",
                quantity="3.000",
                unit_price="25.00",
            ),
        ]
        quote = self._create_quote(lines)

        self.assertEqual(quote.lines.count(), 2)
        totals = calculate_quote_totals(quote.lines.all())
        self.assertEqual(totals["gross"], Decimal("175.00"))
        self.assertEqual(totals["discount"], Decimal("5.00"))
        self.assertEqual(totals["net"], Decimal("170.00"))
        self.assertEqual(totals["tax"], Decimal("19.00"))
        self.assertEqual(totals["total"], Decimal("189.00"))
        self.assertIsNone(quote.lines.order_by("id").last().service)

    def test_per_line_rounding_uses_half_up_for_subtotal_and_tax(self):
        result = calculate_quote_line(
            Decimal("1.005"),
            Decimal("1.00"),
            QuoteLine.DiscountType.PERCENTAGE,
            Decimal("0.00"),
            Decimal("5.00"),
        )

        self.assertEqual(result["gross"], Decimal("1.01"))
        self.assertEqual(result["tax"], Decimal("0.05"))
        self.assertEqual(result["total"], Decimal("1.06"))

    def test_tax_is_zero_when_no_rate_is_selected(self):
        quote = self._create_single_line_quote()
        line = quote.lines.get()
        self.assertEqual(line.tax_percentage, Decimal("0.00"))
        self.assertEqual(line.tax_name, "Sans taxe")
        self.assertEqual(calculate_quote_totals([line])["total"], Decimal("100.00"))

    def test_only_active_tax_rate_is_preselected_for_new_quote_lines(self):
        response = self.client.get(reverse("quote-create"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["formset"][0]["tax_rate"].value(), self.tax_rate.pk)
        self.assertEqual(
            response.context["formset"].empty_form["tax_rate"].value(),
            self.tax_rate.pk,
        )

    def test_service_choices_show_catalog_price_and_expose_it_for_prefill(self):
        response = self.client.get(reverse("quote-create"))

        self.assertEqual(response.status_code, 200)
        service_field = response.context["formset"][0]["service"]
        rendered = str(service_field)
        self.assertIn("Formation Excel", rendered)
        self.assertIn("100,00 MAD HT", rendered)
        self.assertIn('data-unit-price="100.00"', rendered)
        tax_field = response.context["formset"][0]["tax_rate"]
        self.assertIn('data-percentage="20.00"', str(tax_field))

    def test_multiple_active_tax_rates_are_not_guessed(self):
        second_rate = TaxRate.objects.create(
            name="Exonération",
            percentage=Decimal("0.00"),
        )

        response = self.client.get(reverse("quote-create"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["formset"][0]["tax_rate"].value())
        self.assertIn(
            second_rate.pk,
            list(response.context["formset"][0].fields["tax_rate"].queryset.values_list("pk", flat=True)),
        )

    def test_invalid_quantity_price_and_discounts_are_rejected(self):
        invalid_lines = [
            self._line_data(0, description="Ligne", quantity="0", unit_price="10.00"),
            self._line_data(0, description="Ligne", quantity="-1", unit_price="10.00"),
            self._line_data(0, description="Ligne", quantity="1", unit_price="-1.00"),
            self._line_data(
                0,
                description="Ligne",
                unit_price="10.00",
                discount_value="100.01",
            ),
            self._line_data(
                0,
                description="Ligne",
                unit_price="10.00",
                discount_type="fixed",
                discount_value="10.01",
            ),
        ]
        for line in invalid_lines:
            with self.subTest(line=line):
                response = self.client.post(
                    reverse("quote-create"),
                    self._quote_payload([line]),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Quote.objects.count(), 0)

    def test_discount_above_hundred_is_rejected_by_calculator(self):
        with self.assertRaises(ValueError):
            calculate_quote_line(
                Decimal("1"),
                Decimal("100"),
                QuoteLine.DiscountType.PERCENTAGE,
                Decimal("100.01"),
                Decimal("0"),
            )

    def test_quote_without_customer_or_lines_is_rejected(self):
        response = self.client.post(
            reverse("quote-create"),
            self._quote_payload(
                [self._line_data(0)],
                customer_id="",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Quote.objects.count(), 0)

        response = self.client.post(
            reverse("quote-create"),
            self._quote_payload([self._line_data(0)]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Quote.objects.count(), 0)

    def test_empty_catalog_price_and_free_line_validation(self):
        response = self.client.post(
            reverse("quote-create"),
            self._quote_payload(
                [
                    self._line_data(
                        0,
                        description="Ligne libre sans prix",
                        unit_price="",
                    )
                ]
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Quote.objects.count(), 0)

        quote = self._create_quote(
            [self._line_data(0, description="Ligne libre", unit_price="12.50")]
        )
        self.assertIsNone(quote.lines.get().service)
        self.assertEqual(quote.lines.get().unit_price, Decimal("12.50"))

    def test_service_and_tax_changes_do_not_rewrite_existing_quote_line(self):
        quote = self._create_single_line_quote(
            tax_rate_id=str(self.tax_rate.pk),
            unit_price="100.00",
        )
        line = quote.lines.get()
        self.service.name = "Nouvelle désignation"
        self.service.default_unit_price = Decimal("250.00")
        self.service.save()
        self.tax_rate.name = "Nouveau libellé"
        self.tax_rate.percentage = Decimal("10.00")
        self.tax_rate.save()

        line.refresh_from_db()
        self.assertEqual(line.description, "Formation Excel")
        self.assertEqual(line.unit_price, Decimal("100.00"))
        self.assertEqual(line.tax_name, "Taxe paramétrée")
        self.assertEqual(line.tax_percentage, Decimal("20.00"))

    def test_customer_changes_do_not_rewrite_issued_quote_snapshot(self):
        quote = self._create_single_line_quote()
        send_quote(quote.pk, self.user)
        quote.refresh_from_db()

        self.customer.name = "Nouvelle raison sociale"
        self.customer.address = "Adresse modifiée"
        self.customer.save()
        quote.refresh_from_db()

        self.assertEqual(quote.customer_snapshot["name"], "Client de formation")
        self.assertEqual(quote.customer_snapshot["address"], "10 rue des Écoles")

    def test_edit_draft_saves_customer_and_line_changes(self):
        quote = self._create_single_line_quote()
        line = quote.lines.get()
        payload = self._quote_payload(
            [
                self._line_data(
                    0,
                    line_id=str(line.pk),
                    service_id=str(self.service.pk),
                    description="Formation Excel avancée",
                    quantity="2.000",
                    unit_price="125.00",
                )
            ],
            initial_forms=1,
        )
        response = self.client.post(reverse("quote-edit", args=[quote.pk]), payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        line.refresh_from_db()
        self.assertEqual(line.description, "Formation Excel avancée")
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.unit_price, Decimal("125.00"))

    def test_edit_draft_can_delete_a_line(self):
        quote = self._create_quote(
            [
                self._line_data(0, description="Prestation conservée", unit_price="100.00"),
                self._line_data(1, description="Prestation supprimée", unit_price="50.00"),
            ]
        )
        lines = list(quote.lines.order_by("id"))
        payload = self._quote_payload(
            [
                self._line_data(
                    0,
                    line_id=str(lines[0].pk),
                    description=lines[0].description,
                    unit_price=str(lines[0].unit_price),
                ),
                self._line_data(
                    1,
                    line_id=str(lines[1].pk),
                    description=lines[1].description,
                    unit_price=str(lines[1].unit_price),
                ),
            ],
            initial_forms=2,
        )
        payload["lines-1-DELETE"] = "on"

        response = self.client.post(reverse("quote-edit", args=[quote.pk]), payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(
            list(quote.lines.values_list("description", flat=True)),
            ["Prestation conservée"],
        )
        latest_history = quote.history.order_by("-created_at").first()
        self.assertEqual(len(latest_history.snapshot["after"]["lines"]), 1)

    def test_send_assigns_unique_number_and_history(self):
        first = self._create_single_line_quote()
        second = self._create_single_line_quote()

        send_quote(first.pk, self.user)
        send_quote(second.pk, self.user)
        first.refresh_from_db()
        second.refresh_from_db()

        year = timezone.localdate().year
        self.assertEqual(first.number, f"DEV-{year}-0001")
        self.assertEqual(second.number, f"DEV-{year}-0002")
        self.assertNotEqual(first.number, second.number)
        self.assertEqual(first.status, Quote.Status.SENT)
        self.assertTrue(
            DocumentHistory.objects.filter(
                quote=first,
                event=DocumentHistory.Event.STATUS_CHANGED,
                snapshot__from_status=Quote.Status.DRAFT,
                snapshot__to_status=Quote.Status.SENT,
            ).exists()
        )

    def test_number_format_can_be_changed_from_configuration(self):
        numbering = QuoteNumberingSettings.objects.get(pk=1)
        numbering.prefix = "OFFRE"
        numbering.separator = "/"
        numbering.include_year = False
        numbering.sequence_width = 3
        numbering.save()
        quote = self._create_single_line_quote()

        send_quote(quote.pk, self.user)
        quote.refresh_from_db()

        self.assertEqual(quote.number, "OFFRE/001")

    def test_send_without_lines_does_not_consume_a_number(self):
        quote = Quote.objects.create(customer=self.customer)
        with self.assertRaises(ValidationError):
            send_quote(quote.pk, self.user)

        valid_quote = self._create_single_line_quote()
        send_quote(valid_quote.pk, self.user)
        valid_quote.refresh_from_db()
        self.assertTrue(valid_quote.number.endswith("-0001"))

    def test_number_is_unique_at_database_level(self):
        quote = self._create_single_line_quote()
        Quote.objects.filter(pk=quote.pk).update(
            number="DEV-2026-0001",
            status=Quote.Status.SENT,
            issue_date=date(2026, 10, 25),
        )
        another = Quote.objects.create(customer=self.customer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Quote.objects.filter(pk=another.pk).update(number="DEV-2026-0001")

    def test_sent_quote_and_lines_cannot_be_silently_modified(self):
        quote = self._create_single_line_quote()
        send_quote(quote.pk, self.user)
        quote.refresh_from_db()
        quote.refresh_from_db()
        quote.notes = "Modification silencieuse"
        with self.assertRaises(ValidationError):
            quote.save()
        line = quote.lines.get()
        line.unit_price = Decimal("1.00")
        with self.assertRaises(ValidationError):
            line.save()

        response = self.client.get(reverse("quote-edit", args=[quote.pk]))
        self.assertEqual(response.status_code, 302)
        quote.refresh_from_db()
        self.assertNotEqual(quote.notes, "Modification silencieuse")

    def test_status_transitions_and_history_are_validated(self):
        quote = self._create_single_line_quote()
        send_quote(quote.pk, self.user)
        quote.refresh_from_db()
        accepted = change_quote_status(quote.pk, Quote.Status.ACCEPTED, self.user)
        self.assertEqual(accepted.status, Quote.Status.ACCEPTED)
        self.assertTrue(
            DocumentHistory.objects.filter(
                quote=quote,
                event=DocumentHistory.Event.STATUS_CHANGED,
                snapshot__from_status=Quote.Status.SENT,
                snapshot__to_status=Quote.Status.ACCEPTED,
            ).exists()
        )
        with self.assertRaises(ValidationError):
            change_quote_status(quote.pk, Quote.Status.DRAFT, self.user)

    def test_revision_links_to_immutable_source_and_copies_snapshots(self):
        quote = self._create_single_line_quote(
            tax_rate_id=str(self.tax_rate.pk),
            discount_value="5.00",
        )
        send_quote(quote.pk, self.user)
        quote.refresh_from_db()
        original_number = quote.number
        original_line = quote.lines.get()

        from .quote_services import create_quote_revision

        revision = create_quote_revision(quote.pk, self.user)
        copied = revision.lines.get()
        self.assertEqual(revision.revision_of, quote)
        self.assertEqual(revision.status, Quote.Status.DRAFT)
        self.assertIsNone(revision.number)
        self.assertEqual(copied.description, original_line.description)
        self.assertEqual(copied.unit_price, original_line.unit_price)
        self.assertEqual(copied.tax_percentage, original_line.tax_percentage)
        quote.refresh_from_db()
        self.assertEqual(quote.number, original_number)
        self.assertEqual(quote.status, Quote.Status.SENT)

    def test_pdf_download_is_valid_and_uses_a4_layout(self):
        line_text = "Conseil informatique et accompagnement " * 6
        quote = self._create_quote(
            [self._line_data(0, description=line_text[:250], unit_price="100.00")]
        )
        send_quote(quote.pk, self.user)
        response = self.client.get(reverse("quote-pdf", args=[quote.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(b"/MediaBox [ 0 0 595.2756 841.8898 ]", response.content)

    def test_draft_pdf_is_available_as_unissued_preview(self):
        quote = self._create_single_line_quote()
        response = self.client.get(reverse("quote-pdf", args=[quote.pk]))
        quote.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f"devis-brouillon-{quote.pk}.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIsNone(quote.number)
        self.assertEqual(quote.status, Quote.Status.DRAFT)

    def test_users_without_permissions_cannot_list_read_or_change_quotes(self):
        quote = self._create_single_line_quote()
        other_user = get_user_model().objects.create_user(
            username="unauthorized",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(other_user)

        self.assertEqual(client.get(reverse("quote-list")).status_code, 403)
        self.assertEqual(client.get(reverse("quote-detail", args=[quote.pk])).status_code, 403)
        self.assertEqual(client.get(reverse("quote-edit", args=[quote.pk])).status_code, 403)
        self.assertEqual(client.post(reverse("quote-send", args=[quote.pk])).status_code, 403)

    def test_anonymous_users_are_redirected_to_login(self):
        quote = self._create_single_line_quote()
        client = Client()
        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 302)
        self.assertEqual(root_response.url, reverse("dashboard"))
        response = client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/connexion/", response.url)
        login_page = client.get(response.url)
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, "Se connecter")
        self.assertContains(login_page, "Nom d’utilisateur")
        self.assertContains(login_page, 'lang="fr"')
        self.assertContains(login_page, "/static/finance/quotes.css")

    def test_quote_page_links_stylesheet_from_absolute_static_path(self):
        response = self.client.get(reverse("quote-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/static/finance/quotes.css"')

    def test_list_search_filtering_and_detail_render(self):
        quote = self._create_single_line_quote()
        send_quote(quote.pk, self.user)
        quote.refresh_from_db()

        response = self.client.get(reverse("quote-list"), {"q": "Client de formation", "status": "sent"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, quote.number)
        self.assertContains(response, "Client de formation")
        self.assertEqual(
            self.client.get(reverse("quote-detail", args=[quote.pk])).status_code,
            200,
        )


class ConcurrentQuoteNumberTests(TransactionTestCase):
    reset_sequences = True

    def test_simultaneous_sends_receive_distinct_numbers(self):
        customer = Customer.objects.create(name="Client concurrent")
        category = ServiceCategory.objects.create(name="Services")
        service = Service.objects.create(
            category=category,
            name="Consultation",
            default_unit_price=Decimal("100.00"),
        )
        user = get_user_model().objects.create_user(username="concurrent")
        quotes = []
        for _ in range(2):
            quote = Quote.objects.create(customer=customer)
            QuoteLine.objects.create(
                quote=quote,
                service=service,
                description=service.name,
                quantity=Decimal("1.000"),
                unit_price=service.default_unit_price,
            )
            quotes.append(quote.pk)
        barrier = Barrier(len(quotes))

        def send(quote_id):
            close_old_connections()
            actor = get_user_model().objects.get(pk=user.pk)
            barrier.wait(timeout=10)
            number = send_quote(quote_id, actor).number
            close_old_connections()
            return number

        with ThreadPoolExecutor(max_workers=2) as executor:
            numbers = list(executor.map(send, quotes))

        self.assertEqual(len(set(numbers)), 2)
        self.assertTrue(all(number.startswith(f"DEV-{timezone.localdate().year}-") for number in numbers))
