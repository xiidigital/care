"""
Route composition by runtime role (ADR-0006).

Two surfaces exist and they do not overlap. The ``api`` role routes the public
application; the ``task_worker`` role routes the private task-execution endpoint
and nothing public. Isolation is by *absence* -- an unregistered URL cannot be
reached by a request that guesses it, whereas a view-level refusal still leaves
the view mounted, importable and one authentication mistake away from serving.

A small diagnostic set is intentionally shared by both roles and is listed in
``DIAGNOSTIC_URLS`` below. ``home`` is in it for a reason worth recording: every
error template extends ``base.html``, which reverses ``home``, so a worker
without that route would answer an ordinary 404 with ``NoReverseMatch``.

``scheduler`` and ``init`` serve no HTTP. They resolve the diagnostic set alone,
because nothing dereferences a URL in those processes and mounting the public
API in a process that cannot serve it would only widen what an accident exposes.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views import defaults as default_views
from django.views.decorators.cache import cache_page
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from care.users.api.viewsets.change_password import ChangePasswordView
from care.users.reset_password_views import (
    ResetPasswordCheck,
    ResetPasswordConfirm,
    ResetPasswordRequestToken,
)
from care.utils.tasks.views import execute_task
from config import api_router
from config.firebase_auth_urls import build_firebase_auth_urlpatterns
from config.keycloak_urls import build_keycloak_urlpatterns
from config.runtime import API_ROLE, TASK_WORKER_ROLE

from .auth_views import (
    AnnotatedTokenVerifyView,
    LogoutView,
    TokenObtainPairView,
    TokenRefreshView,
)
from .views import app_version, home_view, ping

#: Shared by every role. Liveness, dependency diagnostics, build identification,
#: and the ``home`` route the error templates reverse.
DIAGNOSTIC_URLS = [
    path("", home_view, name="home"),
    path("ping/", ping, name="ping"),
    path("health/", include("healthy_django.urls", namespace="healthy_django")),
    path("app_version/", app_version, name="app_version"),
]

PUBLIC_API_URLS = [
    path(f"{settings.ADMIN_URL.rstrip('/')}/", admin.site.urls),
    path("api/v1/auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="token_obtain_pair"),
    path(
        "api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"
    ),
    path(
        "api/v1/auth/token/verify/",
        AnnotatedTokenVerifyView.as_view(),
        name="token_verify",
    ),
    path(
        "api/v1/password_reset/",
        ResetPasswordRequestToken.as_view(),
        name="password_reset_request",
    ),
    path(
        "api/v1/password_reset/confirm/",
        ResetPasswordConfirm.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "api/v1/password_reset/check/",
        ResetPasswordCheck.as_view(),
        name="password_reset_check",
    ),
    path(
        "api/v1/password_change/",
        ChangePasswordView.as_view(),
        name="change_password_view",
    ),
    path("api/v1/", include(api_router.urlpatterns)),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
    *build_firebase_auth_urlpatterns(enabled=settings.FIREBASE_AUTH_ENABLED),
    *build_keycloak_urlpatterns(enabled=settings.KEYCLOAK_ENABLED),
]

#: Private. Registered only by the task-worker role; see §14 of ES-06 for why it
#: carries no application-layer secret.
WORKER_URLS = [
    path("internal/tasks/execute/", execute_task, name="internal_task_execute")
]

urlpatterns = [*DIAGNOSTIC_URLS]

if settings.CARE_PROCESS_ROLE == API_ROLE:
    urlpatterns += PUBLIC_API_URLS

if settings.CARE_PROCESS_ROLE == TASK_WORKER_ROLE:
    # The flag remains the switch, so a deployment can still turn the endpoint
    # off on a worker; it defaults on for this role and off for every other.
    if settings.CARE_TASK_HANDLER_ENDPOINT_ENABLED:
        urlpatterns += WORKER_URLS
elif settings.CARE_TASK_HANDLER_ENDPOINT_ENABLED:
    # Explicitly enabled outside the worker role. Honoured rather than silently
    # dropped -- ADR-0006 forbids inferring intent -- but it is a deployment
    # mistake worth seeing in the logs.
    import logging

    logging.getLogger("care.runtime").warning(
        "CARE_TASK_HANDLER_ENDPOINT_ENABLED is set on the %r role; the private "
        "task-execution route belongs to task_worker.",
        settings.CARE_PROCESS_ROLE,
    )
    urlpatterns += WORKER_URLS

if settings.DEBUG and settings.CARE_PROCESS_ROLE == API_ROLE:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]

    if "silk" in settings.INSTALLED_APPS:
        urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]

if (settings.DEBUG or not settings.IS_PRODUCTION) and (
    settings.CARE_PROCESS_ROLE == API_ROLE
):
    urlpatterns += [
        path(
            "api/schema/",
            cache_page(None, cache="swagger_cache")(SpectacularAPIView.as_view()),
            name="schema",
        ),
        path(
            "swagger/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
        path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

if settings.CARE_PROCESS_ROLE == API_ROLE:
    # Plugin routes are public API surface and follow it. A plugin that expects
    # its URLs in every process is documented as an API-only assumption in
    # inventory/plugin-impact.md rather than accommodated here.
    for plug in settings.PLUGIN_APPS:
        urlpatterns += [path(f"api/{plug}/", include(f"{plug}.urls"))]
