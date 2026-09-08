from django.shortcuts import render, get_object_or_404,redirect
from .models import ProjetoIPD, ProjetoCliente
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.contrib import messages
# Exemplo se você tiver um Model no Django:
# from .models import Projeto

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
        return JsonResponse({'ipds': ipds_json})

    context = {
        'projeto': projeto,
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