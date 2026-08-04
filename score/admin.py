import hashlib
from django.contrib import admin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from import_export.admin import ImportExportModelAdmin
from .models import IPD, Conteudo
from client.models import ProjetoIPD, ProjetoCliente

class SmartForeignKeyWidget(ForeignKeyWidget):
    """Widget que aceita tanto o ID numérico quanto o Nome do projeto."""
    def get_queryset(self, value, row, *args, **kwargs):
        if str(value).isdigit():
            return self.model.objects.filter(pk=int(value))
        return self.model.objects.filter(nome__iexact=str(value).strip())


class IPDResource(resources.ModelResource):
    projeto_ipd = fields.Field(
        column_name='projeto_ipd',
        attribute='projeto_ipd',
        widget=SmartForeignKeyWidget(ProjetoIPD, field='id')
    )
    projeto_cliente = fields.Field(
        column_name='projeto_cliente',
        attribute='projeto_cliente',
        widget=SmartForeignKeyWidget(ProjetoCliente, field='id')
    )

    class Meta:
        model = IPD
        skip_unchanged = False
        report_skipped = True
        
        # IGNORA automaticamente qualquer coluna a mais presente na planilha
        ignore_unknown_fields = True
        
        # Mapeia apenas as colunas oficiais que o model IPD utiliza
        fields = (
            'projeto_cliente',
            'projeto_ipd',
            'profile',
            'fama',
            'engaj',
            'valencia',
            'mob',
            'interesse',
            'ipd',
            'data',
        )

    def before_import_row(self, row, **kwargs):
        """Prepara e traduz mapeamentos alternativos de colunas antes de importar."""
        # Se a planilha tiver a coluna 'pd', mapeia para 'projeto_ipd'
        if 'pd' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['pd']
            
        if 'projeto_ipd_id' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['projeto_ipd_id']
            
        if 'projeto_cliente_id' in row and ('projeto_cliente' not in row or not row['projeto_cliente']):
            row['projeto_cliente'] = row['projeto_cliente_id']

    def get_instance(self, instance_loader, row):
        """Localiza o registro para atualizar em vez de duplicar."""
        proj_ipd_val = row.get('projeto_ipd') or row.get('pd')
        profile = row.get('profile')
        data = row.get('data')

        if proj_ipd_val and profile and data:
            if str(proj_ipd_val).isdigit():
                proj_ipd = ProjetoIPD.objects.filter(pk=int(proj_ipd_val)).first()
            else:
                proj_ipd = ProjetoIPD.objects.filter(nome__iexact=str(proj_ipd_val).strip()).first()

            if proj_ipd:
                return IPD.objects.filter(
                    projeto_ipd=proj_ipd,
                    profile=profile,
                    data=data
                ).first()
        return None

@admin.register(IPD)
class IPDAdmin(ImportExportModelAdmin):
    resource_classes = [IPDResource]
    list_display = ('profile', 'data', 'ipd', 'projeto_ipd', 'projeto_cliente', 'data_registro')
    list_filter = ('projeto_ipd', 'projeto_cliente', 'data')
    search_fields = ('profile', 'hash_indice')
    readonly_fields = ('hash_indice', 'data_registro')
    ordering = ('-data',)


@admin.register(Conteudo)
class ConteudoAdmin(admin.ModelAdmin):
    list_display = ('id_post', 'titulo', 'data', 'projeto_cliente', 'projeto_ipd')
    list_filter = ('projeto_cliente', 'projeto_ipd', 'data')
    search_fields = ('id_post', 'titulo', 'texto')
    filter_horizontal = ('ipds',)