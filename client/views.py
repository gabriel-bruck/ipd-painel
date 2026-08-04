from django.shortcuts import render, get_object_or_404
from django.http import Http404
from .models import ProjetoIPD, ProjetoCliente
# Exemplo se você tiver um Model no Django:
# from .models import Projeto

def detalhe_projeto_view(request, slug):
   
    # Busca o projeto pelo slug
    projeto = get_object_or_404(ProjetoCliente, slug=slug)

    if not projeto:
        raise Http404("Projeto não encontrado.")

    # Renderiza o template passando a variável 'projeto'
    return render(request, 'detalhes_projeto.html', {'projeto': projeto})




