from django.contrib import admin
from django.urls import path
from client import views as client_views
from score import views as score_views

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Rota de detalhe do projeto (Client)
    path('projetos/<slug:slug>/', client_views.detalhe_projeto_view, name='projeto'),
    
    # API de Profiles do IPD (Score)
    path(
        'projetos/<int:projeto_id>/profiles/',
        score_views.ProjetoProfilesAPIView.as_view(),
        name='projeto-profiles-ipd'
    ),
    
    # Rota de Streaming do Resumo Executivo IA (Score / Client)
    path(
        'projetos/<int:projeto_id>/resumo-executivo-stream/',
        score_views.resumo_executivo_stream_view,  # Caso a view esteja em score/views.py
        name='resumo_executivo_stream'
    ),

    path(
        'projetos/<int:projeto_id>/causal-impact/',
        score_views.analise_causal_impact_view,
        name='projeto-causal-impact'
    ),

    path('api/conteudo/medias/', score_views.MediaMetricasConteudoView.as_view(), name='media-metricas-conteudo'),
]