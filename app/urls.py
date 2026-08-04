
from django.contrib import admin
from django.urls import path
from client import views
from score.views import ProjetoProfilesAPIView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('projetos/<slug:slug>/', views.detalhe_projeto_view, name='projeto'),
    path(
        'projetos/<int:projeto_id>/profiles/',
        ProjetoProfilesAPIView.as_view(),
        name='projeto-profiles-ipd'
    ),
]
