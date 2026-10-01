from django.urls import path

from api import views

urlpatterns = [
    path("api/v1/health", views.health),
    path("api/v1/docs", views.docs),
    path("api/v1/openapi.json", views.openapi),
]

handler404 = "api.views.not_found"
