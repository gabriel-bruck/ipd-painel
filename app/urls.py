from django.contrib import admin
from django.urls import path
from client import views as client_views
from score import views as score_views
from user import views as user_views
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', client_views.HomeView.as_view(), name='home'),
    path('login/', user_views.CustomLoginView.as_view(), name='login'),
    
    # Rota de Logout (Opcional, redireciona para a home)
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),
    path('projetos/', client_views.MeusProjetosView.as_view(), name='meus_projetos'),
    
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
    

    path('api/conteudo/medias/', score_views.MediaMetricasIPDView.as_view(), name='media-metricas-conteudo'),
    path('api/temas/', score_views.TemasEngajamentoView.as_view(), name='temas'),
    path('api/ipd/previsao-mensal/', score_views.PrevisaoRankingMensalView.as_view(), name='ipd-previsao'),
    path("api/explicacao-ranking/", score_views.ExplicacaoRankingIAView.as_view(), name="explicacao-ranking"),

]