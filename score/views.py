from django.db.models import Avg
from django.db.models.functions import TruncMonth, TruncWeek
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

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