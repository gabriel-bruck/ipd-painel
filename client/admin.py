from django.contrib import admin
from .models import ProjetoIPD, ProjetoCliente, ProjetoClienteIPD

# 1. Criamos o Inline para o modelo intermediário
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
    
    # O filter_horizontal foi removido e substituído pelos inlines abaixo:
    inlines = [ProjetoClienteIPDInline]

    @admin.display(description='Tipo IPD')
    def get_tipo_ipd(self, obj):
        return obj.get_tipo_ipd_display()

@admin.register(ProjetoIPD)
class ProjetoIPDAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome')
    search_fields = ('nome',)