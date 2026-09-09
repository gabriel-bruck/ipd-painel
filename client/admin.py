from django import forms
from django.urls import path
from django.template import engines
from django.template.response import TemplateResponse
from django.shortcuts import redirect
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.utils.safestring import mark_safe

from .models import ProjetoIPD, ProjetoCliente, ProjetoClienteIPD


# =============================================================================
# INLINES E CLIENTE
# =============================================================================

class ProjetoClienteIPDInline(admin.TabularInline):
    model = ProjetoClienteIPD
    extra = 1
    verbose_name = "Projeto IPD e Perfis"
    verbose_name_plural = "Projetos IPD Vinculados"


@admin.register(ProjetoCliente)
class ProjetoClienteAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome', 'cliente', 'descricao', 'get_tipo_ipd')
    prepopulated_fields = {'slug': ('nome',)}
    search_fields = ('nome', 'cliente')
    inlines = [ProjetoClienteIPDInline]

    @admin.display(description='Tipo IPD')
    def get_tipo_ipd(self, obj):
        return obj.get_tipo_ipd_display()

    # Apaga o cache do projeto assim que os perfis do Inline são salvos
    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        cache.delete(f"projeto_profiles:v2:projeto:{form.instance.id}")


# =============================================================================
# CRIAÇÃO EM LOTE - PROJETO IPD
# =============================================================================

class ProjetoIPDLoteForm(forms.Form):
    nomes = forms.CharField(
        label='Nomes dos Projetos (um por linha)',
        widget=forms.Textarea(attrs={'rows': 15, 'cols': 80, 'placeholder': 'Ex:\nProjeto A\nProjeto B\nProjeto C'}),
        required=True,
        help_text="Insira os nomes dos projetos que deseja criar, um por linha. Projetos com nomes idênticos aos que já existem não serão duplicados."
    )


# Templates injetados dinamicamente para não precisar criar arquivos HTML
LISTA_PROJETO_IPD_TEMPLATE = '''{% extends "admin/change_list.html" %}
{% block object-tools-items %}
<li><a href="adicionar-lote/" class="addlink">Adicionar Projetos em Lote</a></li>
{{ block.super }}
{% endblock %}'''

FORM_LOTE_TEMPLATE = '''{% extends "admin/base_site.html" %}
{% block content %}
<style>
    #loading-overlay {
        display: none;
        position: fixed;
        top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(0, 0, 0, 0.6);
        z-index: 9999;
        text-align: center;
        color: #ffffff;
        font-family: sans-serif;
    }
    .spinner {
        border: 8px solid rgba(255,255,255, 0.3);
        border-top: 8px solid #ffffff;
        border-radius: 50%;
        width: 60px;
        height: 60px;
        animation: spin 1s linear infinite;
        margin: 25vh auto 20px auto;
    }
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
</style>

<div id="loading-overlay">
    <div class="spinner"></div>
    <h2>Processando, por favor aguarde...</h2>
    <p>Criando os projetos e gerando os IDs no banco de dados.</p>
</div>

<p><a href="../">&lsaquo; Voltar para a lista de Projetos IPD</a></p>
<h1>Adicionar Projetos IPD em Lote</h1>

<form method="post" id="lote-form">
    {% csrf_token %}
    {{ form.as_p }}
    <div class="submit-row" style="text-align: left;">
        <input type="submit" value="Criar Projetos" class="default" id="submit-btn">
    </div>
</form>

<script>
    document.getElementById('lote-form').addEventListener('submit', function() {
        document.getElementById('loading-overlay').style.display = 'block';
        var btn = document.getElementById('submit-btn');
        btn.style.pointerEvents = 'none';
        btn.style.opacity = '0.6';
        btn.value = 'Processando...';
    });
</script>
{% endblock %}'''


@admin.register(ProjetoIPD)
class ProjetoIPDAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome')
    search_fields = ('nome',)

    def get_urls(self):
        """Registra a URL customizada para a página de importação em lote."""
        urls = super().get_urls()
        custom_urls = [
            path(
                'adicionar-lote/',
                self.admin_site.admin_view(self.adicionar_lote_view),
                name=f'{self.model._meta.app_label}_{self.model._meta.model_name}_lote'
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        """Injeta o botão 'Adicionar Projetos em Lote' no topo da listagem."""
        response = super().changelist_view(request, extra_context)
        if hasattr(response, 'template_name'):
            response.template_name = engines['django'].from_string(LISTA_PROJETO_IPD_TEMPLATE)
        return response

    def adicionar_lote_view(self, request):
        """View responsável por exibir o formulário e processar a criação."""
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = ProjetoIPDLoteForm(request.POST or None)

        if request.method == 'POST' and form.is_valid():
            nomes_raw = form.cleaned_data['nomes']
            nomes = [n.strip() for n in nomes_raw.split('\n') if n.strip()]

            criados = []
            existentes = []

            for nome in nomes:
                obj, created = ProjetoIPD.objects.get_or_create(nome=nome)
                if created:
                    criados.append(obj)
                else:
                    existentes.append(obj)

            msg_partes = []
            if criados:
                lista_criados = " | ".join([f"{p.nome} (ID: {p.id})" for p in criados])
                msg_partes.append(f"<b>{len(criados)} CRIADOS:</b> {lista_criados}.")
            
            if existentes:
                lista_existentes = " | ".join([f"{p.nome} (ID: {p.id})" for p in existentes])
                msg_partes.append(f"<b>{len(existentes)} JÁ EXISTIAM:</b> {lista_existentes}.")

            if msg_partes:
                self.message_user(request, mark_safe("<br><br>".join(msg_partes)), messages.SUCCESS)
            else:
                self.message_user(request, "Nenhum nome válido foi inserido.", messages.WARNING)

            return redirect(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist')

        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'opts': self.model._meta,
            'title': 'Adicionar Projetos IPD em Lote',
        }
        
        return TemplateResponse(
            request,
            engines['django'].from_string(FORM_LOTE_TEMPLATE),
            context
        )