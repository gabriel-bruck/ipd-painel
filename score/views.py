from django.db.models import Avg
from django.db.models.functions import TruncMonth, TruncWeek
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .services import processar_analise_causal_ipd
from client.models import ProjetoCliente, ProjetoIPD
from score.models import IPD



class ProjetoProfilesAPIView(APIView):

    def get(self, request, projeto_id):
        # Garante que o projeto existe
        projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)
        
        # Correção do campo de busca: 'projetos_cliente' em vez de 'projetos_cliente_id'
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

from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt
from .services import extrair_insumo_mes, gerar_resumo_executivo_stream

@csrf_exempt  # Opcional caso tenha problemas com CSRF em requisições GET externas
@require_GET
def resumo_executivo_stream_view(request, projeto_id):
    mes_referencia = request.GET.get('mes', None)

    # Captura insumo_texto e nome_cliente da função
    insumo_texto, nome_cliente = extrair_insumo_mes(projeto_id, mes_referencia)

    # Dispara o gerador com os 3 argumentos esperados
    gerador_stream = gerar_resumo_executivo_stream(
        insumo_texto=insumo_texto,
        nome_cliente=nome_cliente,
        mes_referencia=mes_referencia
    )

    response = StreamingHttpResponse(
        gerador_stream, 
        content_type='text/plain; charset=utf-8'
    )
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'no-cache'

    return response

import pandas as pd
from causalimpact import CausalImpact
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404
from .models import ProjetoCliente, ProjetoIPD, IPD

@require_GET
def analise_causal_impact_view(request, projeto_id):
    perfil_alvo = request.GET.get('perfil')
    data_inicio = request.GET.get('data_inicio_evento')
    data_fim = request.GET.get('data_fim_evento')

    if not all([perfil_alvo, data_inicio, data_fim]):
        return JsonResponse(
            {
                "sucesso": False,
                "etapa_erro": "Validação de Parâmetros HTTP",
                "error": "Informe os parâmetros 'perfil', 'data_inicio_evento' e 'data_fim_evento'."
            },
            status=400,
        )

    # Executa a função do serviço
    resultado = processar_analise_causal_ipd(
        projeto_id=projeto_id,
        perfil_alvo=perfil_alvo,
        data_inicio_evento_str=data_inicio,
        data_fim_evento_str=data_fim
    )

    # Se a chave "sucesso" for False, devolve status HTTP 400 com o detalhe do erro
    if not resultado.get("sucesso", False):
        return JsonResponse(resultado, status=400)

    # Sucesso completo
    return JsonResponse(resultado, status=200)