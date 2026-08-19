import hashlib
from datetime import datetime
from django.contrib import admin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget
from import_export.admin import ImportExportModelAdmin
from .models import IPD, Conteudo
from client.models import ProjetoIPD, ProjetoCliente


class SmartForeignKeyWidget(ForeignKeyWidget):
    """Widget que aceita tanto o ID numérico quanto o Nome do projeto."""
    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return None
        
        val_str = str(value).strip()
        if val_str.isdigit():
            return self.model.objects.filter(pk=int(val_str)).first()
        return self.model.objects.filter(nome__iexact=val_str).first()

class SmartManyToManyWidget(ManyToManyWidget):
    """Widget ManyToMany que aceita IDs ou Nomes separados por vírgula ou ponto e vírgula."""
    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return self.model.objects.none()
        
        raw_values = [v.strip() for v in str(value).replace(';', ',').split(',') if v.strip()]
        pks = []
        names = []

        for val in raw_values:
            if val.isdigit():
                pks.append(int(val))
            else:
                names.append(val)

        qs_pk = self.model.objects.filter(pk__in=pks) if pks else self.model.objects.none()
        qs_name = self.model.objects.filter(nome__in=names) if names else self.model.objects.none()

        return (qs_pk | qs_name).distinct()


# =============================================================================
# RECURSOS E ADMIN DO MODEL IPD
# =============================================================================
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
        ignore_unknown_fields = True
        
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
        if 'pd' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['pd']
            
        if 'projeto_ipd_id' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['projeto_ipd_id']
            
        if 'projeto_cliente_id' in row and ('projeto_cliente' not in row or not row['projeto_cliente']):
            row['projeto_cliente'] = row['projeto_cliente_id']

        if 'perfil' in row and ('profile' not in row or not row['profile']):
            row['profile'] = row['perfil']

        # Tratamento de Data
        data_raw = row.get('data')
        if data_raw:
            if isinstance(data_raw, datetime):
                row['data'] = data_raw.strftime('%Y-%m-%d')
            else:
                data_str = str(data_raw).strip()
                if ' ' in data_str:
                    data_str = data_str.split(' ')[0]
                if '/' in data_str:
                    partes = data_str.split('/')
                    if len(partes) == 3:
                        data_str = f"{partes[2]}-{partes[1]}-{partes[0]}"
                row['data'] = data_str

    def get_instance(self, instance_loader, row):
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
                    profile=str(profile).strip(),
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


# =============================================================================
# RECURSOS E ADMIN DO MODEL CONTEUDO (POSTS)
# =============================================================================
class ConteudoResource(resources.ModelResource):
    projeto_ipd = fields.Field(
        column_name='projeto_ipd',
        attribute='projeto_ipd',
        widget=SmartManyToManyWidget(ProjetoIPD, field='id')
    )
    
    projeto_cliente = fields.Field(
        column_name='projeto_cliente',
        attribute='projeto_cliente',
        widget=SmartForeignKeyWidget(ProjetoCliente, field='id')
    ) if hasattr(Conteudo, 'projeto_cliente') else None

    class Meta:
        model = Conteudo
        skip_unchanged = False
        report_skipped = True
        ignore_unknown_fields = True
        import_id_fields = ('id_post',)  # Chave primária de identificação da importação
        
        fields = (
            'id_post',
            'projeto_ipd',
            'profile',
            'texto',
            'link_post',
            'curtidas',
            'comentarios',
            'data',
            'categoria_tema',
        )

    def before_import(self, dataset, **kwargs):
        super().before_import(dataset, **kwargs)
        self.seen_ids = set()

    def before_import_row(self, row, **kwargs):
        """Prepara e traduz mapeamentos alternativos de colunas antes de importar."""

        if 'pd' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['pd']
            
        if 'projeto_ipd_id' in row and ('projeto_ipd' not in row or not row['projeto_ipd']):
            row['projeto_ipd'] = row['projeto_ipd_id']

        if 'id' in row and ('id_post' not in row or not row['id_post']):
            row['id_post'] = row['id']
            
        if 'url' in row and ('link_post' not in row or not row['link_post']):
            row['link_post'] = row['url']

        if 'link' in row and ('link_post' not in row or not row['link_post']):
            row['link_post'] = row['link']

        if 'perfil' in row and ('profile' not in row or not row['profile']):
            row['profile'] = row['perfil']

        if 'likes' in row and ('curtidas' not in row or not row['curtidas']):
            row['curtidas'] = row['likes']

        # Tratamento da Data
        data_raw = row.get('data')
        if data_raw:
            if isinstance(data_raw, datetime):
                row['data'] = data_raw.strftime('%Y-%m-%d')
            else:
                data_str = str(data_raw).strip()
                if ' ' in data_str:
                    data_str = data_str.split(' ')[0]
                if '/' in data_str:
                    partes = data_str.split('/')
                    if len(partes) == 3:
                        data_str = f"{partes[2]}-{partes[1]}-{partes[0]}"
                row['data'] = data_str

        # Tratamento e Limpeza do Link/URL
        link_raw = row.get('link_post') or row.get('url') or row.get('link')
        if link_raw:
            link_limpo = str(link_raw).strip().replace('\n', '').replace('\r', '')
            if link_limpo and not link_limpo.startswith(('http://', 'https://')):
                link_limpo = f"https://{link_limpo}"
            row['link_post'] = link_limpo

    def get_instance(self, instance_loader, row):
        id_post_val = str(row.get('id_post') or '').strip()

        if id_post_val:
            instance = Conteudo.objects.filter(id_post=id_post_val).first()
            if instance:
                return instance

            link_val = str(row.get('link_post') or '').strip()
            profile_val = str(row.get('profile') or '').strip()
            if link_val and profile_val:
                instance_link = Conteudo.objects.filter(link_post=link_val, profile=profile_val).first()
                if instance_link:
                    return instance_link

        return None

    def save_m2m(self, obj, data, using_historical_record, dry_run):
        """
        Sobrescreve a sincronização M2M para ACUMULAR novos projetos.
        Atualiza todos os dados do Post normalmente, mas junta os projetos antigos com os novos.
        """
        # 1. Salva em memória os projetos que o post JÁ TINHA antes da importação
        projetos_antigos = list(obj.projeto_ipd.all()) if obj.pk else []

        # 2. Chama o método original. Ele cuida de ler a planilha e salvar o NOVO projeto (ex: 2).
        # (Neste momento, por baixo dos panos, a biblioteca sobrescreve o antigo)
        super().save_m2m(obj, data, using_historical_record, dry_run)

        # 3. Readiciona os projetos antigos junto com o novo que acabou de ser salvo (ex: junta 1 e 2).
        # Fazemos isso apenas se não for dry_run (pré-visualização), para gravar de fato no banco.
        if not dry_run and projetos_antigos:
            obj.projeto_ipd.add(*projetos_antigos)
    def after_import_row(self, row, row_result, **kwargs):
        super().after_import_row(row, row_result, **kwargs)
        id_post_val = str(row.get('id_post') or '').strip()
        if id_post_val:
            self.seen_ids.add(id_post_val)


@admin.register(Conteudo)
class ConteudoAdmin(ImportExportModelAdmin):
    resource_classes = [ConteudoResource]
    list_display = ('id_post', 'data', 'exibir_projetos_ipd')
    list_filter = ('data', 'projeto_ipd')
    search_fields = ('id_post', 'texto', 'profile')
    filter_horizontal = ('projeto_ipd',)

    autocomplete_fields = ['projeto_cliente'] if hasattr(Conteudo, 'projeto_cliente') else []

    @admin.display(description='Projetos IPD Vinculados')
    def exibir_projetos_ipd(self, obj):
        projetos = obj.projeto_ipd.all()
        if not projetos:
            return "-"
        return ", ".join([p.nome for p in projetos])