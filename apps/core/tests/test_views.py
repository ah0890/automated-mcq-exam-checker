from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AuthenticationRequired(TestCase):
    def test_dashboard_redirects_anonymous_users_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_settings_redirects_anonymous_users_to_login(self):
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 302)

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)


class SignedInPages(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
        self.client.force_login(self.user)

    def test_dashboard_renders_navigation_and_status(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        for label in ["Answer Key Library", "Upload Student Papers", "Processing Center", "Manual Verification"]:
            self.assertContains(response, label)
        self.assertContains(response, "System status")

    def test_settings_page(self):
        self.assertEqual(self.client.get(reverse("core:settings")).status_code, 200)

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
