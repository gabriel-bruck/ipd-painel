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

class LimitedAdminChangeList(ChangeList):

    LIMITE_ADMIN = 50

    def get_results(
        self,
        request
    ):

        # IMPORTANTE:
        # self.queryset continua sem slice.
        #
        # Isso permite:
        # filtro
        # busca
        # delete
        # actions
        # POST do admin

        queryset = self.queryset

        # Apenas a exibição é limitada.
        self.result_list = list(
            queryset[
                :self.LIMITE_ADMIN
            ]
        )

        quantidade = len(
            self.result_list
        )

        self.result_count = quantidade
        self.full_result_count = quantidade

        self.can_show_all = False
        self.multi_page = False

        self.paginator = (
            self.model_admin
            .get_paginator(
                request,
                queryset,
                self.model_admin.list_per_page,
            )
        )


# =============================================================================
# ADMIN IPD
# =============================================================================

@admin.register(IPD)
class IPDAdmin(ImportExportModelAdmin):

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

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return True


    actions = [
        'delete_selected',
    ]


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
    list_max_show_all = 50
    show_full_result_count = False


    autocomplete_fields = (
        'projeto_ipd',
    )
# =============================================================================
# CONTEÚDO
# =============================================================================

class ConteudoResource(resources.ModelResource):

    # =========================================================================
    # PROJETO IPD
    # =========================================================================
    #
    # IMPORTANTE:
    #
    # Não usamos ManyToManyWidget aqui.
    #
    # O valor da planilha já contém os IDs corretos.
    # Vamos guardar as relações em memória e fazer um único
    # bulk_create no final.
    #
    # Isso remove consultas SQL por linha.
    # =========================================================================

    projeto_ipd = fields.Field(
        column_name='projeto_ipd',
        readonly=True,
    )


    class Meta:

        model = Conteudo

        import_id_fields = (
            'id_post',
        )

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

        ignore_unknown_fields = True

        # ============================================================
        # BULK
        # ============================================================

        use_bulk = True

        # 1000 é seguro mesmo com campo texto grande.
        batch_size = 1000

        skip_diff = True
        skip_unchanged = False
        report_skipped = False

        store_instance = False


    # =========================================================================
    # PROGRESSO
    # =========================================================================

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


    # =========================================================================
    # IMPORTAÇÃO COMPLETA
    # =========================================================================

    def import_data(
        self,
        dataset,
        *args,
        **kwargs
    ):

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
                f"conteúdos..."
            ),
        )


        try:

            result = super().import_data(
                dataset,
                *args,
                **kwargs
            )


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
                        f"conteúdos processados."
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
                mensagem=str(
                    exc
                )[:500],
            )

            raise


    # =========================================================================
    # INÍCIO
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


        # ============================================================
        # IDS DO ARQUIVO
        # ============================================================

        ids_csv = set()


        for row in dataset.dict:

            valor = row.get(
                'id_post'
            )

            if valor not in (
                None,
                '',
            ):

                ids_csv.add(
                    str(
                        valor
                    ).strip()
                )


        # ============================================================
        # UMA CONSULTA PARA TODOS OS EXISTENTES
        # ============================================================
        #
        # Depois disso, get_instance() não consulta mais o banco.
        # ============================================================

        self._salvar_progresso(
            status='processando',
            processados=0,
            percentual=0,
            mensagem=(
                "Localizando conteúdos "
                "já existentes..."
            ),
        )


        if ids_csv:

            self.conteudos_existentes = (
                Conteudo.objects.in_bulk(
                    ids_csv
                )
            )

        else:

            self.conteudos_existentes = {}


        # ============================================================
        # RELAÇÕES M2M
        # ============================================================

        self.relacoes_projetos = set()


    # =========================================================================
    # PREPARAÇÃO DA LINHA
    # =========================================================================

    def before_import_row(
        self,
        row,
        **kwargs
    ):

        # ============================================================
        # ID POST
        # ============================================================

        if row.get(
            'id_post'
        ) is not None:

            row['id_post'] = str(
                row['id_post']
            ).strip()


        # ============================================================
        # PROFILE
        # ============================================================

        if row.get(
            'profile'
        ) is not None:

            row['profile'] = str(
                row['profile']
            ).strip()


        # ============================================================
        # DATA
        # ============================================================

        if row.get(
            'data'
        ):

            row['data'] = (
                normalizar_data_importacao(
                    row['data']
                )
            )


        # ============================================================
        # LINK
        # ============================================================

        if row.get(
            'link_post'
        ):

            link = str(
                row['link_post']
            ).strip()

            link = (
                link
                .replace(
                    '\n',
                    ''
                )
                .replace(
                    '\r',
                    ''
                )
            )

            if (
                link
                and not link.startswith(
                    (
                        'http://',
                        'https://',
                    )
                )
            ):

                link = (
                    f"https://{link}"
                )


            row['link_post'] = link


    # =========================================================================
    # DESCOBRE REGISTRO EXISTENTE
    # =========================================================================

    def get_instance(
        self,
        instance_loader,
        row,
    ):
        """
        ZERO SELECT por linha.

        O dicionário inteiro foi carregado
        no before_import().
        """

        id_post = str(
            row.get(
                'id_post'
            )
            or ''
        ).strip()


        if not id_post:
            return None


        return (
            self.conteudos_existentes
            .get(
                id_post
            )
        )


    # =========================================================================
    # NÃO SALVA M2M LINHA POR LINHA
    # =========================================================================

    def save_m2m(
        self,
        obj,
        data,
        using_historical_record=False,
        dry_run=False,
        **kwargs
    ):
        """
        Intencionalmente vazio.

        As relações ProjetoIPD serão feitas
        todas juntas em after_import().
        """

        return


    # =========================================================================
    # DEPOIS DE CADA LINHA
    # =========================================================================

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


        # ============================================================
        # RELAÇÃO CONTEUDO -> PROJETO
        # ============================================================

        if not row_result.errors:

            id_post = str(
                row.get(
                    'id_post'
                )
                or ''
            ).strip()


            projetos_raw = row.get(
                'projeto_ipd'
            )


            if (
                id_post
                and projetos_raw not in (
                    None,
                    '',
                )
            ):

                valores = (
                    str(
                        projetos_raw
                    )
                    .replace(
                        ';',
                        ','
                    )
                    .split(
                        ','
                    )
                )


                for valor in valores:

                    valor = (
                        valor.strip()
                    )

                    if not valor:
                        continue


                    try:

                        # Funciona também se Excel
                        # entregar "5.0".
                        projeto_id = int(
                            float(
                                valor
                            )
                        )

                    except (
                        TypeError,
                        ValueError,
                    ):

                        continue


                    self.relacoes_projetos.add(
                        (
                            id_post,
                            projeto_id,
                        )
                    )


        # ============================================================
        # PROGRESSO
        # ============================================================

        self._progress_processados += 1


        processados = (
            self._progress_processados
        )

        total = (
            self._progress_total
        )


        # A cada 500.
        #
        # Para 15 mil registros são somente
        # 30 writes no Redis.
        if (
            processados % 500 == 0
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


            percentual = min(
                percentual,
                99,
            )


            if processados >= total:

                mensagem = (
                    "Finalizando conteúdos "
                    "e relacionamentos..."
                )

            else:

                mensagem = (
                    f"Processando "
                    f"{processados:,} de "
                    f"{total:,} conteúdos..."
                )


            self._salvar_progresso(
                status='processando',
                processados=processados,
                percentual=percentual,
                mensagem=mensagem,
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


        # ============================================================
        # SE NÃO EXISTEM RELAÇÕES, TERMINA
        # ============================================================

        if not self.relacoes_projetos:
            return


        self._salvar_progresso(
            status='processando',
            processados=
                self._progress_total,
            percentual=99,
            mensagem=(
                "Salvando relações "
                "com os projetos..."
            ),
        )


        # ============================================================
        # THROUGH MODEL AUTOMÁTICO DO MANY-TO-MANY
        # ============================================================

        through = (
            Conteudo
            .projeto_ipd
            .through
        )


        # ============================================================
        # DESCOBRE OS NOMES REAIS DAS FKs
        # ============================================================
        #
        # Assim não dependemos de saber se Django chamou:
        #
        # conteudo_id
        # projetoipd_id
        #
        # etc.
        # ============================================================

        campo_conteudo = None
        campo_projeto = None


        for campo in (
            through._meta.fields
        ):

            remote_model = getattr(
                getattr(
                    campo,
                    'remote_field',
                    None,
                ),
                'model',
                None,
            )


            if remote_model is Conteudo:

                campo_conteudo = (
                    campo.attname
                )


            elif remote_model is ProjetoIPD:

                campo_projeto = (
                    campo.attname
                )


        if (
            not campo_conteudo
            or not campo_projeto
        ):

            raise RuntimeError(
                "Não foi possível identificar "
                "as FKs da relação "
                "Conteudo.projeto_ipd."
            )


        # ============================================================
        # MONTA RELAÇÕES EM MEMÓRIA
        # ============================================================

        objetos_relacao = []


        for (
            id_post,
            projeto_id,
        ) in self.relacoes_projetos:

            objetos_relacao.append(
                through(
                    **{
                        campo_conteudo:
                            id_post,

                        campo_projeto:
                            projeto_id,
                    }
                )
            )


        # ============================================================
        # UM BULK PARA TODAS AS RELAÇÕES
        # ============================================================
        #
        # ignore_conflicts=True:
        #
        # se conteúdo já estiver ligado ao projeto,
        # simplesmente ignora.
        #
        # Não remove relações antigas.
        # ============================================================

        if objetos_relacao:

            through.objects.bulk_create(
                objetos_relacao,
                batch_size=5000,
                ignore_conflicts=True,
            )
# =============================================================================
# ADMIN CONTEÚDO
# =============================================================================

@admin.register(Conteudo)
class ConteudoAdmin(ImportExportModelAdmin):

    resource_classes = [
        ConteudoResource
    ]


    # ============================================================
    # TEMPLATE DE IMPORTAÇÃO
    # ============================================================

    import_template_name = (
        "conteudo_import.html"
    )

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
        'data',
        'projeto_ipd',
    )


    search_fields = (
        '=id_post',
        'profile',
    )


    ordering = (
        '-data',
    )


    list_per_page = 50
    list_max_show_all = 50
    show_full_result_count = False


    # ============================================================
    # SEM EDIÇÃO/EXCLUSÃO MANUAL
    # ============================================================

    def has_change_permission(
        self,
        request,
        obj=None,
    ):

        return False


    def has_delete_permission(
        self,
        request,
        obj=None,
    ):

        return False
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