import json
import re
import numpy as np
import pandas as pd
import nltk
from nltk.corpus import stopwords
from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import euclidean_distances
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

try:
    pt_stop_words = stopwords.words('portuguese')
except LookupError:
    nltk.download('stopwords')
    pt_stop_words = stopwords.words('portuguese')


class Categoria(BaseModel):
    cluster_id: int = Field(description="ID do grupo")
    nome_categoria: str = Field(description="Nome equilibrado, neutro e sem nomes de clientes ou canais")
    descricao: str = Field(description="Resumo executivo do tema em 1 frase")

class ResultadoCategorias(BaseModel):
    categorias: list[Categoria]


def encontrar_k_otimo_cotovelo(X, k_min=5, k_max=20):
    n_amostras = X.shape[0]
    k_min = min(k_min, n_amostras)
    k_max = min(k_max, max(1, n_amostras - 1))

    if k_min >= k_max:
        return k_min

    k_values = list(range(k_min, k_max + 1))
    inertias = []

    for k in k_values:
        km = KMeans(n_clusters=k, random_state=42, n_init=3)
        km.fit(X)
        inertias.append(km.inertia_)

    p1 = np.array([k_values[0], inertias[0]])
    p2 = np.array([k_values[-1], inertias[-1]])

    v1 = p2 - p1
    norm_v1 = np.linalg.norm(v1)

    distancias = []
    for k, inertia in zip(k_values, inertias):
        p0 = np.array([k, inertia])
        v2 = p1 - p0
        
        cross_2d = v1[0] * v2[1] - v1[1] * v2[0]
        distancia = np.abs(cross_2d) / norm_v1 if norm_v1 != 0 else 0
        distancias.append(distancia)

    return k_values[np.argmax(distancias)]


def classificar_tabela_input(
    df_input: pd.DataFrame,
    coluna_id: str = 'id_post',
    coluna_texto: str = 'post',
    openrouter_api_key: str = '',
    descricao_cliente: str = '',
    descricao_projeto: str = ''
) -> pd.DataFrame:
    """Recebe o DataFrame do input, aplica TF-IDF + Elbow + K-Means + IA

    considerando a descrição do cliente e do projeto para priorizar temas úteis.
    """
    if coluna_id not in df_input.columns or coluna_texto not in df_input.columns:
        raise KeyError(f"As colunas '{coluna_id}' e/ou '{coluna_texto}' não existem na tabela de entrada.")

    # 1. Isola posts válidos para cálculo sem alterar a estrutura do input
    df_validos = df_input[
        df_input[coluna_id].notna() & 
        df_input[coluna_texto].notna() & 
        (df_input[coluna_texto].astype(str).str.strip() != '')
    ].copy()

    textos = df_validos[coluna_texto].astype(str).tolist()
    if not textos:
        raise ValueError("Nenhum post válido encontrado para processamento.")

    # 2. Vetorização TF-IDF
    vectorizer = TfidfVectorizer(
        max_features=400,
        stop_words=pt_stop_words,
        ngram_range=(1, 2)
    )
    X = vectorizer.fit_transform(textos)

    # 3. Definição do K ótimo via Método do Cotovelo
    k_otimo = encontrar_k_otimo_cotovelo(X, k_min=5, k_max=20)

    # 4. K-Means
    km_final = KMeans(n_clusters=k_otimo, random_state=42, n_init=5)
    labels = km_final.fit_predict(X)
    centroides = km_final.cluster_centers_

    df_validos['cluster_id'] = labels

    # 5. Amostras centrais por centroide
    grupos_amostras = {}
    for cid in range(k_otimo):
        indices_cluster = np.where(labels == cid)[0]
        if len(indices_cluster) == 0:
            continue

        X_cluster = X[indices_cluster].toarray()
        distancias = euclidean_distances(X_cluster, [centroides[cid]]).flatten()
        indices_ordenados = indices_cluster[np.argsort(distancias)]

        top_3_indices = indices_ordenados[:10]
        grupos_amostras[cid] = [textos[idx] for idx in top_3_indices]

    # 6. Rotulagem das classes via IA com Contexto do Projeto e Cliente
    llm = ChatOpenAI(
        model="openrouter/free",
        openai_api_key=openrouter_api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
        request_timeout=400,
        max_retries=3
    )
    parser = JsonOutputParser(pydantic_object=ResultadoCategorias)

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "Você é um analista de dados e taxonomista sênior.\n"
         "Sua tarefa é analisar amostras de textos de cada grupo e dar um NOME DE CATEGORIA EQUILIBRADO e acionável.\n\n"
         "CONTEXTO DA ANÁLISE:\n"
         "- Descrição do Cliente: {descricao_cliente}\n"
         "- Objetivo do Projeto: {descricao_projeto}\n\n"
         "REGRAS DE NOMENCLATURA:\n"
         "1. Crie nomes de categorias que sejam diretamente úteis para os objetivos do projeto e o negócio do cliente.\n"
         "2. NUNCA inclua nomes próprios de clientes, empresas, marcas, veículos de comunicação ou redes sociais (ex: JAMAIS use 'CazéTV', 'Globo', 'YouTube').\n"
         "3. Mantenha um nível intermediário de abstração (2 a 4 palavras).\n"
         "4. A CATEGORIA DEVE SER ÚNICA, SEM DOIS TEMAS EM UMA MESMA CATEGORIA.\n"
         "Sua resposta deve ser ESTRITAMENTE um objeto JSON válido.\n\n"
         "Nome das classes deve ser em português Brasileiro, neutro e sem termos de marcas ou clientes.\n"
         "{format_instructions}"),
        ("human", "Crie nomes de categorias úteis e neutros para estes grupos:\n{dados_grupos}")
    ])

    resposta_raw = (prompt | llm).invoke({
        "format_instructions": parser.get_format_instructions(),
        "dados_grupos": json.dumps(grupos_amostras, ensure_ascii=False),
        "descricao_cliente": descricao_cliente if descricao_cliente else "Não informado",
        "descricao_projeto": descricao_projeto if descricao_projeto else "Não informado"
    }).content

    match = re.search(r'\{.*\}', str(resposta_raw), re.DOTALL)
    texto_json_limpo = match.group(0) if match else str(resposta_raw)
    resultado_llm = parser.parse(texto_json_limpo)

    lista_categorias = resultado_llm.get('categorias', []) if isinstance(resultado_llm, dict) else (resultado_llm if isinstance(resultado_llm, list) else [])

    mapa_categorias = {
        cat['cluster_id']: cat['nome_categoria']
        for cat in lista_categorias
        if isinstance(cat, dict) and 'cluster_id' in cat
    }

    df_validos['categoria_tema'] = df_validos['cluster_id'].map(mapa_categorias)

    # 7. Merge mantendo a estrutura da tabela original
    df_chaves = df_validos[[coluna_id, 'cluster_id', 'categoria_tema']]
    df_output = pd.merge(df_input, df_chaves, on=coluna_id, how='left')

    df_output['categoria_tema'] = df_output['categoria_tema'].fillna('Outros')
    df_output.drop(columns=['cluster_id'], inplace=True)

    return df_output


# Execução do script
df_input = pd.read_excel(r"C:\Users\gabri\Downloads\data.xlsx")

df_resultado = classificar_tabela_input(
    df_input=df_input,
    coluna_id="id_post",
    coluna_texto="texto",
    openrouter_api_key="",
    descricao_cliente="Empresa do setor bancário focada em serviços digitais.",
    descricao_projeto="Projeto monitoramento do Indice popularidade digital das marcas streaming e televisão."
)

df_resultado.to_excel("planilha_categorizada.xlsx", index=False)