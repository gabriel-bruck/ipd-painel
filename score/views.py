import calendar
import os
from datetime import datetime

from django.conf import settings
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Sum
from django.db.models.functions import Coalesce, TruncMonth, TruncWeek
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from client.models import ProjetoCliente, ProjetoIPD
from score.models import IPD, Conteudo
from .services import (
    extrair_insumo_mes,
    gerar_resumo_executivo_stream,
    processar_analise_causal_ipd,
)


# ==============================================================================
# HELPER DE VALIDAÇÃO DE PERMISSÃO
# ==============================================================================
def usuario_tem_acesso_ao_projeto(user, projeto_cliente):
    """
    Verifica se o usuário logado possui acesso ao ProjetoCliente
    através da model PermissoesUsuario (related_name='usuarios_autorizados').
    Superusuários/Staffs ignoram essa restrição.
    """
    if user.is_staff or user.is_superuser:
        return True
    return projeto_cliente.usuarios_autorizados.filter(user=user).exists()


# ==============================================================================
# 1. API VIEW: PROFILES DO PROJETO
# ==============================================================================
class ProjetoProfilesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, projeto_id):
        # Garante que o projeto existe
        projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)

        # Validação de Segurança / Permissão de Acesso
        if not usuario_tem_acesso_ao_projeto(request.user, projeto):
            return Response(
                {"error": "Você não tem permissão para acessar os dados deste projeto."},
                status=status.HTTP_403_FORBIDDEN,
            )

        projetos_ipd = ProjetoIPD.objects.filter(projetos_cliente=projeto)
        total_ipds = projetos_ipd.count()

        if total_ipds == 0:
            return Response(
                {"error": "Nenhum IPD encontrado para este projeto."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if total_ipds == 1:
            ipd = projetos_ipd.first()
            medicoes = IPD.objects.filter(projeto_ipd=ipd)

            media_geral = medicoes.aggregate(media=Avg("ipd"))["media"] or 0.00

            semanais = (
                medicoes.annotate(semana=TruncWeek("data"))
                .values("profile", "semana")
                .annotate(
                    media_ipd=Avg("ipd"),
                    media_fama=Avg("fama"),
                    media_engaj=Avg("engaj"),
                    media_valencia=Avg("valencia"),
                    media_mob=Avg("mob"),
                    media_interesse=Avg("interesse"),
                )
                .order_by("profile", "-semana")
            )

            mensais = (
                medicoes.annotate(mes=TruncMonth("data"))
                .values("profile", "mes")
                .annotate(
                    media_ipd=Avg("ipd"),
                    media_fama=Avg("fama"),
                    media_engaj=Avg("engaj"),
                    media_valencia=Avg("valencia"),
                    media_mob=Avg("mob"),
                    media_interesse=Avg("interesse"),
                )
                .order_by("profile", "-mes")
            )

            payload = {
                "total_ipds": 1,
                "ipd_id": ipd.id,
                "ipd_nome": ipd.nome,
                "profiles_usados": (
                    ipd.profiles_usados
                    if hasattr(ipd, "profiles_usados")
                    else list(medicoes.values_list("profile", flat=True).distinct())
                ),
                "ipd_media": round(media_geral, 2),
                "medias_semanais": list(semanais),
                "medias_mensais": list(mensais),
            }

        else:
            lista_ipds = []

            for ipd in projetos_ipd:
                medicoes = IPD.objects.filter(projeto_ipd=ipd)

                media_geral = medicoes.aggregate(media=Avg("ipd"))["media"] or 0.00

                semanais = (
                    medicoes.annotate(semana=TruncWeek("data"))
                    .values("profile", "semana")
                    .annotate(
                        media_ipd=Avg("ipd"),
                        media_fama=Avg("fama"),
                        media_engaj=Avg("engaj"),
                        media_valencia=Avg("valencia"),
                        media_mob=Avg("mob"),
                        media_interesse=Avg("interesse"),
                    )
                    .order_by("profile", "-semana")
                )

                mensais = (
                    medicoes.annotate(mes=TruncMonth("data"))
                    .values("profile", "mes")
                    .annotate(
                        media_ipd=Avg("ipd"),
                        media_fama=Avg("fama"),
                        media_engaj=Avg("engaj"),
                        media_valencia=Avg("valencia"),
                        media_mob=Avg("mob"),
                        media_interesse=Avg("interesse"),
                    )
                    .order_by("profile", "-mes")
                )

                lista_ipds.append({
                    "ipd_id": ipd.id,
                    "ipd_nome": ipd.nome,
                    "profiles_usados": (
                        ipd.profiles_usados
                        if hasattr(ipd, "profiles_usados")
                        else list(medicoes.values_list("profile", flat=True).distinct())
                    ),
                    "ipd_media": round(media_geral, 2),
                    "medias_semanais": list(semanais),
                    "medias_mensais": list(mensais),
                })

            payload = {
                "total_ipds": total_ipds,
                "projeto_id": projeto.id,
                "ipds": lista_ipds,
            }

        return Response(payload, status=status.HTTP_200_OK)


# ==============================================================================
# 2. VIEW FUNCTION: STREAMING RESUMO EXECUTIVO
# ==============================================================================
@require_GET
def resumo_executivo_stream_view(request, projeto_id):
    # Autenticação prévia
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Não autenticado."}, status=401)

    projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)

    # Permissão do projeto
    if not usuario_tem_acesso_ao_projeto(request.user, projeto):
        return JsonResponse(
            {"error": "Você não tem permissão para acessar este projeto."}, status=403
        )

    mes_referencia = request.GET.get('mes', None)

    insumo_texto, nome_cliente = extrair_insumo_mes(projeto_id, mes_referencia)

    gerador_stream = gerar_resumo_executivo_stream(
        insumo_texto=insumo_texto,
        nome_cliente=nome_cliente,
        mes_referencia=mes_referencia,
    )

    response = StreamingHttpResponse(
        gerador_stream, content_type='text/plain; charset=utf-8'
    )
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'no-cache'

    return response


# ==============================================================================
# 3. VIEW FUNCTION: ANÁLISE CAUSAL IMPACT
# ==============================================================================
@require_GET
def analise_causal_impact_view(request, projeto_id):
    # Autenticação prévia
    if not request.user.is_authenticated:
        return JsonResponse({"sucesso": False, "error": "Não autenticado."}, status=401)

    projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)

    # Permissão do projeto
    if not usuario_tem_acesso_ao_projeto(request.user, projeto):
        return JsonResponse(
            {"sucesso": False, "error": "Você não tem permissão para acessar este projeto."},
            status=403,
        )

    perfil_alvo = request.GET.get('perfil')
    data_inicio = request.GET.get('data_inicio_evento')
    data_fim = request.GET.get('data_fim_evento')

    if not all([perfil_alvo, data_inicio, data_fim]):
        return JsonResponse(
            {
                "sucesso": False,
                "etapa_erro": "Validação de Parâmetros HTTP",
                "error": "Informe os parâmetros 'perfil', 'data_inicio_evento' e 'data_fim_evento'.",
            },
            status=400,
        )

    resultado = processar_analise_causal_ipd(
        projeto_id=projeto_id,
        perfil_alvo=perfil_alvo,
        data_inicio_evento_str=data_inicio,
        data_fim_evento_str=data_fim,
    )

    if not resultado.get("sucesso", False):
        return JsonResponse(resultado, status=400)

    return JsonResponse(resultado, status=200)


# ==============================================================================
# 4. API VIEW: MÉDIA DE MÉTRICAS DE CONTEÚDO
# ==============================================================================
class MediaMetricasConteudoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        projeto_ipd_id = request.query_params.get('projeto_ipd')
        profile = request.query_params.get('profile')
        ano_mes = request.query_params.get('ano_mes')  # YYYY-MM

        if not projeto_ipd_id or not profile or not ano_mes:
            return Response(
                {
                    "error": "Parâmetros obrigatórios ausentes: 'projeto_ipd', 'profile' e 'ano_mes' (ex: YYYY-MM)."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validação de Permissão buscando o ProjetoCliente via ProjetoIPD
        projeto_ipd = get_object_or_404(ProjetoIPD, pk=projeto_ipd_id)
        # Verifica se o usuário tem permissão para ao menos um ProjetoCliente vinculado ao ProjetoIPD
        projetos_cliente = projeto_ipd.projetos_cliente.all()

        tem_permissao = any(
            usuario_tem_acesso_ao_projeto(request.user, proj) for proj in projetos_cliente
        )
        if not tem_permissao:
            return Response(
                {"error": "Você não tem permissão para acessar os dados deste projeto."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            data_obj = datetime.strptime(ano_mes, '%Y-%m')
            ano = data_obj.year
            mes = data_obj.month

            _, ultimo_dia = calendar.monthrange(ano, mes)

            data_inicio = f"{ano:04d}-{mes:02d}-01"
            data_fim = f"{ano:04d}-{mes:02d}-{ultimo_dia:02d}"
        except ValueError:
            return Response(
                {"error": "Formato de 'ano_mes' inválido. Use o formato YYYY-MM (ex: 2026-06)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = Conteudo.objects.filter(
            projeto_ipd__id=projeto_ipd_id,
            profile__iexact=profile.strip(),
            data__range=[data_inicio, data_fim],
        )

        metricas = queryset.aggregate(
            media_curtidas=Avg('curtidas'),
            media_comentarios=Avg('comentarios'),
            total_posts=Count('id_post'),
        )

        total_posts = metricas['total_posts'] or 0

        top_posts_objs = queryset.order_by('-curtidas', '-comentarios')[:3]

        top_posts_data = []
        for post in top_posts_objs:
            top_posts_data.append({
                "id_post": post.id_post,
                "texto": post.texto,
                "data": post.data,
                "curtidas": post.curtidas or 0,
                "comentarios": post.comentarios or 0,
                "url": post.link_post,
            })

        return Response({
            "projeto_ipd": int(projeto_ipd_id) if str(projeto_ipd_id).isdigit() else projeto_ipd_id,
            "profile": profile,
            "periodo": ano_mes,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "total_posts": total_posts,
            "media_curtidas": round(metricas['media_curtidas'] or 0, 2),
            "media_comentarios": round(metricas['media_comentarios'] or 0, 2),
            "top_posts": top_posts_data,
        }, status=status.HTTP_200_OK)


# ==============================================================================
# 5. API VIEW: TEMAS E ENGAJAMENTO
# ==============================================================================
class TemasEngajamentoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        projeto_ipd_id = request.query_params.get(
            'projeto_ipd_id'
        ) or request.query_params.get('projeto_ipd')
        mes = request.query_params.get('mes')
        ano = request.query_params.get('ano')

        if not all([projeto_ipd_id, mes, ano]):
            return Response(
                {"error": "Os parâmetros 'projeto_ipd_id', 'mes' e 'ano' são obrigatórios."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mes = int(mes)
            ano = int(ano)
            projeto_ipd_id = int(projeto_ipd_id)
        except ValueError:
            return Response(
                {"error": "Os parâmetros devem ser numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validação de Permissão buscando o ProjetoCliente via ProjetoIPD
        projeto_ipd = get_object_or_404(ProjetoIPD, pk=projeto_ipd_id)
        projetos_cliente = projeto_ipd.projetos_cliente.all()

        tem_permissao = any(
            usuario_tem_acesso_ao_projeto(request.user, proj) for proj in projetos_cliente
        )
        if not tem_permissao:
            return Response(
                {"error": "Você não tem permissão para acessar os dados deste projeto."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = Conteudo.objects.filter(
            projeto_ipd__id=projeto_ipd_id, data__year=ano, data__month=mes
        )

        interacao_expr = F('curtidas') + F('comentarios')

        totais_globais = queryset.aggregate(
            total_interacoes_mes=Sum(interacao_expr), total_posts_mes=Count('id_post')
        )
        grand_interacoes = totais_globais['total_interacoes_mes'] or 0
        grand_posts = totais_globais['total_posts_mes'] or 0

        totais_por_perfil = {
            p['profile']: {
                'total_interacoes': p['total_interacoes'] or 0,
                'total_posts': p['total_posts'] or 0,
            }
            for p in queryset.values('profile').annotate(
                total_interacoes=Sum(interacao_expr), total_posts=Count('id_post')
            )
        }

        # 1. Agrupamento Geral
        raw_geral = (
            queryset.values('categoria_tema')
            .annotate(
                total_posts=Count('id_post'),
                total_interacoes=Coalesce(Sum(interacao_expr), 0),
            )
            .order_by('-total_interacoes')
        )

        temas_geral = []
        for item in raw_geral:
            t_inter = item['total_interacoes']
            t_posts = item['total_posts']
            temas_geral.append({
                'categoria_tema': item['categoria_tema'],
                'total_posts': t_posts,
                'total_interacoes': t_inter,
                'share_interacoes': (
                    round((t_inter / grand_interacoes * 100), 2)
                    if grand_interacoes > 0
                    else 0.0
                ),
                'share_posts': (
                    round((t_posts / grand_posts * 100), 2) if grand_posts > 0 else 0.0
                ),
                'interacao_por_post': round(t_inter / t_posts, 2) if t_posts > 0 else 0.0,
            })

        # 2. Agrupamento Por Perfil
        raw_perfil = (
            queryset.values('profile', 'categoria_tema')
            .annotate(
                total_posts=Count('id_post'),
                total_interacoes=Coalesce(Sum(interacao_expr), 0),
            )
            .order_by('profile', '-total_interacoes')
        )

        temas_por_perfil = []
        for item in raw_perfil:
            prof = item['profile']
            t_inter = item['total_interacoes']
            t_posts = item['total_posts']
            prof_totals = totais_por_perfil.get(
                prof, {'total_interacoes': 0, 'total_posts': 0}
            )

            p_inter_tot = prof_totals['total_interacoes']
            p_posts_tot = prof_totals['total_posts']

            temas_por_perfil.append({
                'profile': prof,
                'categoria_tema': item['categoria_tema'],
                'total_posts': t_posts,
                'total_interacoes': t_inter,
                'share_interacoes_perfil': (
                    round((t_inter / p_inter_tot * 100), 2) if p_inter_tot > 0 else 0.0
                ),
                'share_posts_perfil': (
                    round((t_posts / p_posts_tot * 100), 2) if p_posts_tot > 0 else 0.0
                ),
                'interacao_por_post': round(t_inter / t_posts, 2) if t_posts > 0 else 0.0,
            })

        return Response({
            "filtros": {"projeto_ipd_id": projeto_ipd_id, "mes": mes, "ano": ano},
            "temas_geral": temas_geral,
            "temas_por_perfil": temas_por_perfil,
        }, status=status.HTTP_200_OK)