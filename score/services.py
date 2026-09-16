import os
from django.db.models import Avg
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from dotenv import load_dotenv
from .models import Conteudo, IPD
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import numpy as np
import pandas as pd
from client.models import ProjetoClienteIPD, ProjetoIPD, ProjetoCliente
# Carrega as variáveis do arquivo .env
load_dotenv()

# Pega a chave (ajuste o nome da chave conforme o seu arquivo .env)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


def extrair_insumo_mes(projeto_id, mes_referencia=None):
    """Extrai os dados filtrando os perfis a partir de 'profiles_usados'

    presente no model intermediário ProjetoClienteIPD.
    """
    projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)
    nome_cliente = getattr(projeto, "cliente", "Cliente")

    # Mapeia as relações intermediárias do projeto cliente com cada IPD
    relacoes_ipd = ProjetoClienteIPD.objects.filter(
        projeto_cliente=projeto
    ).select_related("projeto_ipd")

    insumo_texto = (
        f"PROJETO: {projeto.nome} | CLIENTE ANALISADO: {nome_cliente}\n\n"
    )

    for relacao in relacoes_ipd:
        ipd = relacao.projeto_ipd
        profiles_usados = relacao.profiles_usados or []

        # Se não houver perfis cadastrados na lista do JSONField, pula o IPD
        if not profiles_usados:
            continue

        # 1. FILTRA MEDIÇÕES DO IPD APENAS PARA OS PERFIS EM 'profiles_usados'
        medicoes = IPD.objects.filter(
            projeto_ipd=ipd, profile__in=profiles_usados
        )

        qs_mensal = medicoes.annotate(mes_trunc=TruncMonth("data"))

        if mes_referencia:
            mes_alvo_str = str(mes_referencia)[:7]
            qs_mensal = qs_mensal.filter(mes_trunc__startswith=mes_alvo_str)
        else:
            ultimo_mes_dt = (
                qs_mensal.order_by("-mes_trunc")
                .values_list("mes_trunc", flat=True)
                .first()
            )
            if ultimo_mes_dt:
                qs_mensal = qs_mensal.filter(mes_trunc=ultimo_mes_dt)
                mes_alvo_str = (
                    ultimo_mes_dt.strftime("%Y-%m")
                    if hasattr(ultimo_mes_dt, "strftime")
                    else str(ultimo_mes_dt)[:7]
                )
            else:
                mes_alvo_str = None

        mensais = (
            qs_mensal.values("profile", "mes_trunc")
            .annotate(
                media_ipd=Avg("ipd"),
                media_fama=Avg("fama"),
                media_engaj=Avg("engaj"),
                media_valencia=Avg("valencia"),
                media_mob=Avg("mob"),
                media_interesse=Avg("interesse"),
            )
            .order_by("-media_ipd")
        )

        if not mensais:
            continue

        insumo_texto += f"=== INÍCIO DO MÓDULO IPD: {ipd.nome} ===\n"

        # Exibe apenas a classificação dos perfis que constam no JSONField
        insumo_texto += "RANKING DOS PERFIS UTILIZADOS NO PROJETO:\n"
        for idx, m in enumerate(mensais, 1):
            insumo_texto += f"  {idx}º {m['profile']} - IPD: {round(m['media_ipd'] or 0, 2)} (Fama: {round(m['media_fama'] or 0, 2)}, Engaj: {round(m['media_engaj'] or 0, 2)})\n"

        # 2. FILTRA CONTEÚDOS APENAS PARA OS PERFIS EM 'profiles_usados'
        if mes_alvo_str:
            top_posts = Conteudo.objects.filter(
                projeto_ipd=ipd,
                profile__in=profiles_usados,
                data__startswith=mes_alvo_str,
            ).order_by("-curtidas", "-comentarios")[:2]

            if top_posts.exists():
                insumo_texto += "DESTAQUES DE CONTEÚDO (PERFIS UTILIZADOS):\n"
                for post in top_posts:
                    texto_limpo = (
                        (post.texto or "").replace("\n", " ").strip()[:100]
                    )
                    insumo_texto += f"  - [{post.profile}] Likes: {post.curtidas} | \"{texto_limpo}...\"\n"

        insumo_texto += f"=== FIM DO MÓDULO IPD: {ipd.nome} ===\n\n"

    return insumo_texto, str(nome_cliente)


def gerar_resumo_executivo_stream(insumo_texto, nome_cliente, mes_referencia):
    llm = ChatOpenAI(
        model="openrouter/free",
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base="https://openrouter.ai/api/v1",
        streaming=True,
        temperature=0.1,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "Você é um analista sênior da Quaest Pesquisa e Consultoria.\n"
                    "Gere uma análise dividida rigorosamente por módulo de IPD para o cliente **{nome_cliente}**.\n\n"
                    "REGRA DE ISOLAMENTO DE DOMÍNIO E PERFIS:\n"
                    "1. Os insumos contêm APENAS os perfis configurados no projeto do cliente (`profiles_usados`).\n"
                    "2. Monte os rankings e análises exclusivamente com base nos perfis presentes dentro de cada bloco IPD.\n"
                    "3. NUNCA cruze ou mencione dados de um IPD no bloco de outro IPD.\n\n"
                    "FORMATO DE SAÍDA (Gere este bloco compacto para CADA IPD presente nos insumos):\n\n"
                    "### IPD: **[Nome Exato do IPD]**\n"
                    "- **Ranking Filtrado:** Apresente a posição dos perfis monitorados e destaque a colocação de **{nome_cliente}**.\n"
                    "- **Desempenho e Métricas:** Compare as métricas (Fama/Engajamento) do líder do grupo com o cliente.\n"
                    "- **Conteúdo e Oportunidade:** Destaque o post principal e traga uma recomendação estratégica acionável.\n\n"
                    "REGRAS DE REDAÇÃO:\n"
                    "- Mantenha exatamente 3 bullet points curtos por IPD.\n"
                    "- Sem texto introdutório. Comece diretamente em `### IPD:`."
                ),
            ),
            (
                "user",
                (
                    "Cliente: {nome_cliente}\n"
                    "Período: {mes}\n\n"
                    "Analise os dados dos perfis monitorados em cada IPD abaixo:\n\n"
                    "{insumo}"
                ),
            ),
        ]
    )

    chain = prompt | llm | StrOutputParser()

    inputs = {
        "nome_cliente": nome_cliente or "Cliente",
        "mes": mes_referencia or "Último mês disponível",
        "insumo": insumo_texto,
    }

    for chunk in chain.stream(inputs):
        yield chunk

import numpy as np
import pandas as pd

from causalimpact import CausalImpact as _CausalImpact
from django.shortcuts import get_object_or_404

# Ajuste estes imports conforme a estrutura do seu projeto.
# from seu_app.models import ProjetoCliente, ProjetoIPD, IPD

class CausalImpactCompat(_CausalImpact):
    """
    Corrige incompatibilidades do pycausalimpact com versões recentes
    do pandas.

    Correções principais:
    - troca mu[0] por mu.iloc[0];
    - troca sig[0] por sig.iloc[0];
    - suporta DataFrame.map nas versões recentes do pandas;
    - evita divisão por zero em colunas constantes.
    """

    def _format_input_data(self, data):
        if not isinstance(data, pd.DataFrame):
            try:
                data = pd.DataFrame(data)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Não foi possível converter os dados para DataFrame."
                ) from exc

        if data.empty:
            raise ValueError(
                "O DataFrame enviado ao CausalImpact está vazio."
            )

        self._validate_y(data.iloc[:, 0])

        if hasattr(data, "map"):
            mascara_numerica = data.map(np.isreal)
        else:
            mascara_numerica = data.applymap(np.isreal)

        if not mascara_numerica.to_numpy(dtype=bool).all():
            raise ValueError(
                "O CausalImpact aceita apenas valores numéricos."
            )

        if (
            data.shape[1] > 1
            and data.iloc[:, 1:].isna().to_numpy().any()
        ):
            raise ValueError(
                "As séries de controle não podem conter valores nulos."
            )

        return self._convert_index_to_datetime(data)

    def _standardize_pre_post_data(self):
        mu = self.pre_data.mean(skipna=True)
        sig = self.pre_data.std(skipna=True, ddof=0)

        desvio_serie_alvo = sig.iloc[0]

        if (
            pd.isna(desvio_serie_alvo)
            or np.isclose(float(desvio_serie_alvo), 0.0)
        ):
            raise ValueError(
                "A série alvo é constante no período pré-evento. "
                "Não é possível ajustar o modelo causal."
            )

        sig_seguro = sig.copy()

        for coluna in sig_seguro.index:
            valor = sig_seguro.loc[coluna]

            if pd.isna(valor) or np.isclose(float(valor), 0.0):
                sig_seguro.loc[coluna] = 1.0

        self.normed_pre_data = (
            self.pre_data - mu
        ) / sig_seguro

        self.normed_post_data = (
            self.post_data - mu
        ) / sig_seguro

        # Correção central do KeyError: 0
        self.mu_sig = (
            float(mu.iloc[0]),
            float(sig_seguro.iloc[0]),
        )


def numero_json(valor, casas_decimais=2):
    """
    Converte valores NumPy/Pandas em número válido para JSON.

    Retorna None quando o valor é nulo, NaN ou infinito.
    """
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None

        numero = float(valor)

        if not np.isfinite(numero):
            return None

        return round(numero, casas_decimais)

    except (TypeError, ValueError, OverflowError):
        return None
import numpy as np
import pandas as pd
from django.shortcuts import get_object_or_404


def processar_analise_causal_ipd(
    projeto_id,
    perfil_alvo,
    data_inicio_evento_str,
    data_fim_evento_str,
):
    """Executa análise de impacto causal para um perfil de IPD."""

    # ---------------------------------------------------------------------
    # ETAPA 1: Busca no Banco de Dados
    # ---------------------------------------------------------------------
    try:
        projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)

        # Consulta simplificada via relacionamento reverso
        medicoes = IPD.objects.filter(
            projeto_ipd__projetos_cliente=projeto
        ).values("data", "profile", "ipd")

        if not medicoes.exists():
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 1: Busca no Banco de Dados",
                "error": f"Nenhuma medição de IPD foi encontrada para o projeto ID {projeto_id}.",
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 1: Busca no Banco de Dados",
            "error": f"Falha ao consultar banco de dados: {type(e).__name__} - {str(e)}",
        }

    # ---------------------------------------------------------------------
    # ETAPA 2 & 3: Sanitização, Normalização e Pivotagem
    # ---------------------------------------------------------------------
    try:
        df = pd.DataFrame(medicoes)

        colunas_necessarias = {"data", "profile", "ipd"}
        if not colunas_necessarias.issubset(df.columns):
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 2: Sanitização de Dados",
                "error": f"Colunas ausentes: {sorted(colunas_necessarias - set(df.columns))}",
            }

        perfil_alvo = str(perfil_alvo).strip()
        if not perfil_alvo:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Perfil Alvo",
                "error": "O perfil alvo não pode estar vazio.",
            }

        # Normalização de datas e tipos antes do agrupamento
        df["data_postagem"] = pd.to_datetime(
            df["data"], errors="coerce"
        ).dt.normalize()
        df["ipd"] = pd.to_numeric(df["ipd"], errors="coerce")
        df["origem_busca"] = df["profile"].astype("string").str.strip()

        df = df.dropna(subset=["data_postagem", "ipd", "origem_busca"])
        df = df[df["origem_busca"].str.len() > 0]

        if df.empty:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 2: Sanitização de Dados",
                "error": "Todas as medições continham valores inválidos.",
            }

        # Pivotagem direta (já calcula a média por dia normalizado)
        df_pivot = (
            df.pivot_table(
                index="data_postagem",
                columns="origem_busca",
                values="ipd",
                aggfunc="mean",
            )
            .sort_index()
            .asfreq("D")
        )

        if perfil_alvo not in df_pivot.columns:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Pivotagem de Perfil",
                "error": f"Perfil '{perfil_alvo}' não encontrado. Perfis disponíveis: {list(df_pivot.columns)}",
            }

        # Garante que a variável alvo não seja interpolada artificialmente
        df_final = df_pivot.dropna(subset=[perfil_alvo]).copy()

        # Preenche lacunas APENAS nas variáveis de controle
        controles = [c for c in df_final.columns if c != perfil_alvo]
        if controles:
            df_final[controles] = df_final[controles].ffill().bfill()
            controles_validos = df_final[controles].dropna(axis=1).columns
            df_final = df_final[[perfil_alvo] + list(controles_validos)]

        dados_modelo = df_final.replace([np.inf, -np.inf], np.nan).astype(
            "float64"
        )

        if len(dados_modelo) < 5:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Série Temporal",
                "error": "São necessárias pelo menos 5 observações temporais.",
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 3: Pivotagem e Estruturação Temporal",
            "error": f"Erro ao organizar a matriz temporal: {type(e).__name__} - {str(e)}",
        }

    # ---------------------------------------------------------------------
    # ETAPA 4: Validação dos Períodos Pré e Pós-Evento
    # ---------------------------------------------------------------------
    try:
        data_evento = pd.to_datetime(
            data_inicio_evento_str, errors="raise"
        ).normalize()
        data_fim_evento = pd.to_datetime(
            data_fim_evento_str, errors="raise"
        ).normalize()

        data_minima = dados_modelo.index[0]
        data_maxima = dados_modelo.index[-1]

        if data_fim_evento < data_evento:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": "Data final é anterior à data inicial do evento.",
            }

        if data_evento <= data_minima or data_evento > data_maxima:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": "Data de início do evento está fora do escopo da série disponível.",
            }

        mask_pre = dados_modelo.index < data_evento
        mask_pos = (dados_modelo.index >= data_evento) & (
            dados_modelo.index <= min(data_fim_evento, data_maxima)
        )

        quantidade_pre = mask_pre.sum()
        quantidade_pos = mask_pos.sum()

        if quantidade_pre < 4 or quantidade_pos < 1:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": "Observações insuficientes no pré (mínimo 4) ou pós-evento (mínimo 1).",
            }

        pre_period_ts = [data_minima, dados_modelo.index[mask_pre][-1]]
        post_period_ts = [
            dados_modelo.index[mask_pos][0],
            dados_modelo.index[mask_pos][-1],
        ]

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 4: Cálculo dos Intervalos Temporais",
            "error": f"Erro ao calcular períodos: {type(e).__name__} - {str(e)}",
        }

    # ---------------------------------------------------------------------
    # ETAPA 5: Filtro Vetorizado e Execução do CausalImpact
    # ---------------------------------------------------------------------
    try:
        dados_pre = dados_modelo.loc[pre_period_ts[0] : pre_period_ts[1]]

        if np.isclose(float(dados_pre[perfil_alvo].std(ddof=0)), 0.0):
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 5: Execução do Algoritmo CausalImpact",
                "error": f"A série alvo '{perfil_alvo}' é constante no período pré-evento.",
            }

        # Filtro de controles constantes/inválidos vetorizado
        controles_cols = [c for c in dados_modelo.columns if c != perfil_alvo]
        stds = dados_pre[controles_cols].std(ddof=0)
        uniques = dados_pre[controles_cols].nunique()

        controles_removidos = stds[
            (stds.isna()) | (np.isclose(stds, 0.0)) | (uniques <= 1)
        ].index.tolist()

        df_causal_input = dados_modelo.drop(columns=controles_removidos)

        ci = CausalImpactCompat(
            df_causal_input,
            pre_period_ts,
            post_period_ts,
            standardize=True,
            prior_level_sd=None,
        )

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 5: Execução do Algoritmo CausalImpact",
            "error": f"Falha no cálculo do CausalImpact: {type(e).__name__} - {str(e)}",
            "versoes": {"pandas": pd.__version__},
        }

    # ---------------------------------------------------------------------
    # ETAPA 6: Construção da Resposta JSON
    # ---------------------------------------------------------------------
    try:
        try:
            report_text = str(ci.summary(output="report"))
        except TypeError:
            report_text = str(ci.summary("report"))

        p_value_bruto = getattr(ci, "p_value", None)
        p_val = (
            float(p_value_bruto)
            if p_value_bruto is not None and np.isfinite(float(p_value_bruto))
            else 1.0
        )

        df_inferences = getattr(ci, "inferences", None)
        if df_inferences is None:
            raise RuntimeError("O modelo não retornou ci.inferences.")

        # Extração do efeito acumulado
        efeito_acumulado_total = None
        if "post_cum_effects" in df_inferences.columns:
            val_cum = df_inferences["post_cum_effects"].dropna()
            if not val_cum.empty:
                efeito_acumulado_total = numero_json(val_cum.iloc[-1])

        # Montagem da série temporal de forma direta
        serie_temporal = []
        for idx, row in df_inferences.iterrows():
            dt_ts = pd.to_datetime(idx, errors="coerce")
            valor_obs = (
                df_causal_input.at[dt_ts, perfil_alvo]
                if dt_ts in df_causal_input.index
                else None
            )

            serie_temporal.append(
                {
                    "data": (
                        dt_ts.strftime("%Y-%m-%d") if pd.notna(dt_ts) else str(idx)
                    ),
                    "ipd_observado": numero_json(valor_obs),
                    "ipd_sintetico_previsto": numero_json(row.get("preds")),
                    "limite_inferior": numero_json(row.get("preds_lower")),
                    "limite_superior": numero_json(row.get("preds_upper")),
                    "efeito_pontual": numero_json(row.get("point_effects")),
                    "efeito_acumulado": numero_json(
                        row.get("post_cum_effects")
                    ),
                }
            )

        # Fallback de segurança para o efeito acumulado
        if efeito_acumulado_total is None and serie_temporal:
            pos_inicio_str = pre_period_ts[1].strftime("%Y-%m-%d")
            pontos_pos = [
                item
                for item in serie_temporal
                if item["data"] > pos_inicio_str
            ]
            if pontos_pos:
                soma_obs = sum(
                    item["ipd_observado"] or 0 for item in pontos_pos
                )
                soma_prev = sum(
                    item["ipd_sintetico_previsto"] or 0 for item in pontos_pos
                )
                efeito_acumulado_total = round(soma_obs - soma_prev, 2)

        return {
            "sucesso": True,
            "projeto_id": projeto.id,
            "cliente": str(getattr(projeto, "cliente", "Cliente")),
            "perfil_alvo": perfil_alvo,
            "periodo_pre": [
                pre_period_ts[0].strftime("%Y-%m-%d"),
                pre_period_ts[1].strftime("%Y-%m-%d"),
            ],
            "periodo_pos": [
                post_period_ts[0].strftime("%Y-%m-%d"),
                post_period_ts[1].strftime("%Y-%m-%d"),
            ],
            "quantidade_observacoes_pre": int(quantidade_pre),
            "quantidade_observacoes_pos": int(quantidade_pos),
            "p_value": round(p_val, 4),
            "estatisticamente_significativo": bool(p_val < 0.05),
            "efeito_acumulado_total": efeito_acumulado_total,
            "relatorio_textual": report_text,
            "controles_utilizados": [
                str(col) for col in df_causal_input.columns if col != perfil_alvo
            ],
            "controles_removidos_por_serem_constantes": controles_removidos,
            "serie_temporal": serie_temporal,
        }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 6: Formatação de Resposta JSON",
            "error": f"Erro ao extrair as séries e construir resposta: {type(e).__name__} - {str(e)}",
        }