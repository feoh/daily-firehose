from __future__ import annotations

from django.urls import reverse

from .support.base import StaticFilesTestCase
from .support.builders import build_user


class AuthenticationShellTests(StaticFilesTestCase):
    def test_login_page_omits_authenticated_keyboard_help(self) -> None:
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Keyboard shortcuts: press")
        self.assertNotContains(response, 'id="keyboard-help"')

    def test_authenticated_pages_include_keyboard_help(self) -> None:
        self.client.force_login(build_user())

        response = self.client.get(reverse("feeds"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keyboard shortcuts: press")
        self.assertContains(response, 'id="keyboard-help"')
