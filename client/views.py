from django.shortcuts import render, get_object_or_404,redirect
from .models import ProjetoIPD, ProjetoCliente, CorPadrao
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.contrib import messages



@login_required(login_url='home')
def detalhe_projeto_view(request, slug, json_response=False):
    projeto = get_object_or_404(
        ProjetoCliente.objects.prefetch_related('projetoclienteipd_set__projeto_ipd'), 
        slug=slug
    )
    
    tem_permissao = (
        request.user.is_superuser or 
        request.user.is_staff or 
        projeto.usuarios_autorizados.filter(user=request.user).exists()
    )
    
    if not tem_permissao:
        if json_response:
            return JsonResponse({'error': 'Acesso não autorizado.'}, status=403)
        messages.error(request, 'Você não possui permissão para acessar este projeto.')
        return redirect('meus_projetos')

    # 1. Consulta todas as cores cadastradas no banco
    cores_queryset = CorPadrao.objects.all()
    
    # Lista estruturada com nome, chave e hex (ideal para iteração em loops)
    cores_lista = [
        {
            'id': cor.id,
            'nome': cor.nome,
            'chave': cor.chave,
            'codigo_hex': cor.codigo_hex,
        }
        for cor in cores_queryset
    ]
    
    # Dicionário mapeado por chave (ideal para buscar direto pelo nome da chave)
    cores_dict = {
        cor.chave: {
            'nome': cor.nome,
            'codigo_hex': cor.codigo_hex
        }
        for cor in cores_queryset
    }

    # Se a requisição for da API de perfis, devolve JSON direto
    if json_response or request.headers.get('Accept') == 'application/json':
        vinculos = projeto.projetoclienteipd_set.all()
        ipds_json = [
            {
                'ipd_id': vinculo.projeto_ipd.id,
                'ipd_nome': vinculo.projeto_ipd.nome,
                'profiles_usados': vinculo.profiles_usados,
            }
            for vinculo in vinculos
        ]
        return JsonResponse({
            'ipds': ipds_json,
            'cores': cores_lista,     # Lista completa com id, nome, chave e codigo_hex
            'cores_map': cores_dict   # Dicionário indexado pela chave ex: {"primary": {"nome": "Azul", "codigo_hex": "#007bff"}}
        })

    context = {
        'projeto': projeto,
        'cores_lista': cores_lista,  # Para percorrer com {% for cor in cores_lista %}
        'cores': cores_dict,         # Para acessar direto: {{ cores.primary.codigo_hex }} ou {{ cores.primary.nome }}
    }
    return render(request, 'detalhes_projeto.html', context)

from django.views.generic import TemplateView

class HomeView(TemplateView):
    """
    Class-Based View (CBV) para a Landing Page / Home da Quaest.
    Exibe a apresentação do IPD e os acessos ao sistema.
    """
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo_pagina'] = 'Quaest Pesquisa e Consultoria | Inteligência de Dados & IPD'
        return context

    def dispatch(self, request, *args, **kwargs):
        # Opcional: Se o usuário já estiver logado e você quiser redirecioná-lo direto para o relatório
        # if request.user.is_authenticated:
        #     return redirect('relatorio_ipd')
        return super().dispatch(request, *args, **kwargs)



from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProjetoCliente

class MeusProjetosView(LoginRequiredMixin, ListView):
    model = ProjetoCliente
    template_name = 'meus_projetos.html'
    context_object_name = 'projetos'
    login_url = '/'

    def get_queryset(self):
        return ProjetoCliente.objects.filter(
            usuarios_autorizados__user=self.request.user
        ).distinct().prefetch_related('projetoclienteipd_set__projeto_ipd')