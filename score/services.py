import os
from django.db.models import Avg
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from dotenv import load_dotenv
from .models import Conteudo, ProjetoCliente, ProjetoIPD, IPD
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import numpy as np
import pandas as pd
# Carrega as variáveis do arquivo .env
load_dotenv()

# Pega a chave (ajuste o nome da chave conforme o seu arquivo .env)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")



def extrair_insumo_mes(projeto_id, mes_referencia=None):
    """
    Busca o mês filtrado (ou o último disponível) para cada IPD do Projeto,
    filtra o Top 10 posts mais engajados POR IPD no mês e formata o insumo para a IA.
    """
    projeto = get_object_or_404(ProjetoCliente, pk=projeto_id)
    nome_cliente = getattr(projeto, 'cliente', 'Cliente')
    projetos_ipd = ProjetoIPD.objects.filter(projetos_cliente=projeto)

    insumo_texto = f"PROJETO: {projeto.nome}\n"

    for ipd in projetos_ipd:
        medicoes = IPD.objects.filter(projeto_ipd=ipd)
        qs_mensal = medicoes.annotate(mes_trunc=TruncMonth("data"))

        # Determina o mês exato a ser analisado
        if mes_referencia:
            mes_alvo_str = str(mes_referencia)[:7]  # Formato 'YYYY-MM'
            qs_mensal = qs_mensal.filter(mes_trunc__startswith=mes_alvo_str)
        else:
            ultimo_mes_dt = qs_mensal.order_by("-mes_trunc").values_list("mes_trunc", flat=True).first()
            if ultimo_mes_dt:
                qs_mensal = qs_mensal.filter(mes_trunc=ultimo_mes_dt)
                mes_alvo_str = ultimo_mes_dt.strftime("%Y-%m") if hasattr(ultimo_mes_dt, 'strftime') else str(ultimo_mes_dt)[:7]
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
            .order_by("profile")
        )

        insumo_texto += f"\n=========================================\n"
        insumo_texto += f"--- MÓDULO IPD: {ipd.nome} ---\n"
        insumo_texto += f"=========================================\n"
        
        if not mensais:
            insumo_texto += "Sem dados de medição disponíveis para este IPD no período especificado.\n"
            continue

        # 1. MÉTRICAS GERAIS DO MÊS DESTE IPD
        insumo_texto += "\n[MÉTRICAS DO IPD NO MÊS]:\n"
        for m in mensais:
            mes_val = m.get('mes_trunc')
            data_str = mes_val.strftime('%m/%Y') if hasattr(mes_val, 'strftime') else str(mes_val or 'N/A')
            insumo_texto += (
                f"Perfil: {m['profile']} | Mês: {data_str}\n"
                f"  - IPD Geral: {round(m['media_ipd'] or 0, 2)}\n"
                f"  - Fama: {round(m['media_fama'] or 0, 2)} | Engajamento: {round(m['media_engaj'] or 0, 2)}\n"
                f"  - Mobilização: {round(m['media_mob'] or 0, 2)} | Valência: {round(m['media_valencia'] or 0, 2)}\n"
                f"  - Interesse: {round(m['media_interesse'] or 0, 2)}\n"
            )

        # 2. CONTEÚDOS: TOP 10 POSTS MAIS ENGAJADOS DO MÊS VINCULADOS A ESTE IPD
        if mes_alvo_str:
            top_posts = (
                Conteudo.objects.filter(
                    projeto_ipd=ipd,             # Filtra posts associados a ESTE IPD (Relação M2M)
                    data__startswith=mes_alvo_str # Filtra a data no mês alvo 'YYYY-MM'
                )
                .order_by('-curtidas', '-comentarios')[:10]  # Pega os 10 mais engajados
            )

            insumo_texto += f"\n[TOP 10 POSTS MAIS ENGAJADOS DO MÊS NO IPD '{ipd.nome}']:\n"
            if top_posts.exists():
                for idx, post in enumerate(top_posts, 1):
                    texto_limpo = (post.texto or "").replace("\n", " ").strip()
                    texto_curto = texto_limpo[:220] + "..." if len(texto_limpo) > 220 else texto_limpo
                    
                    insumo_texto += (
                        f"{idx}. [{post.profile}] ({post.data}) - Likes: {post.curtidas} | Comentários: {post.comentarios}\n"
                        f"   Texto: \"{texto_curto}\"\n"
                    )
            else:
                insumo_texto += f"Nenhuma publicação vinculada ao IPD '{ipd.nome}' no mês {mes_alvo_str}.\n"

    return insumo_texto, str(nome_cliente)


def gerar_resumo_executivo_stream(insumo_texto, nome_cliente, mes_referencia):
    # Alterado para um modelo válido do OpenRouter (ex: gpt-4o-mini ou llama-3.1-80b-instruct:free)
    llm = ChatOpenAI(
        model="openai/gpt-4o-mini",
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base="https://openrouter.ai/api/v1",
        streaming=True,
        temperature=0.2,
    )

    prompt = ChatPromptTemplate.from_messages([
    ("system", (
        "Você é um especialista e analista sênior de dados da Quaest Pesquisa e Consultoria.\n"
        "Sua missão é gerar uma síntese executiva moderna, fluida e engajante sobre o desempenho digital do cliente **{nome_cliente}** no mês analisado.\n\n"
        "CONHECIMENTOS METODOLÓGICOS DO IPD (Índice de Popularidade Digital):\n"
        "- O IPD varia de 1.00 a 4.00, sendo 4 maior nota e quanto mais proximo de 1 pior calculado via 175 variáveis em 7 plataformas ativas no Brasil.\n"
        "- Avalia 5 dimensões:  Fama, Engajamento, Mobilização, Valência e Interesse O valor deve ser encarado de forma comparativo se nota distante dos lideres não é muito boa. \n\n"
        "REGRAS DE ESTRUTURA E ESTILO:\n"
        "0. É preciso deixar claro qual ipd se refere, caso haja mais de um no projeto do insumo"
        "1. A resposta DEVE conter EXATAMENTE 4 tópicos principais (parágrafos em bullet points começando com '- ').\n"
        "2. Cada tópico deve ser um parágrafo bem desenvolvido, com texto fluido, tom consultivo, leve e dinâmico (evite frases muito curtas ou puramente estatísticas).\n"
        "3. Tópico 1: Visão Geral e Destaques de Liderança (quem liderou o IPD dizer explicitamente o top 3 do ipd  e em cada uma das 5 dimensões e onde cliente se situa aqui).\n"
        "4. Tópico 2: Dinâmica de Engajamento e Mobilização (como o público interagiu, compartilhou e repercutiu o conteúdo com o cliente).\n"
        "5. Tópico 3: Valência, Percepção e Oportunidades (análise da qualidade das reações positivas vs. negativas e pontos de atenção para o cliente).\n"
        "6. Tópico 4: Uma análise das postagens que mais engajaram e os temas delas gerais e se cliente esteve entre os posts mais engajados.\n"
        "7. Use **negrito** para destacar nomes de perfis, notas importantes e insights vitais.\n"
        "8. NÃO inclua saudações, introduções ou conclusões genéricas. Comece direto no primeiro bullet point."
    )),
    ("user", "Mês de Análise: {mes}\n\nInsumos do Banco de Dados:\n{insumo}")
])

    chain = prompt | llm | StrOutputParser()

    # Passa as 3 variáveis exigidas pelo prompt
    inputs = {
        "nome_cliente": nome_cliente or "Cliente",
        "mes": mes_referencia or "Último mês disponível",
        "insumo": insumo_texto
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


def processar_analise_causal_ipd(
    projeto_id,
    perfil_alvo,
    data_inicio_evento_str,
    data_fim_evento_str,
):
    """
    Executa análise de impacto causal para um perfil de IPD.

    A primeira coluna enviada ao CausalImpact é o perfil alvo.
    Os demais perfis são utilizados como séries de controle.
    """

    # ---------------------------------------------------------------------
    # ETAPA 1: Busca do projeto e das medições
    # ---------------------------------------------------------------------
    try:
        projeto = get_object_or_404(
            ProjetoCliente,
            pk=projeto_id,
        )

        projetos_ipd = ProjetoIPD.objects.filter(
            projetos_cliente=projeto
        )

        medicoes = IPD.objects.filter(
            projeto_ipd__in=projetos_ipd
        ).values(
            "data",
            "profile",
            "ipd",
        )

        if not medicoes.exists():
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 1: Busca no Banco de Dados",
                "error": (
                    "Nenhuma medição de IPD foi encontrada "
                    f"para o projeto ID {projeto_id}."
                ),
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 1: Busca no Banco de Dados",
            "error": (
                "Falha ao consultar medições no banco de dados: "
                f"{type(e).__name__} - {str(e)}"
            ),
        }

    # ---------------------------------------------------------------------
    # ETAPA 2: Conversão e sanitização
    # ---------------------------------------------------------------------
    try:
        df = pd.DataFrame(list(medicoes))

        if df.empty:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 2: Sanitização de Dados",
                "error": (
                    "A lista de medições ficou vazia após "
                    "a conversão para DataFrame."
                ),
            }

        colunas_necessarias = {
            "data",
            "profile",
            "ipd",
        }

        colunas_faltantes = (
            colunas_necessarias.difference(df.columns)
        )

        if colunas_faltantes:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 2: Sanitização de Dados",
                "error": (
                    "Colunas obrigatórias ausentes nas medições: "
                    f"{sorted(colunas_faltantes)}"
                ),
            }

        df["data_postagem"] = pd.to_datetime(
            df["data"],
            errors="coerce",
        )

        df["ipd"] = pd.to_numeric(
            df["ipd"],
            errors="coerce",
        )

        df["origem_busca"] = (
            df["profile"]
            .astype("string")
            .str.strip()
        )

        df = df.dropna(
            subset=[
                "data_postagem",
                "ipd",
                "origem_busca",
            ]
        )

        df = df[
            df["origem_busca"].str.len() > 0
        ]

        df["ipd"] = df["ipd"].astype("float64")

        mascara_finita = np.isfinite(
            df["ipd"].to_numpy(dtype=float)
        )

        df = df.loc[mascara_finita].copy()

        if df.empty:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 2: Sanitização de Dados",
                "error": (
                    "Todas as medições possuíam valores inválidos, "
                    "nulos ou infinitos."
                ),
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": "Etapa 2: Sanitização de Dados",
            "error": (
                "Erro ao limpar os dados de IPD: "
                f"{type(e).__name__} - {str(e)}"
            ),
        }

    # ---------------------------------------------------------------------
    # ETAPA 3: Pivotagem e criação da série temporal diária
    # ---------------------------------------------------------------------
    try:
        perfil_alvo = str(perfil_alvo).strip()

        if not perfil_alvo:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Perfil Alvo",
                "error": "O perfil alvo não pode estar vazio.",
            }

        df_pivot = df.pivot_table(
            index="data_postagem",
            columns="origem_busca",
            values="ipd",
            aggfunc="mean",
        )

        df_pivot.columns.name = None
        df_pivot.index.name = None

        df_pivot = df_pivot.sort_index()

        if df_pivot.empty:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Pivotagem",
                "error": (
                    "Não foi possível gerar a matriz temporal "
                    "com os dados disponíveis."
                ),
            }

        # Normaliza as datas para remover horários.
        df_pivot.index = pd.DatetimeIndex(
            df_pivot.index
        ).normalize()

        # Caso a normalização gere datas duplicadas.
        if df_pivot.index.has_duplicates:
            df_pivot = df_pivot.groupby(
                level=0
            ).mean()

        # Cria frequência diária.
        df_final = df_pivot.asfreq("D")

        # Preenche lacunas temporais.
        df_final = (
            df_final
            .ffill()
            .bfill()
        )

        # Remove perfis que continuam completamente vazios.
        df_final = df_final.dropna(
            axis=1,
            how="all",
        )

        if perfil_alvo not in df_final.columns:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Pivotagem de Perfil",
                "error": (
                    f"Perfil '{perfil_alvo}' não encontrado. "
                    "Perfis disponíveis: "
                    f"{list(df_final.columns)}"
                ),
            }

        # Remove linhas em que a variável alvo está ausente.
        df_final = df_final.dropna(
            subset=[perfil_alvo]
        )

        if df_final.empty:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Série Alvo",
                "error": (
                    f"O perfil '{perfil_alvo}' não possui "
                    "observações válidas."
                ),
            }

        outras_colunas = [
            coluna
            for coluna in df_final.columns
            if coluna != perfil_alvo
        ]

        dados_modelo_com_datas = df_final[
            [perfil_alvo] + outras_colunas
        ].copy()

        dados_modelo_com_datas = (
            dados_modelo_com_datas
            .replace([np.inf, -np.inf], np.nan)
        )

        # Controles podem ser preenchidos porque o pycausalimpact
        # não aceita NaN nas covariáveis.
        if outras_colunas:
            dados_modelo_com_datas[outras_colunas] = (
                dados_modelo_com_datas[outras_colunas]
                .ffill()
                .bfill()
            )

        # Remove controles que continuam com NaN.
        controles_invalidos = [
            coluna
            for coluna in outras_colunas
            if dados_modelo_com_datas[coluna].isna().any()
        ]

        if controles_invalidos:
            dados_modelo_com_datas = (
                dados_modelo_com_datas.drop(
                    columns=controles_invalidos
                )
            )

        dados_modelo_com_datas = (
            dados_modelo_com_datas.astype("float64")
        )

        datas_ordenadas = list(
            dados_modelo_com_datas.index
        )

        if len(datas_ordenadas) < 5:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 3: Série Temporal",
                "error": (
                    "São necessárias pelo menos 5 observações "
                    "temporais para executar a análise."
                ),
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": (
                "Etapa 3: Pivotagem e Estruturação Temporal"
            ),
            "error": (
                "Erro ao organizar a matriz temporal: "
                f"{type(e).__name__} - {str(e)}"
            ),
        }

    # ---------------------------------------------------------------------
    # ETAPA 4: Validação dos períodos pré e pós-evento
    # ---------------------------------------------------------------------
    try:
        data_evento = pd.to_datetime(
            data_inicio_evento_str,
            errors="raise",
        ).normalize()

        data_fim_evento = pd.to_datetime(
            data_fim_evento_str,
            errors="raise",
        ).normalize()

        data_minima = pd.Timestamp(
            datas_ordenadas[0]
        )

        data_maxima = pd.Timestamp(
            datas_ordenadas[-1]
        )

        if data_fim_evento < data_evento:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "A data final do evento "
                    f"({data_fim_evento.strftime('%Y-%m-%d')}) "
                    "não pode ser anterior à data inicial "
                    f"({data_evento.strftime('%Y-%m-%d')})."
                ),
            }

        if data_evento <= data_minima:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "A data de início do evento "
                    f"({data_evento.strftime('%Y-%m-%d')}) "
                    "deve ser posterior à primeira data da série "
                    f"({data_minima.strftime('%Y-%m-%d')})."
                ),
            }

        if data_evento > data_maxima:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "A data de início do evento "
                    f"({data_evento.strftime('%Y-%m-%d')}) "
                    "é posterior à última data disponível "
                    f"({data_maxima.strftime('%Y-%m-%d')})."
                ),
            }

        indice_datas = dados_modelo_com_datas.index

        posicoes_pre = np.flatnonzero(
            indice_datas < data_evento
        )

        if len(posicoes_pre) == 0:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "Não existem observações anteriores "
                    "ao início do evento."
                ),
            }

        pos_pre_inicio = 0
        pos_pre_fim = int(posicoes_pre[-1])
        pos_pos_inicio = pos_pre_fim + 1

        data_final_utilizada = min(
            data_fim_evento,
            data_maxima,
        )

        posicoes_pos = np.flatnonzero(
            (indice_datas >= data_evento)
            & (indice_datas <= data_final_utilizada)
        )

        if len(posicoes_pos) == 0:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "Não existem observações dentro do "
                    "período pós-evento informado."
                ),
            }

        pos_pos_inicio = int(posicoes_pos[0])
        pos_pos_fim = int(posicoes_pos[-1])

        pre_period = [
            pos_pre_inicio,
            pos_pre_fim,
        ]

        post_period = [
            pos_pos_inicio,
            pos_pos_fim,
        ]

        quantidade_pre = (
            pre_period[1] - pre_period[0] + 1
        )

        quantidade_pos = (
            post_period[1] - post_period[0] + 1
        )

        if quantidade_pre < 4:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": (
                    "O período pré-evento precisa conter "
                    "pelo menos 4 observações."
                ),
            }

        if quantidade_pos < 1:
            return {
                "sucesso": False,
                "etapa_erro": "Etapa 4: Intervalos Temporais",
                "error": "O período pós-evento está vazio.",
            }

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": (
                "Etapa 4: Cálculo dos Intervalos Temporais"
            ),
            "error": (
                "Erro ao calcular os períodos pré e pós-evento: "
                f"{type(e).__name__} - {str(e)}"
            ),
        }

    # ---------------------------------------------------------------------
    # ETAPA 5: Preparação e execução do CausalImpact
    # ---------------------------------------------------------------------
    try:
        df_causal_input = (
            dados_modelo_com_datas
            .copy()
            .astype("float64")
        )

        if df_causal_input.index.has_duplicates:
            raise ValueError(
                "O índice temporal contém datas duplicadas."
            )

        if not df_causal_input.index.is_monotonic_increasing:
            df_causal_input = (
                df_causal_input.sort_index()
            )

        if df_causal_input[perfil_alvo].isna().any():
            raise ValueError(
                "A série alvo contém valores nulos."
            )

        if not np.isfinite(
            df_causal_input.to_numpy(dtype=float)
        ).all():
            raise ValueError(
                "A matriz contém valores NaN ou infinitos."
            )

        dados_pre = df_causal_input.iloc[
            pre_period[0]:pre_period[1] + 1
        ]

        desvio_alvo_pre = dados_pre[
            perfil_alvo
        ].std(ddof=0)

        if (
            pd.isna(desvio_alvo_pre)
            or np.isclose(
                float(desvio_alvo_pre),
                0.0,
            )
        ):
            return {
                "sucesso": False,
                "etapa_erro": (
                    "Etapa 5: Execução do Algoritmo CausalImpact"
                ),
                "error": (
                    f"A série alvo '{perfil_alvo}' é constante "
                    "no período pré-evento."
                ),
            }

        controles_removidos = []

        for coluna in list(
            df_causal_input.columns[1:]
        ):
            serie_pre = dados_pre[coluna]

            desvio = serie_pre.std(ddof=0)
            quantidade_unicos = serie_pre.nunique(
                dropna=True
            )

            if (
                quantidade_unicos <= 1
                or pd.isna(desvio)
                or np.isclose(float(desvio), 0.0)
            ):
                controles_removidos.append(
                    str(coluna)
                )

        if controles_removidos:
            df_causal_input = (
                df_causal_input.drop(
                    columns=controles_removidos
                )
            )

        pre_period_ts = [
            pd.Timestamp(
                df_causal_input.index[
                    pre_period[0]
                ]
            ),
            pd.Timestamp(
                df_causal_input.index[
                    pre_period[1]
                ]
            ),
        ]

        post_period_ts = [
            pd.Timestamp(
                df_causal_input.index[
                    post_period[0]
                ]
            ),
            pd.Timestamp(
                df_causal_input.index[
                    post_period[1]
                ]
            ),
        ]

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
            "etapa_erro": (
                "Etapa 5: Execução do Algoritmo CausalImpact"
            ),
            "error": (
                "Falha no cálculo do CausalImpact: "
                f"{type(e).__name__} - {str(e)}"
            ),
            "versoes": {
                "pandas": pd.__version__,
            },
        }

    # ---------------------------------------------------------------------
    # ETAPA 6: Construção da resposta JSON
    # ---------------------------------------------------------------------
    try:
        try:
            report_text = str(
                ci.summary(output="report")
            )
        except TypeError:
            report_text = str(
                ci.summary("report")
            )

        p_value_bruto = getattr(
            ci,
            "p_value",
            None,
        )

        if p_value_bruto is None:
            p_val = 1.0
        else:
            p_val = float(p_value_bruto)

            if not np.isfinite(p_val):
                p_val = 1.0

        resultado = {
            "sucesso": True,
            "projeto_id": projeto.id,
            "cliente": str(
                getattr(
                    projeto,
                    "cliente",
                    "Cliente",
                )
            ),
            "perfil_alvo": perfil_alvo,
            "periodo_pre": [
                pre_period_ts[0].strftime(
                    "%Y-%m-%d"
                ),
                pre_period_ts[1].strftime(
                    "%Y-%m-%d"
                ),
            ],
            "periodo_pos": [
                post_period_ts[0].strftime(
                    "%Y-%m-%d"
                ),
                post_period_ts[1].strftime(
                    "%Y-%m-%d"
                ),
            ],
            "quantidade_observacoes_pre": (
                quantidade_pre
            ),
            "quantidade_observacoes_pos": (
                quantidade_pos
            ),
            "p_value": round(p_val, 4),
            "estatisticamente_significativo": bool(
                p_val < 0.05
            ),
            "relatorio_textual": report_text,
            "controles_utilizados": [
                str(coluna)
                for coluna in df_causal_input.columns[1:]
            ],
            "controles_removidos_por_serem_constantes": (
                controles_removidos
            ),
            "serie_temporal": [],
        }

        if (
            not hasattr(ci, "inferences")
            or ci.inferences is None
        ):
            raise RuntimeError(
                "O modelo não retornou ci.inferences."
            )

        df_inferences = ci.inferences.copy()

        colunas_obrigatorias = {
            "preds",
            "preds_lower",
            "preds_upper",
        }

        colunas_faltantes = (
            colunas_obrigatorias.difference(
                df_inferences.columns
            )
        )

        if colunas_faltantes:
            raise RuntimeError(
                "Colunas ausentes no resultado: "
                f"{sorted(colunas_faltantes)}"
            )

        for idx, row in df_inferences.iterrows():
            valor_observado = None
            data_idx = None

            # Caso normal: índice com datas.
            try:
                idx_convertido = pd.Timestamp(idx)

                if idx_convertido in df_causal_input.index:
                    data_idx = idx_convertido
                    valor_observado = (
                        df_causal_input.at[
                            idx_convertido,
                            perfil_alvo,
                        ]
                    )
            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                pass

            # Compatibilidade com índice numérico.
            if data_idx is None:
                try:
                    posicao = int(idx)

                    if (
                        0
                        <= posicao
                        < len(df_causal_input)
                    ):
                        data_idx = pd.Timestamp(
                            df_causal_input.index[
                                posicao
                            ]
                        )

                        valor_observado = (
                            df_causal_input[
                                perfil_alvo
                            ].iloc[posicao]
                        )
                except (
                    TypeError,
                    ValueError,
                    OverflowError,
                ):
                    continue

            resultado["serie_temporal"].append({
                "data": data_idx.strftime(
                    "%Y-%m-%d"
                ),
                "ipd_observado": numero_json(
                    valor_observado
                ),
                "ipd_sintetico_previsto": numero_json(
                    row.get("preds")
                ),
                "limite_inferior": numero_json(
                    row.get("preds_lower")
                ),
                "limite_superior": numero_json(
                    row.get("preds_upper")
                ),
                "efeito_pontual": numero_json(
                    row.get("point_effects")
                ),
                "efeito_acumulado": numero_json(
                    row.get("post_cum_effects")
                ),
            })

        return resultado

    except Exception as e:
        return {
            "sucesso": False,
            "etapa_erro": (
                "Etapa 6: Formatação de Resposta JSON"
            ),
            "error": (
                "Erro ao extrair as séries e construir "
                "a resposta final: "
                f"{type(e).__name__} - {str(e)}"
            ),
        }