from django.contrib import admin
from .models import ProjetoIPD, ProjetoCliente





@admin.register(ProjetoCliente)
class ProjetoClienteAdmin(admin.ModelAdmin):
    list_display = ('id','nome', 'cliente', 'descricao', 'tipo_ipd')
    prepopulated_fields = {'slug': ('nome',)}
    search_fields = ('nome', 'cliente')
    filter_horizontal = ('projetos_ipd',)


@admin.register(ProjetoIPD)
class ProjetoIPDAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome')
    search_fields = ('nome',)