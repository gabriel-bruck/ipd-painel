from datetime import datetime
import hashlib
from django.contrib import admin
from django.urls import path
from django.http import JsonResponse
from django.core.cache import cache

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin

from .models import IPD, Conteudo, ResumoExecutivo
from client.models import ProjetoIPD, ProjetoCliente
from django.contrib.admin.views.main import ChangeList


from import_export.widgets import (
    ForeignKeyWidget,
    ManyToManyWidget,
    IntegerWidget,
)
class SmartForeignKeyWidget(ForeignKeyWidget):
    """
    Aceita tanto ID numérico quanto nome do projeto.
    """

    def clean(self, value, row=None, *args, **kwargs):

        if not value:
            return None

        val_str = str(value).strip()

        if val_str.isdigit():
            return self.model.objects.filter(
                pk=int(val_str)
            ).first()

        return self.model.objects.filter(
            nome__iexact=val_str
        ).first()


class SmartManyToManyWidget(ManyToManyWidget):
    """
    Aceita IDs ou nomes separados por vírgula ou ponto e vírgula.

    Exemplos:

        1
        1,2
        1;2
        Streaming
        Streaming,Itaú
    """

    def clean(self, value, row=None, *args, **kwargs):

        if not value:
            return self.model.objects.none()

        raw_values = [
            valor.strip()
            for valor in str(value)
            .replace(';', ',')
            .split(',')
            if valor.strip()
        ]

        pks = []
        names = []

        for valor in raw_values:

            if valor.isdigit():
                pks.append(
                    int(valor)
                )

            else:
                names.append(
                    valor
                )

        qs_pk = (
            self.model.objects.filter(
                pk__in=pks
            )
            if pks
            else self.model.objects.none()
        )

        qs_name = (
            self.model.objects.filter(
                nome__in=names
            )
            if names
            else self.model.objects.none()
        )

        return (
            qs_pk |
            qs_name
        ).distinct()


# =============================================================================
# FUNÇÃO AUXILIAR DE DATA
# =============================================================================

def normalizar_data_importacao(valor):
    """
    Normaliza datas vindas de CSV/Excel.

    Aceita, por exemplo:

        datetime
        2026-09-03
        2026-09-03 00:00:00
        03/09/2026

    Retorna:

        2026-09-03
    """

    if not valor:
        return valor

    if isinstance(valor, datetime):
        return valor.strftime(
            '%Y-%m-%d'
        )

    data_str = str(
        valor
    ).strip()

    if ' ' in data_str:
        data_str = data_str.split(
            ' '
        )[0]

    if '/' in data_str:

        partes = data_str.split(
            '/'
        )

        if len(partes) == 3:

            data_str = (
                f"{partes[2]}-"
                f"{partes[1]}-"
                f"{partes[0]}"
            )

    return data_str


# =============================================================================
# IPD
# =============================================================================
# =============================================================================
# IPD
# =============================================================================

class IPDResource(resources.ModelResource):

    # =========================================================================
    # CAMPOS
    # =========================================================================

    # Hash é interno.
    # Não precisa existir no Excel.
    hash_indice = fields.Field(
        column_name='hash_indice',
        attribute='hash_indice',
        readonly=True,
    )

    # O arquivo sempre contém o ID real do ProjetoIPD.
    projeto_ipd = fields.Field(
    column_name='projeto_ipd',
    attribute='projeto_ipd_id',
    widget=IntegerWidget(),
)


    class Meta:
        model = IPD

        fields = (
            'hash_indice',
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

        # Chave lógica da medição.
        import_id_fields = (
            'projeto_ipd',
            'profile',
            'data',
        )

        ignore_unknown_fields = True

        # Importação em lote.
        use_bulk = True
        batch_size = 1000

        skip_diff = True
        skip_unchanged = False
        report_skipped = False
        store_instance = False


    # =========================================================================
    # PROGRESSO
    # =========================================================================

    def _progress_key(self):
        """
        Chave única no Redis por usuário + importação.
        """

        request = getattr(
            self,
            '_progress_request',
            None,
        )

        job_id = getattr(
            self,
            '_progress_job_id',
            None,
        )

        if not request or not job_id:
            return None

        if not getattr(
            request,
            'user',
            None,
        ):
            return None

        return (
            f"ipd_import_progress:"
            f"{request.user.pk}:"
            f"{job_id}"
        )


    def _salvar_progresso(
        self,
        status,
        processados=None,
        percentual=None,
        mensagem=None,
    ):
        """
        Salva o progresso no Redis.

        Falha do Redis não pode derrubar a importação.
        """

        chave = self._progress_key()

        if not chave:
            return

        total = getattr(
            self,
            '_progress_total',
            0,
        )

        if processados is None:
            processados = getattr(
                self,
                '_progress_processados',
                0,
            )

        if percentual is None:

            if total:

                percentual = int(
                    (
                        processados
                        / total
                    )
                    * 100
                )

                # 100% somente depois que
                # super().import_data() terminar.
                percentual = min(
                    percentual,
                    99,
                )

            else:
                percentual = 0

        try:

            cache.set(
                chave,
                {
                    'status': status,
                    'total': total,
                    'processados': processados,
                    'percentual': percentual,
                    'mensagem': mensagem,
                },
                timeout=3600,
            )

        except Exception as exc:

            # Progresso é auxiliar.
            # Redis fora do ar não pode cancelar o import.
            print(
                f"Erro ao salvar progresso "
                f"da importação IPD: {exc}"
            )


    # =========================================================================
    # IMPORTAÇÃO
    # =========================================================================

    def import_data(
        self,
        dataset,
        *args,
        **kwargs
    ):
        """
        Envolve toda a importação para sabermos exatamente
        quando começou, terminou ou deu erro.

        O 100% somente é enviado depois que
        super().import_data() terminou.
        """

        request = kwargs.get(
            'request'
        )

        self._progress_request = request

        self._progress_job_id = None

        if request:

            self._progress_job_id = (
                request.POST.get(
                    'import_job_id'
                )
            )

        self._progress_total = len(
            dataset
        )

        self._progress_processados = 0

        self._salvar_progresso(
            status='processando',
            processados=0,
            percentual=0,
            mensagem=(
                f"Preparando "
                f"{self._progress_total:,} "
                f"registros..."
            ),
        )

        try:

            result = super().import_data(
                dataset,
                *args,
                **kwargs
            )

            # ================================================================
            # IMPORTAÇÃO FINALIZADA
            # ================================================================

            if result.has_errors():

                self._salvar_progresso(
                    status='concluido_com_erros',
                    processados=
                        self._progress_total,
                    percentual=100,
                    mensagem=(
                        "Importação finalizada, "
                        "mas existem registros "
                        "com erro."
                    ),
                )

            else:

                self._salvar_progresso(
                    status='concluido',
                    processados=
                        self._progress_total,
                    percentual=100,
                    mensagem=(
                        f"Importação concluída. "
                        f"{self._progress_total:,} "
                        f"registros processados."
                    ),
                )

            return result

        except Exception as exc:

            self._salvar_progresso(
                status='erro',
                processados=getattr(
                    self,
                    '_progress_processados',
                    0,
                ),
                mensagem=(
                    str(exc)[:500]
                ),
            )

            raise


    # =========================================================================
    # INÍCIO DA IMPORTAÇÃO
    # =========================================================================

    def before_import(
        self,
        dataset,
        **kwargs
    ):

        super().before_import(
            dataset,
            **kwargs
        )

        self.projetos_ipd_alterados = set()

        # ============================================================
        # PRÉ-CARREGA TODOS OS IPDs QUE PODEM EXISTIR
        # ============================================================
        #
        # Em vez de:
        #
        # 15.000 linhas = 15.000 SELECTs
        #
        # fazemos:
        #
        # 15.000 hashes
        #       ↓
        # 1 SELECT hash_indice IN (...)
        #       ↓
        # dict em memória
        #
        # ============================================================

        hashes_arquivo = set()

        for row in dataset.dict:

            projeto_ipd_id = row.get(
                'projeto_ipd'
            )

            profile = row.get(
                'profile'
            )

            data = row.get(
                'data'
            )

            if not (
                projeto_ipd_id
                and profile
                and data
            ):
                continue

            # --------------------------------------------------------
            # ID
            # --------------------------------------------------------

            try:

                projeto_ipd_id = int(
                    float(
                        projeto_ipd_id
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue


            # --------------------------------------------------------
            # PROFILE
            # --------------------------------------------------------

            profile = str(
                profile
            ).strip()


            # --------------------------------------------------------
            # DATA
            # --------------------------------------------------------

            data = (
                normalizar_data_importacao(
                    data
                )
            )

            if not data:
                continue


            # --------------------------------------------------------
            # HASH
            # --------------------------------------------------------

            raw_string = (
                f"{projeto_ipd_id}-"
                f"{profile}-"
                f"{data}"
            )

            hash_indice = (
                hashlib.sha256(
                    raw_string.encode(
                        'utf-8'
                    )
                ).hexdigest()
            )

            hashes_arquivo.add(
                hash_indice
            )


        # ============================================================
        # UMA CONSULTA AO BANCO
        # ============================================================

        if hashes_arquivo:

            self.ipds_existentes = (
                IPD.objects.in_bulk(
                    hashes_arquivo,
                    field_name='hash_indice',
                )
            )

        else:

            self.ipds_existentes = {}

    # =========================================================================
    # PREPARAÇÃO DE CADA LINHA
    # =========================================================================

    def before_import_row(
        self,
        row,
        **kwargs
    ):

        # ============================================================
        # PROJETO IPD
        # ============================================================

        if row.get('projeto_ipd') not in (
            None,
            '',
        ):

            row['projeto_ipd'] = int(
                float(
                    row['projeto_ipd']
                )
            )


        # ============================================================
        # PROFILE
        # ============================================================

        if row.get('profile') is not None:

            row['profile'] = str(
                row['profile']
            ).strip()


        # ============================================================
        # INTERESSE
        # ============================================================

        if (
            'interesse' not in row
            or row.get('interesse') in (
                '',
                None,
            )
        ):

            row['interesse'] = None


        # ============================================================
        # DATA
        # ============================================================

        if row.get('data'):

            row['data'] = (
                normalizar_data_importacao(
                    row['data']
                )
            )

    # =========================================================================
    # DEPOIS DE CADA LINHA
    # =========================================================================

    def get_instance(
        self,
        instance_loader,
        row,
    ):
        """
        Procura o IPD no dict carregado em memória.

        ZERO consulta SQL por linha.
        """

        projeto_ipd_id = row.get(
            'projeto_ipd'
        )

        profile = row.get(
            'profile'
        )

        data = row.get(
            'data'
        )

        if not (
            projeto_ipd_id
            and profile
            and data
        ):
            return None


        try:

            projeto_ipd_id = int(
                float(
                    projeto_ipd_id
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            return None


        profile = str(
            profile
        ).strip()


        data = (
            normalizar_data_importacao(
                data
            )
        )


        raw_string = (
            f"{projeto_ipd_id}-"
            f"{profile}-"
            f"{data}"
        )


        hash_indice = (
            hashlib.sha256(
                raw_string.encode(
                    'utf-8'
                )
            ).hexdigest()
        )


        return (
            self.ipds_existentes.get(
                hash_indice
            )
        )

    def after_import_row(
        self,
        row,
        row_result,
        **kwargs
    ):

        super().after_import_row(
            row,
            row_result,
            **kwargs
        )

        self._progress_processados += 1

        processados = (
            self._progress_processados
        )

        total = (
            self._progress_total
        )

        # Não precisamos escrever no Redis
        # 15 mil vezes.
        #
        # A cada 100 registros é mais que suficiente.
        if (
            processados % 100 == 0
            or processados == total
        ):

            if total:

                percentual = int(
                    (
                        processados
                        / total
                    )
                    * 100
                )

            else:
                percentual = 0

            # Nunca mostra 100 antes da importação
            # realmente terminar.
            percentual = min(
                percentual,
                99,
            )

            if processados >= total:

                mensagem = (
                    "Finalizando gravação "
                    "no banco de dados..."
                )

            else:

                mensagem = (
                    f"Processando "
                    f"{processados:,} de "
                    f"{total:,} registros..."
                )

            self._salvar_progresso(
                status='processando',
                processados=processados,
                percentual=percentual,
                mensagem=mensagem,
            )


    # =========================================================================
    # ANTES DE SALVAR
    # =========================================================================

    def before_save_instance(
        self,
        instance,
        row,
        **kwargs
    ):

        # ================================================================
        # PROFILE
        # ================================================================

        instance.profile = str(
            instance.profile or ''
        ).strip()


        # ================================================================
        # HASH
        # ================================================================

        if (
            instance.projeto_ipd_id
            and instance.profile
            and instance.data
        ):

            raw_string = (
                f"{instance.projeto_ipd_id}-"
                f"{instance.profile}-"
                f"{instance.data.strftime('%Y-%m-%d')}"
            )

            instance.hash_indice = (
                hashlib.sha256(
                    raw_string.encode(
                        'utf-8'
                    )
                ).hexdigest()
            )

            self.projetos_ipd_alterados.add(
                instance.projeto_ipd_id
            )


        # ================================================================
        # COMPATIBILIDADE projeto_cliente
        # ================================================================

        if (
            hasattr(
                instance,
                'projeto_cliente_id'
            )
            and
            not instance.projeto_cliente_id
        ):

            primeiro_cliente = (
                instance
                .projeto_ipd
                .projetos_cliente
                .first()
            )

            if primeiro_cliente:

                instance.projeto_cliente = (
                    primeiro_cliente
                )


        super().before_save_instance(
            instance,
            row,
            **kwargs
        )


    # =========================================================================
    # FINAL
    # =========================================================================

    def after_import(
        self,
        dataset,
        result,
        **kwargs
    ):

        super().after_import(
            dataset,
            result,
            **kwargs
        )

        projetos_ipd_ids = getattr(
            self,
            'projetos_ipd_alterados',
            set(),
        )

        if not projetos_ipd_ids:
            return

        # ================================================================
        # PROJETOS CLIENTE AFETADOS
        # ================================================================

        projetos_cliente_ids = (
            ProjetoIPD.objects
            .filter(
                id__in=
                    projetos_ipd_ids
            )
            .values_list(
                'projetos_cliente__id',
                flat=True,
            )
            .exclude(
                projetos_cliente__id=None
            )
            .distinct()
        )


        # ================================================================
        # INVALIDAÇÃO DOS CACHES
        # ================================================================

        for projeto_id in (
            projetos_cliente_ids
        ):

            cache_key = (
                f"projeto_profiles:"
                f"v1:"
                f"projeto:{projeto_id}"
            )

            try:

                cache.delete(
                    cache_key
                )

            except Exception as exc:

                print(
                    f"Erro ao invalidar cache "
                    f"{cache_key}: {exc}"
                )


# =============================================================================
# CHANGELIST LIMITADO
# =============================================================================

# =============================================================================
# GESTÃO MANUAL: filtros, vínculos e exclusões em lotes
# =============================================================================
from datetime import timedelta, time
from django import forms
from django.contrib import messages
from django.contrib.admin.actions import delete_selected
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, models
from django.http import HttpResponseRedirect
from django.template import engines
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.conf import settings


MODOS_PROJETOS = (
    ('adicionar', 'Adicionar aos projetos atuais'),
    ('substituir', 'Substituir todos os projetos atuais'),
)


class ConteudoAdminForm(forms.ModelForm):
    modo_projetos = forms.ChoiceField(
        label='Como salvar os projetos IPD', choices=MODOS_PROJETOS,
        initial='adicionar',
        help_text='Adicionar preserva vínculos antigos. Substituir mantém apenas os projetos informados.',
    )

    class Meta:
        model = Conteudo
        fields = '__all__'


class GestaoPeriodoForm(forms.Form):
    projeto = forms.IntegerField(label='ID do projeto IPD', min_value=1)
    profile = forms.CharField(label='Profile exato (opcional)', required=False)
    inicio = forms.DateField(label='Data inicial', widget=forms.DateInput(attrs={'type': 'date'}))
    fim = forms.DateField(label='Data final (inclusive)', widget=forms.DateInput(attrs={'type': 'date'}))
    operacao = forms.ChoiceField(label='Operação', choices=(('excluir', 'Excluir registros'),))
    projetos_destino = forms.CharField(
        label='IDs dos projetos de destino', required=False,
        help_text='Para adicionar/substituir vínculos: IDs separados por vírgula.',
    )
    confirmar = forms.BooleanField(required=False, label='Confirmo a operação nos registros deste filtro')

    def __init__(self, *args, conteudo=False, pode_excluir=False, pode_alterar=False, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if pode_excluir:
            choices.append(('excluir', 'Excluir registros (o cadastro do projeto é preservado)'))
        if conteudo and pode_alterar:
            choices.extend(MODOS_PROJETOS)
        self.fields['operacao'].choices = choices
        if not conteudo:
            self.fields.pop('projetos_destino')

    def clean_projeto(self):
        pk = self.cleaned_data['projeto']
        if not ProjetoIPD.objects.filter(pk=pk).exists():
            raise ValidationError('Projeto IPD não encontrado.')
        return pk

    def clean(self):
        data = super().clean()
        if data.get('inicio') and data.get('fim') and data['inicio'] > data['fim']:
            raise ValidationError('A data inicial deve ser menor ou igual à final.')
        if data.get('operacao') in {'adicionar', 'substituir'}:
            raw = data.get('projetos_destino', '').replace(';', ',')
            try:
                ids = sorted({int(v.strip()) for v in raw.split(',') if v.strip()})
            except ValueError:
                raise ValidationError('Informe somente IDs inteiros nos projetos de destino.')
            if not ids or len(ids) > 100:
                raise ValidationError('Informe entre 1 e 100 projetos de destino.')
            if ProjetoIPD.objects.filter(pk__in=ids).count() != len(ids):
                raise ValidationError('Um ou mais projetos de destino não existem.')
            data['destinos'] = ids
        return data


LISTA_GESTAO_TEMPLATE = '''{% extends base_lista %}
{% block object-tools-items %}
<li><a href="{{ gestao_url }}">Gerenciar por projeto / período</a></li>
{{ block.super }}{% endblock %}
{% block search %}
<p>Pesquise para carregar os registros: até 50 por página. Para um projeto e intervalo exato, use “Gerenciar por projeto / período”.</p>
{{ block.super }}{% endblock %}'''

GESTAO_TEMPLATE = '''{% extends "admin/base_site.html" %}
{% block content %}
<p><a href="{{ lista_url }}">Voltar à listagem</a></p>
<p>Informe o ID do projeto IPD e o período. Profile vazio inclui todos os perfis.
Excluir remove os registros inteiros; em Conteúdo isso também remove seus vínculos com outros projetos.</p>
<form method="post">{% csrf_token %}{{ form.as_p }}
<button type="submit" name="acao" value="consultar">Consultar</button>
<button type="submit" name="acao" value="executar">Executar operação</button></form>
{% if total != None %}<p><strong>{{ total }} registros encontrados.</strong> Exibindo no máximo 50.</p>
<table><thead><tr><th>Registro</th><th>Profile</th><th>Data</th></tr></thead><tbody>
{% for item in amostra %}<tr><td><a href="{{ item.url }}">{{ item.pk }}</a></td><td>{{ item.profile }}</td><td>{{ item.data }}</td></tr>{% endfor %}
</tbody></table>{% endif %}{% endblock %}'''


class GestaoAdminMixin:
    actions = ['excluir_selecionados_limitados']
    if hasattr(admin, 'ShowFacets'):
        show_facets = admin.ShowFacets.NEVER

    def get_urls(self):
        opts = self.model._meta
        return [path('gestao-periodo/', self.admin_site.admin_view(self.gestao_periodo),
                     name=f'{opts.app_label}_{opts.model_name}_gestao_periodo')] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        opts = self.model._meta
        context = dict(extra_context or {})
        context['gestao_url'] = reverse(
            f'{self.admin_site.name}:{opts.app_label}_{opts.model_name}_gestao_periodo')
        context['base_lista'] = self.change_list_template or 'admin/change_list.html'
        response = super().changelist_view(request, extra_context=context)
        if isinstance(response, TemplateResponse):
            response.template_name = engines['django'].from_string(LISTA_GESTAO_TEMPLATE)
        return response

    @admin.action(description='Excluir itens selecionados (até 50)', permissions=['delete'])
    def excluir_selecionados_limitados(self, request, queryset):
        if queryset.count() > 50:
            self.message_user(request, 'Para excluir mais de 50 registros, use Gerenciar por projeto / período.', messages.ERROR)
            return
        return delete_selected(self, request, queryset)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('excluir_selecionados_limitados', None)
        if self.has_delete_permission(request):
            actions['delete_selected'] = (type(self).excluir_selecionados_limitados, 'delete_selected', 'Excluir itens selecionados (até 50)')
        else:
            actions.pop('delete_selected', None)
        return actions

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self.model is IPD:
            qs = qs.select_related('projeto_ipd')
        # Não carregar textos grandes na listagem; formulários individuais continuam completos.
        if self.model is Conteudo and request.resolver_match and request.resolver_match.url_name.endswith('_changelist'):
            qs = qs.defer('texto')
        return qs

    def _invalidar_cache_ipd(self, queryset):
        if self.model is not IPD:
            return
        projetos = queryset.order_by().values('projeto_ipd_id')
        clientes = ProjetoIPD.objects.filter(pk__in=projetos).values_list(
            'projetos_cliente__pk', flat=True).distinct()
        chaves = [f'projeto_profiles:v1:projeto:{pk}' for pk in clientes if pk is not None]
        def limpar():
            try:
                cache.delete_many(chaves)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Falha ao invalidar cache de profiles IPD')
        transaction.on_commit(limpar, using=queryset.db)

    def delete_queryset(self, request, queryset):
        self._invalidar_cache_ipd(queryset)
        return super().delete_queryset(request, queryset)

    def delete_model(self, request, obj):
        self._invalidar_cache_ipd(self.get_queryset(request).filter(pk=obj.pk))
        return super().delete_model(request, obj)

    def _filtrar_periodo(self, request, data):
        qs = self.get_queryset(request).filter(projeto_ipd__pk=data['projeto'])
        if data.get('profile'):
            qs = qs.filter(profile=data['profile'])
        if isinstance(self.model._meta.get_field('data'), models.DateTimeField):
            inicio = datetime.combine(data['inicio'], time.min)
            fim = datetime.combine(data['fim'] + timedelta(days=1), time.min)
            if settings.USE_TZ:
                inicio = timezone.make_aware(inicio)
                fim = timezone.make_aware(fim)
            qs = qs.filter(data__gte=inicio, data__lt=fim)
        else:
            qs = qs.filter(data__gte=data['inicio'], data__lte=data['fim'])
        return qs.order_by().distinct()

    def _executar_periodo(self, request, queryset, data):
        operacao = data['operacao']
        if operacao == 'excluir' and not self.has_delete_permission(request):
            raise PermissionDenied
        if operacao != 'excluir' and not self.has_change_permission(request):
            raise PermissionDenied
        # Transação única: erro/PROTECT/permissão cancela toda esta execução.
        # Apenas 200 registros e seus relacionamentos por lote em Python.
        total = 0
        with transaction.atomic(using=queryset.db):
            # Cursor por PK evita OFFSET e continua funcionando se os vínculos mudarem.
            ultima_pk = None
            while True:
                pagina = queryset if ultima_pk is None else queryset.filter(pk__gt=ultima_pk)
                ids = list(pagina.order_by('pk').values_list('pk', flat=True)[:200])
                if not ids:
                    break
                ultima_pk = ids[-1]
                lote = self.get_queryset(request).filter(pk__in=ids).order_by('pk')
                objetos = list(lote.select_for_update())
                if operacao == 'excluir':
                    for obj in objetos:
                        if not self.has_delete_permission(request, obj):
                            raise PermissionDenied
                    _, _, permissoes, protegidos = self.get_deleted_objects(objetos, request)
                    if permissoes:
                        raise PermissionDenied
                    if protegidos:
                        raise ValidationError('Há registros relacionados protegidos. Nenhum item desta execução foi excluído.')
                    # Django 5 registra exclusões por queryset.
                    self.log_deletions(request, lote)
                    self.delete_queryset(request, lote)
                else:
                    for obj in objetos:
                        if not self.has_change_permission(request, obj):
                            raise PermissionDenied
                        if operacao == 'adicionar':
                            obj.projeto_ipd.add(*data['destinos'])
                        else:
                            obj.projeto_ipd.set(data['destinos'])
                        self.log_change(request, obj, f"Projetos IPD: {operacao} {data['destinos']}")
                total += len(objetos)
        return total

    def gestao_periodo(self, request):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        opts = self.model._meta
        lista_url = reverse(f'{self.admin_site.name}:{opts.app_label}_{opts.model_name}_changelist')
        form = GestaoPeriodoForm(
            request.POST if request.method == 'POST' else None,
            conteudo=self.model is Conteudo,
            pode_excluir=self.has_delete_permission(request),
            pode_alterar=self.has_change_permission(request),
        )
        total, amostra = None, []
        if form.is_valid():
            qs = self._filtrar_periodo(request, form.cleaned_data)
            if request.POST.get('acao') == 'executar':
                if not form.cleaned_data['confirmar']:
                    form.add_error('confirmar', 'Marque a confirmação para executar.')
                else:
                    try:
                        quantidade = self._executar_periodo(request, qs, form.cleaned_data)
                    except ValidationError as exc:
                        form.add_error(None, exc)
                    else:
                        self.message_user(request, f'Operação concluída em {quantidade} registros.', messages.SUCCESS)
                        return HttpResponseRedirect(request.path)
            total = qs.count()
            for item in qs.order_by('-data', 'pk').values('pk', 'profile', 'data')[:50]:
                from django.contrib.admin.utils import quote
                item['url'] = reverse(f'{self.admin_site.name}:{opts.app_label}_{opts.model_name}_change', args=[quote(item['pk'])])
                amostra.append(item)
        request.current_app = self.admin_site.name
        return TemplateResponse(request, engines['django'].from_string(GESTAO_TEMPLATE), {
            **self.admin_site.each_context(request), 'opts': opts,
            'title': f'Gerenciar {opts.verbose_name_plural} por período',
            'form': form, 'total': total, 'amostra': amostra, 'lista_url': lista_url,
        })


class LimitedAdminChangeList(ChangeList):
    """Página inicial vazia; paginação normal apenas depois de pesquisar."""
    def get_queryset(self, request, **kwargs):
        qs = super().get_queryset(request, **kwargs)
        filtros = {k: v for k, v in request.GET.items()
                   if k not in {'p', 'o', 'all', '_facets', '_popup', '_to_field'} and v}
        return qs if filtros else qs.none()



# =============================================================================
# ADMIN IPD
# =============================================================================

@admin.register(IPD)
class IPDAdmin(GestaoAdminMixin, ImportExportModelAdmin):

    resource_classes = [
        IPDResource
    ]

    skip_import_confirm = True

    # Template com barra de progresso.
    import_template_name = "ipd_import.html"

    # =========================================================================
    # URL DO PROGRESSO
    # =========================================================================

    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [
            path(
                'import-progress/',
                self.admin_site.admin_view(
                    self.import_progress
                ),
                name=
                    'score_ipd_import_progress',
            ),
        ]

        return custom_urls + urls


    # =========================================================================
    # API DO PROGRESSO
    # =========================================================================

    def import_progress(
        self,
        request
    ):

        job_id = request.GET.get(
            'job_id'
        )

        if not job_id:

            response = JsonResponse({
                'status': 'aguardando',
                'percentual': 0,
                'processados': 0,
                'total': 0,
                'mensagem':
                    'Aguardando importação...',
            })

            response[
                'Cache-Control'
            ] = 'no-store'

            return response


        chave = (
            f"ipd_import_progress:"
            f"{request.user.pk}:"
            f"{job_id}"
        )

        try:

            progresso = cache.get(
                chave
            )

        except Exception:

            progresso = None


        if progresso is None:

            progresso = {
                'status': 'aguardando',
                'percentual': 0,
                'processados': 0,
                'total': 0,
                'mensagem':
                    'Enviando e preparando arquivo...',
            }


        response = JsonResponse(
            progresso
        )

        response[
            'Cache-Control'
        ] = (
            'no-store, no-cache, '
            'must-revalidate, max-age=0'
        )

        return response


    # =========================================================================
    # CHANGELIST
    # =========================================================================

    def get_changelist(
        self,
        request,
        **kwargs
    ):
        return LimitedAdminChangeList


    # =========================================================================
    # EXCLUSÃO
    # =========================================================================



    actions = ['excluir_selecionados_limitados']


    # =========================================================================
    # LISTAGEM
    # =========================================================================

    list_display = (
        'profile',
        'data',
        'ipd',
        'projeto_ipd',
    )


    list_filter = (
        'projeto_ipd',
        'data',
    )


    search_fields = (
        'profile',
    )


    ordering = (
        '-data',
    )


    readonly_fields = (
        'hash_indice',
        'data_registro',
    )


    list_per_page = 50
    list_max_show_all = 0
    show_full_result_count = False


    autocomplete_fields = (
        'projeto_ipd',
    )
# =============================================================================
# CONTEÚDO
# =============================================================================

from decimal import Decimal, InvalidOperation
import re
from import_export.forms import ImportForm, ConfirmImportForm


def normalizar_id_conteudo(valor):
    """Expande notação científica sem passar textos por float.

    Retorna (id textual, risco de precisão). Mantém IDs alfanuméricos
    e zeros à esquerda em identificadores que já vieram como texto.
    Não recupera dígitos que a origem já arredondou.
    """
    if valor is None or isinstance(valor, bool):
        raise ValueError('id_post vazio ou inválido.')
    texto = str(valor).strip()
    if not texto:
        raise ValueError('id_post vazio.')
    if isinstance(valor, str) and re.fullmatch(r'[0-9]+', texto):
        normalizado = texto
    elif isinstance(valor, (int, float, Decimal)) or re.fullmatch(
        r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', texto
    ):
        try:
            numero = Decimal(texto)
        except InvalidOperation:
            raise ValueError('id_post numérico inválido.')
        if not numero.is_finite() or numero < 0 or numero != numero.to_integral_value():
            raise ValueError('id_post numérico deve ser inteiro e não negativo.')
        if numero.adjusted() >= 255:
            raise ValueError('id_post excede 255 caracteres.')
        normalizado = format(numero.quantize(Decimal(1)) if numero.adjusted() < 25 else numero, 'f').split('.')[0]
    else:
        normalizado = texto
    if len(normalizado) > 255:
        raise ValueError('id_post excede 255 caracteres.')
    risco = isinstance(valor, float) and len(normalizado.lstrip('0')) > 15
    return normalizado, risco


def normalizar_projetos_conteudo(valor):
    if valor is None or str(valor).strip() == '':
        return ()
    valores = str(valor).replace(';', ',').split(',') if isinstance(valor, str) else [valor]
    ids = set()
    for item in valores:
        if not str(item).strip():
            continue
        try:
            numero = Decimal(str(item).strip())
        except InvalidOperation:
            raise ValueError('projeto_ipd deve conter IDs numéricos separados por vírgula ou ponto e vírgula.')
        if not numero.is_finite() or numero <= 0 or numero != numero.to_integral_value() or numero > 9223372036854775807:
            raise ValueError('ID de projeto IPD inválido.')
        ids.add(int(numero))
    return tuple(sorted(ids))


class ConteudoImportForm(ImportForm):
    modo_projetos = forms.ChoiceField(
        label='Projetos IPD dos conteúdos importados', choices=MODOS_PROJETOS,
        initial='adicionar', widget=forms.RadioSelect,
        help_text='Adicionar preserva os vínculos atuais. Substituir mantém somente os IDs da coluna projeto_ipd para cada post do arquivo. Coluna vazia preserva os vínculos nos dois modos.',
    )


class ConteudoConfirmImportForm(ConfirmImportForm):
    modo_projetos = forms.ChoiceField(choices=MODOS_PROJETOS, widget=forms.HiddenInput)


class ConteudoResource(resources.ModelResource):
    # Defaults explícitos: célula vazia vira zero; zero informado continua zero.
    curtidas = fields.Field(
        column_name='curtidas', attribute='curtidas', widget=IntegerWidget(), default=0,
    )
    comentarios = fields.Field(
        column_name='comentarios', attribute='comentarios', widget=IntegerWidget(), default=0,
    )
    projeto_ipd = fields.Field(column_name='projeto_ipd', readonly=True)

    class Meta:
        model = Conteudo
        import_id_fields = ('id_post',)
        fields = ('id_post', 'projeto_ipd', 'profile', 'texto', 'link_post',
                  'curtidas', 'comentarios', 'data', 'categoria_tema')
        ignore_unknown_fields = True
        use_bulk = True
        batch_size = 1000
        use_transactions = True
        skip_diff = True
        skip_unchanged = False
        report_skipped = False
        store_instance = False

    def _progress_key(self):

        request = getattr(
            self,
            '_progress_request',
            None,
        )

        job_id = getattr(
            self,
            '_progress_job_id',
            None,
        )

        if not request or not job_id:
            return None

        if not getattr(
            request,
            'user',
            None,
        ):
            return None

        return (
            f"conteudo_import_progress:"
            f"{request.user.pk}:"
            f"{job_id}"
        )

    def _salvar_progresso(
        self,
        status,
        processados=None,
        percentual=None,
        mensagem=None,
    ):

        chave = self._progress_key()

        if not chave:
            return

        total = getattr(
            self,
            '_progress_total',
            0,
        )

        if processados is None:

            processados = getattr(
                self,
                '_progress_processados',
                0,
            )


        if percentual is None:

            if total:

                percentual = int(
                    (
                        processados
                        / total
                    )
                    * 100
                )

                percentual = min(
                    percentual,
                    99,
                )

            else:

                percentual = 0


        try:

            cache.set(
                chave,
                {
                    'status': status,
                    'total': total,
                    'processados': processados,
                    'percentual': percentual,
                    'mensagem': mensagem,
                },
                timeout=3600,
            )

        except Exception as exc:

            # Redis nunca deve derrubar a importação.
            print(
                f"Erro ao salvar progresso "
                f"de Conteudo: {exc}"
            )

    def get_bulk_update_fields(self):
        # ManyToMany e chave primária não entram no bulk_update.
        return ['profile', 'texto', 'data', 'curtidas', 'comentarios',
                'link_post', 'categoria_tema']

    def _preparar_arquivo(self, dataset, modo):
        """Normaliza e separa linhas inválidas antes dos lotes bulk."""
        if modo not in {'adicionar', 'substituir'}:
            raise ValueError('Escolha adicionar ou substituir projetos IPD.')
        self.modo_projetos = modo
        self.projetos_por_post = {}
        self.relacoes_projetos = set()
        self.conteudos_existentes = {}
        self.linhas_ignoradas = []
        headers = [str(h).strip().lstrip('\ufeff') for h in (dataset.headers or [])]
        if len(headers) != len(set(headers)):
            raise ValueError('Existem nomes de colunas repetidos no arquivo.')
        obrigatorias = {'id_post', 'projeto_ipd', 'texto', 'data'}
        faltantes = obrigatorias - set(headers)
        if faltantes:
            raise ValueError('Colunas ausentes: ' + ', '.join(sorted(faltantes)))

        candidatas, vistos, riscos, convertidos = [], {}, [], 0
        for indice, valores in enumerate(dataset):
            row, linha = dict(zip(headers, valores)), indice + 2
            try:
                original = row['id_post']
                identificador, risco = normalizar_id_conteudo(original)
                if identificador in vistos:
                    raise ValueError(f'id_post repetido; primeira ocorrência na linha {vistos[identificador]}.')
                vistos[identificador] = linha
                if risco:
                    riscos.append(linha)
                if str(original).strip() != identificador:
                    convertidos += 1
                projetos = normalizar_projetos_conteudo(row['projeto_ipd'])
                if not str(row.get('texto') or '').strip():
                    raise ValueError('texto é obrigatório.')
                if row.get('profile') is not None and len(str(row['profile']).strip()) > 150:
                    raise ValueError('profile ultrapassa 150 caracteres.')
                if len(str(row['texto'])) > 10000000:
                    raise ValueError('texto ultrapassa o limite de segurança.')
                if row.get('link_post') and len(str(row['link_post']).strip()) > 1000:
                    raise ValueError('link_post ultrapassa 1000 caracteres.')
                if row.get('categoria_tema') and len(str(row['categoria_tema']).strip()) > 255:
                    raise ValueError('categoria_tema ultrapassa 255 caracteres.')
                if not row.get('data'):
                    raise ValueError('data é obrigatória.')
                row['data'] = normalizar_data_importacao(row['data'])
                # Validação explícita antes do bulk: vazio e zero são ambos aceitos.
                for campo in ('curtidas', 'comentarios'):
                    valor = row.get(campo)
                    if valor is None or str(valor).strip() == '':
                        row[campo] = 0
                    else:
                        numero = Decimal(str(valor).strip())
                        if not numero.is_finite() or numero != numero.to_integral_value() or not 0 <= numero <= 2147483647:
                            raise ValueError(f'{campo}: informe um inteiro entre 0 e 2147483647.')
                        row[campo] = int(numero)
                row['id_post'] = identificador
                row['projeto_ipd'] = ','.join(map(str, projetos))
                candidatas.append((linha, row, projetos))
            except (ValueError, InvalidOperation) as exc:
                self.linhas_ignoradas.append(f'Linha {linha}: {exc}')

        projetos_arquivo = {pk for _, _, projetos in candidatas for pk in projetos}
        projetos_validos = set(ProjetoIPD.objects.using(self.get_db_connection_name()).filter(
            pk__in=projetos_arquivo
        ).values_list('pk', flat=True))
        validas = []
        for linha, row, projetos in candidatas:
            ausentes = set(projetos) - projetos_validos
            if ausentes:
                self.linhas_ignoradas.append(
                    f'Linha {linha}: projeto(s) IPD inexistente(s): {", ".join(map(str, sorted(ausentes)))}.'
                )
                continue
            validas.append((row, projetos))
            self.projetos_por_post[row['id_post']] = projetos

        # O Resource recebe apenas linhas já verificadas. Assim os lotes bulk não
        # são interrompidos por uma célula inválida.
        dataset.wipe()
        dataset.headers = headers
        for row, _ in validas:
            dataset.append([row[h] for h in headers])
        if not validas:
            raise ValueError('Nenhuma linha válida para importar. ' + ' | '.join(self.linhas_ignoradas[:10]))

        self._avisos_ids = []
        if convertidos:
            self._avisos_ids.append(f'{convertidos} ID(s) convertidos para texto sem notação científica ou sufixo decimal.')
        if riscos:
            self._avisos_ids.append(
                f'{len(riscos)} ID(s) com mais de 15 dígitos podem já ter sido arredondados pelo Excel. '
                f'Linhas: {", ".join(map(str, riscos[:20]))}' + ('...' if len(riscos) > 20 else '.')
            )
        if self.linhas_ignoradas:
            self._avisos_ids.append(
                f'{len(self.linhas_ignoradas)} linha(s) ignorada(s): ' + ' | '.join(self.linhas_ignoradas[:5])
                + ('...' if len(self.linhas_ignoradas) > 5 else '')
            )


    def _resumo_erros_importacao(self, result):
        """Mensagem curta para a barra de progresso sem expor traceback."""
        mensagens = []
        for erro in getattr(result, 'base_errors', []):
            mensagens.append(str(getattr(erro, 'error', erro)))
        try:
            for linha, erros in result.row_errors():
                for erro in erros:
                    mensagens.append(f'Linha {linha}: {getattr(erro, "error", erro)}')
                    if len(mensagens) >= 3:
                        break
                if len(mensagens) >= 3:
                    break
        except Exception:
            pass
        if not mensagens:
            for linha_invalida in getattr(result, 'invalid_rows', [])[:3]:
                mensagens.append(
                    f'Linha {linha_invalida.number}: '
                    f'{linha_invalida.error}'
                )
        return ' | '.join(mensagens)[:900] or 'Erro não detalhado pelo importador. Verifique os logs do servidor.'

    def import_data(self, dataset, dry_run=False, raise_errors=False,
                    use_transactions=None, collect_failed_rows=False,
                    rollback_on_validation_errors=True, **kwargs):
        request = kwargs.get('request')
        self._progress_request = request
        self._progress_job_id = request.POST.get('import_job_id') if request else None
        self._progress_total = len(dataset)
        self._progress_processados = 0
        self._salvar_progresso('processando', processados=0, percentual=0,
                              mensagem='Normalizando IDs e validando projetos...')
        try:
            self._preparar_arquivo(dataset, kwargs.get('modo_projetos', 'adicionar'))
        except ValueError as exc:
            self._salvar_progresso('erro', mensagem=str(exc))
            if raise_errors:
                raise
            result = self.get_result_class()()
            result.total_rows = len(dataset)
            result.diff_headers = self.get_diff_headers()
            result.append_base_error(self.get_error_result_class()(exc))
            return result
        if request:
            request._conteudo_import_avisos = self._avisos_ids
        try:
            result = super().import_data(
                dataset, dry_run=dry_run, raise_errors=raise_errors,
                use_transactions=False, collect_failed_rows=collect_failed_rows,
                rollback_on_validation_errors=False, **kwargs,
            )
            erro = result.has_errors() or result.has_validation_errors()
            self._salvar_progresso(
                'concluido_com_erros' if erro else 'concluido',
                processados=len(dataset), percentual=100,
                mensagem=(
                    'Importação cancelada: ' + self._resumo_erros_importacao(result)
                    if erro else
                    'Validação concluída; nenhuma alteração salva.' if dry_run
                    else f'Importação concluída: {len(dataset)} registros importados; {len(self.linhas_ignoradas)} ignorados. Modo: {self.modo_projetos}.'
                ),
            )
            return result
        except Exception as exc:
            self._salvar_progresso('erro', mensagem=str(exc)[:500])
            raise

    def before_import(self, dataset, **kwargs):
        super().before_import(dataset, **kwargs)
        # Todos os IDs já são textos canônicos, iguais aos usados no import.
        self.conteudos_existentes = Conteudo.objects.using(self.get_db_connection_name()).in_bulk(self.projetos_por_post)

    def before_import_row(self, row, **kwargs):
        # Não converter IDs para float em nenhuma etapa.
        row['id_post'] = normalizar_id_conteudo(row['id_post'])[0]
        if row.get('profile') is not None:
            row['profile'] = str(row['profile']).strip()
        if not row.get('data'):
            raise ValueError('data é obrigatória.')
        row['data'] = normalizar_data_importacao(row['data'])
        for campo in ('curtidas', 'comentarios'):
            valor = row.get(campo)
            if valor is None or str(valor).strip() == '':
                row[campo] = 0
            else:
                try:
                    numero = Decimal(str(valor).strip())
                except InvalidOperation:
                    raise ValueError(f'{campo}: número inválido.')
                if not numero.is_finite() or numero != numero.to_integral_value() or not 0 <= numero <= 2147483647:
                    raise ValueError(f'{campo}: informe um inteiro entre 0 e 2147483647.')
                row[campo] = int(numero)
        if not row.get('categoria_tema'):
            row['categoria_tema'] = 'Outros'
        if row.get('link_post'):
            link = str(row['link_post']).strip().replace('\n', '').replace('\r', '')
            if link and not link.startswith(('http://', 'https://')):
                link = 'https://' + link
            row['link_post'] = link

    def get_instance(self, instance_loader, row):
        return self.conteudos_existentes.get(row['id_post'])

    def save_m2m(self, instance, row, *args, **kwargs):
        # Relações gravadas em bulk somente depois que os conteúdos forem salvos.
        pass

    def after_import_row(self, row, row_result, **kwargs):
        super().after_import_row(row, row_result, **kwargs)
        if row_result.import_type in {'new', 'update'} and not row_result.errors and not row_result.validation_error:
            identificador = row['id_post']
            self.relacoes_projetos.update(
                (identificador, pk) for pk in self.projetos_por_post[identificador]
            )
        self._progress_processados += 1
        if self._progress_processados % 500 == 0 or self._progress_processados == self._progress_total:
            self._salvar_progresso('processando', mensagem=f'Processando {self._progress_processados} de {self._progress_total}...')

    def after_import(self, dataset, result, **kwargs):
        super().after_import(dataset, result, **kwargs)
        if not self.relacoes_projetos:
            return
        self._salvar_progresso('processando', percentual=99, mensagem='Salvando vínculos com projetos IPD...')
        m2m = Conteudo._meta.get_field('projeto_ipd')
        through = m2m.remote_field.through
        campo_conteudo = through._meta.get_field(m2m.m2m_field_name()).attname
        campo_projeto = through._meta.get_field(m2m.m2m_reverse_field_name()).attname
        db = self.get_db_connection_name()
        manager = through.objects.using(db)
        posts = sorted({identificador for identificador, _ in self.relacoes_projetos})
        with transaction.atomic(using=db):
            # Bloqueia os conteúdos afetados durante a troca de vínculos.
            for inicio in range(0, len(posts), 1000):
                lote_ids = posts[inicio:inicio + 1000]
                list(Conteudo.objects.using(db).select_for_update().filter(pk__in=lote_ids).order_by('pk').values_list('pk', flat=True))
                if self.modo_projetos == 'substituir':
                    manager.filter(**{campo_conteudo + '__in': lote_ids}).delete()
            lote = []
            for identificador, projeto_id in self.relacoes_projetos:
                lote.append(through(**{campo_conteudo: identificador, campo_projeto: projeto_id}))
                if len(lote) >= 1000:
                    manager.bulk_create(lote, batch_size=1000, ignore_conflicts=True)
                    lote = []
            if lote:
                manager.bulk_create(lote, batch_size=1000, ignore_conflicts=True)

# =============================================================================
# ADMIN CONTEÚDO
# =============================================================================

@admin.register(Conteudo)
class ConteudoAdmin(GestaoAdminMixin, ImportExportModelAdmin):
    import_form_class = ConteudoImportForm
    confirm_form_class = ConteudoConfirmImportForm

    def get_import_data_kwargs(self, request, *args, **kwargs):
        form = kwargs.get('form')
        data = super().get_import_data_kwargs(request=request, **kwargs)
        data['request'] = request
        data['modo_projetos'] = form.cleaned_data['modo_projetos'] if form and hasattr(form, 'cleaned_data') else 'adicionar'
        return data

    def get_confirm_form_initial(self, request, import_form):
        initial = super().get_confirm_form_initial(request, import_form)
        if import_form:
            initial['modo_projetos'] = import_form.cleaned_data['modo_projetos']
        return initial

    def import_action(self, request, **kwargs):
        response = super().import_action(request, **kwargs)
        for aviso in getattr(request, '_conteudo_import_avisos', []):
            self.message_user(request, aviso, messages.WARNING)
        return response

    form = ConteudoAdminForm
    raw_id_fields = ("projeto_ipd",)

    def save_related(self, request, form, formsets, change):
        antigos = list(form.instance.projeto_ipd.values_list("pk", flat=True)) if change else []
        super().save_related(request, form, formsets, change)
        if form.cleaned_data.get("modo_projetos") == "adicionar" and antigos:
            form.instance.projeto_ipd.add(*antigos)


    resource_classes = [
        ConteudoResource
    ]


    # ============================================================
    # TEMPLATE DE IMPORTAÇÃO
    # ============================================================

    import_template_name = "conteudo_import.html"

    skip_import_confirm = True


    # ============================================================
    # URL PROGRESSO
    # ============================================================

    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [
            path(
                'import-progress/',
                self.admin_site.admin_view(
                    self.import_progress
                ),
                name=
                    'score_conteudo_import_progress',
            ),
        ]

        return custom_urls + urls


    # ============================================================
    # API PROGRESSO
    # ============================================================

    def import_progress(
        self,
        request,
    ):

        job_id = request.GET.get(
            'job_id'
        )


        if not job_id:

            response = JsonResponse({
                'status': 'aguardando',
                'percentual': 0,
                'processados': 0,
                'total': 0,
                'mensagem':
                    'Aguardando importação...',
            })

            response[
                'Cache-Control'
            ] = 'no-store'

            return response


        chave = (
            f"conteudo_import_progress:"
            f"{request.user.pk}:"
            f"{job_id}"
        )


        try:

            progresso = cache.get(
                chave
            )

        except Exception:

            progresso = None


        if progresso is None:

            progresso = {
                'status': 'aguardando',
                'percentual': 0,
                'processados': 0,
                'total': 0,
                'mensagem':
                    'Preparando arquivo...',
            }


        response = JsonResponse(
            progresso
        )


        response[
            'Cache-Control'
        ] = (
            'no-store, no-cache, '
            'must-revalidate, max-age=0'
        )


        return response


    # ============================================================
    # CHANGELIST
    # ============================================================

    def get_changelist(
        self,
        request,
        **kwargs
    ):

        return LimitedAdminChangeList


    # ============================================================
    # LISTAGEM
    # ============================================================

    list_display = (
        'id_post',
        'data',
        'profile',
    )


    list_filter = (
        'projeto_ipd',
        'data',
    )


    search_fields = (
        '=id_post',
        'profile',
    )


    ordering = (
        '-data',
    )


    list_per_page = 50
    list_max_show_all = 0
    show_full_result_count = False


    # ============================================================
    # EDIÇÃO/EXCLUSÃO CONFORME AS PERMISSÕES DO DJANGO
    # ============================================================



@admin.register(ResumoExecutivo)
class ResumoExecutivoAdmin(admin.ModelAdmin):

    list_display = (
        "projeto",
        "mes_referencia",
        "criado_em",
        "atualizado_em",
    )


    list_filter = (
        "mes_referencia",
        "criado_em",
        "atualizado_em",
    )


    search_fields = (
        "projeto__nome",
        "mes_referencia",
        "conteudo",
    )


    readonly_fields = (
        "hash_insumo",
        "criado_em",
        "atualizado_em",
    )


    ordering = (
        "-mes_referencia",
        "-atualizado_em",
    )


    list_per_page = 50

    show_full_result_count = False


    fieldsets = (

        (
            "Identificação",
            {
                "fields": (
                    "projeto",
                    "mes_referencia",
                )
            },
        ),

        (
            "Resumo Executivo",
            {
                "fields": (
                    "conteudo",
                )
            },
        ),

        (
            "Controle",
            {
                "fields": (
                    "hash_insumo",
                    "criado_em",
                    "atualizado_em",
                ),
                "classes": (
                    "collapse",
                ),
            },
        ),
    )