import json
import re
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import euclidean_distances
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

# Inicializa o modelo de embeddings (salva em cache local automaticamente)
modelo_embedding = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

# Lista fixa de categorias permitidas
CATEGORIAS_PADRAO = [
    "Política",
    "Economia",
    "Segurança Pública",
    "Brasil",
    "Mundo",
    "Saúde",
    "Educação",
    "Meio Ambiente",
    "Cidadania",
    "Cultura",
    "Entretenimento",
    "Música",
    "Variedades / Fama",
    "Tecnologia",
    "Games",
    "Ciência",
    "Futebol",
    "Outros Esportes",
    "Automobilismo",
    "Fitness",
    "Finanças e Carreira",
    "Gastronomia",
    "Turismo e Viagens",
    "Moda e Beleza",
    "Outros"
]


class Categoria(BaseModel):
    cluster_id: int = Field(description="ID do grupo")
    nome_categoria: str = Field(description="Nome EXATO escolhido estritamente da lista de categorias permitidas")
    descricao: str = Field(description="Resumo do tema em 1 frase")

class ResultadoCategorias(BaseModel):
    categorias: list[Categoria]


def encontrar_k_otimo_cotovelo(X, k_min=5, k_max=25):
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
    coluna_perfil: str = None,
    openrouter_api_key: str = '',
    categorias_permitidas: list[str] = None,
    max_retries: int = 5
) -> pd.DataFrame:
    """Classifica a tabela cruzando texto e perfil via Embeddings Semânticos + K-Means + IA."""
    if categorias_permitidas is None:
        categorias_permitidas = CATEGORIAS_PADRAO

    if coluna_id not in df_input.columns or coluna_texto not in df_input.columns:
        raise KeyError(f"As colunas '{coluna_id}' e/ou '{coluna_texto}' não existem na tabela de entrada.")

    has_perfil = coluna_perfil and coluna_perfil in df_input.columns

    # 1. Isola posts válidos
    df_validos = df_input[
        df_input[coluna_id].notna() & 
        df_input[coluna_texto].notna() & 
        (df_input[coluna_texto].astype(str).str.strip() != '')
    ].copy()

    textos = df_validos[coluna_texto].astype(str).tolist()
    if not textos:
        raise ValueError("Nenhum post válido encontrado para processamento.")

    # 2. Vetorização por Embeddings Semânticos
    print("🧠 Gerando embeddings semânticos...")
    X = modelo_embedding.encode(textos, batch_size=64, show_progress_bar=True)

    # 3. Definição do K ótimo via Cotovelo
    k_otimo = encontrar_k_otimo_cotovelo(X, k_min=5, k_max=25)

    # 4. K-Means
    km_final = KMeans(n_clusters=k_otimo, random_state=42, n_init=5)
    labels = km_final.fit_predict(X)
    centroides = km_final.cluster_centers_

    df_validos['cluster_id'] = labels

    # 5. Amostras centrais por centroide (com perfil se disponível)
    grupos_amostras = {}
    for cid in range(k_otimo):
        indices_cluster = np.where(labels == cid)[0]
        if len(indices_cluster) == 0:
            continue

        X_cluster = X[indices_cluster]
        distancias = euclidean_distances(X_cluster, [centroides[cid]]).flatten()
        indices_ordenados = indices_cluster[np.argsort(distancias)]

        top_indices = indices_ordenados[:20]
        
        amostras_grupo = []
        for idx in top_indices:
            texto_item = textos[idx]
            if has_perfil:
                perfil_val = str(df_validos.iloc[idx][coluna_perfil]).strip()
                amostras_grupo.append(f"[{perfil_val}] {texto_item}")
            else:
                amostras_grupo.append(texto_item)
                
        grupos_amostras[cid] = amostras_grupo

    # 6. Rotulagem das classes via IA com Prompt Objetivo e Loop de Retry
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
         "Classifique grupos de postagens (com [Perfil] e texto) em EXATAMENTE UMA categoria da lista permitida.\n\n"
         "CATEGORIAS PERMITIDAS:\n"
         "{lista_categorias}\n\n"
         "REGRAS:\n"
         "1. O campo 'nome_categoria' deve usar EXATAMENTE uma das opções da lista acima.\n"
         "2. PROIBIDO criar novos nomes de categoria ou alterar a grafia original.\n"
         "3. Escolha 'Outros' apenas se o grupo não possuir um tema claro e predominante.\n"
         "4. Retorne APENAS um objeto JSON válido no formato solicitado.\n\n"
         "{format_instructions}"),
        ("human", "Classifique os seguintes grupos de posts:\n{dados_grupos}")
    ])

    lista_formatada = "\n".join([f"- {cat}" for cat in categorias_permitidas])

    resultado_llm = None
    for tentativa in range(1, max_retries + 1):
        try:
            print(f"🚀 Enviando requisição para a LLM (Tentativa {tentativa}/{max_retries})...")
            
            resposta_raw = (prompt | llm).invoke({
                "format_instructions": parser.get_format_instructions(),
                "dados_grupos": json.dumps(grupos_amostras, ensure_ascii=False),
                "lista_categorias": lista_formatada
            }).content

            match = re.search(r'\{.*\}', str(resposta_raw), re.DOTALL)
            texto_json_limpo = match.group(0) if match else str(resposta_raw)
            
            resultado_llm = parser.parse(texto_json_limpo)
            print(f"✅ Processamento concluído com sucesso na tentativa {tentativa}.")
            break
        except Exception as e:
            print(f"⚠️ Tentativa {tentativa}/{max_retries} falhou ao gerar JSON válido: {e}")
            if tentativa == max_retries:
                raise RuntimeError("Falha ao obter um JSON válido da IA após atingir o limite de tentativas.") from e

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
    coluna_perfil="profile",
    openrouter_api_key="",
    max_retries=5
)

df_resultado.to_excel("planilha_categorizada.xlsx", index=False)