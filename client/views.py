from django.shortcuts import render, get_object_or_404,redirect
from django.http import Http404
from .models import ProjetoIPD, ProjetoCliente
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib import messages
# Exemplo se você tiver um Model no Django:
# from .models import Projeto

@login_required(login_url='home')
def detalhe_projeto_view(request, slug):
    """
    View responsável por renderizar a página e o relatório do projeto.
    Garante que APENAS usuários autorizados consigam visualizar o conteúdo.
    """
    # 1. Busca o projeto pelo slug
    projeto = get_object_or_404(ProjetoCliente, slug=slug)
    
    # 2. VERIFICAÇÃO DE SEGURANÇA: O usuário logado possui permissão para este projeto?
    tem_permissao = (
        request.user.is_superuser or 
        request.user.is_staff or 
        projeto.usuarios_autorizados.filter(user=request.user).exists()
    )
    
    # 3. Se NÃO tiver acesso, bloqueia a exibição
    if not tem_permissao:
        messages.error(request, 'Você não possui permissão para acessar este projeto.')
        # Redireciona de volta para a lista de projetos permitidos
        return redirect('meus_projetos')
        
        # OU se preferir exibir uma página padrão do Django de "Acesso Negado (403)":
        # raise PermissionDenied("Acesso não autorizado a este projeto.")

    # 4. Se tiver acesso, renderiza o template normalmente
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
    """
    Lista os Projetos Clientes aos quais o usuário logado possui permissão,
    utilizando a model PermissoesUsuario.
    """
    model = ProjetoCliente
    template_name = 'meus_projetos.html'
    context_object_name = 'projetos'
    login_url = '/'  # Redireciona para a Home se estiver deslogado

    def get_queryset(self):
        # O filtro acessa a relação 'usuarios_autorizados' (PermissoesUsuario)
        # buscando onde o usuário da permissão é o usuário logado
        return ProjetoCliente.objects.filter(
            usuarios_autorizados__user=self.request.user
        ).distinct().prefetch_related('projetos_ipd')