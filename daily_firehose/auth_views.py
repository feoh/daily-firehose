from __future__ import annotations

from typing import Any

from django.contrib.auth import views as django_auth_views
from django.http import HttpRequest

from .redirects import safe_redirect_target


class SafeRedirectURLMixin:
    """Apply the application's strict browser redirect policy to auth `next`."""

    request: HttpRequest
    redirect_field_name: str

    def get_redirect_url(self) -> str:
        redirect_to = self.request.POST.get(
            self.redirect_field_name,
            self.request.GET.get(self.redirect_field_name),
        )
        return safe_redirect_target(self.request, redirect_to, fallback="")


class LoginView(SafeRedirectURLMixin, django_auth_views.LoginView):
    request: Any


class LogoutView(SafeRedirectURLMixin, django_auth_views.LogoutView):
    request: Any
