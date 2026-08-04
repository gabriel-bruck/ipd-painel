from django.contrib import admin

from .models import PermissoesUsuario
# Register your models here.
@admin.register(PermissoesUsuario)
class PermissoesUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user',)
    search_fields = ('user__username', 'user__email')
    filter_horizontal = ('projetos_cliente',)