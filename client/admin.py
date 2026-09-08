from django.contrib import admin
from django.core.cache import cache
from .models import ProjetoIPD, ProjetoCliente, ProjetoClienteIPD

# 1. Inline intermediário
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

    # JEITO SIMPLES: Apaga o cache do projeto assim que os perfis do Inline são salvos
    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        cache.delete(f"projeto_profiles:v2:projeto:{form.instance.id}")

@admin.register(ProjetoIPD)
class ProjetoIPDAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome')
    search_fields = ('nome',)