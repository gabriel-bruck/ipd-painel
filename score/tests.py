import json
import re
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_samples
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

# Tentativa de importar UMAP; se não instalado, utiliza PCA do sklearn como fallback
try:
    import umap
    HAS_UMAP = True
except ImportError:
    from sklearn.decomposition import PCA
    HAS_UMAP = False

# -----------------------------------------------------------------------------
# 1. INICIALIZAÇÃO DO MODELO DE EMBEDDINGS
# -----------------------------------------------------------------------------
modelo_embedding = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

# -----------------------------------------------------------------------------
# 2. DICIONÁRIO DE CATEGORIAS
# -----------------------------------------------------------------------------
CATEGORIAS_DETALHADAS = {
    "Política": {
        "definicao": "Governo, eleições, partidos, decisões de congressistas, leis e debates políticos.",
        "exemplo": "Votação da reforma na câmara dos deputados, discursos de candidatos."
    },
    "Economia": {
        "definicao": "Inflação, taxa de juros, mercados financeiros, PIB, comércio e políticas econômicas.",
        "exemplo": "Banco Central altera a taxa Selic, variação do dólar, indicadores de inflação."
    },
    "Segurança Pública": {
        "definicao": "Crime, operações policiais, violência urbana, sistema prisional e segurança nacional.",
        "exemplo": "Operação da Polícia Federal, estatísticas de roubos na capital."
    },
    "Brasil": {
        "definicao": "Acontecimentos gerais de impacto nacional que não se enquadram em áreas específicas.",
        "exemplo": "Censo demográfico do IBGE, feriados nacionais, eventos comemorativos pelo país."
    },
    "Mundo": {
        "definicao": "Relações internacionais, geopolítica, guerras, conflitos e eventos em outros países.",
        "exemplo": "Eleições nos EUA, acordos da União Europeia, conflitos geopolíticos."
    },
    "Saúde": {
        "definicao": "Medicina, sistemas de saúde, vacinação, epidemias, bem-estar físico e mental.",
        "exemplo": "Campanha de vacinação contra a gripe, pesquisas sobre tratamentos do câncer."
    },
    "Educação": {
        "definicao": "Escolas, universidades, ENEM, alfabetização, métodos de ensino e políticas educacionais.",
        "exemplo": "Abertura das inscrições para o Sisu, reformas no ensino médio."
    },
    "Meio Ambiente": {
        "definicao": "Sustentabilidade, mudanças climáticas, desmatamento, fauna, flora e recursos naturais.",
        "exemplo": "Conferência do clima da ONU, metas de redução de emissão de carbono."
    },
    "Cidadania": {
        "definicao": "Direitos humanos, inclusão social, deveres civis e assistência social.",
        "exemplo": "Programas de acessibilidade urbana, campanhas contra a discriminação."
    },
    "Cultura": {
        "definicao": "Arte, literatura, teatro, museus, patrimônio histórico e manifestações culturais.",
        "exemplo": "Lançamento de livros, exposições em museus, peças teatrais."
    },
    "Entretenimento": {
        "definicao": "Cinema, séries de TV, streaming, realities e universo pop em geral.",
        "exemplo": "Estreia de novo filme de super-herói, término de temporada de série."
    },
    "Música": {
        "definicao": "Lançamento de álbuns, shows, festivais, bandas e artistas musicais.",
        "exemplo": "Line-up de festival de música, novo single de artista famoso."
    },
    "Variedades / Fama": {
        "definicao": "Fofocas de celebridades, influenciadores digitais, curiosidades e vida pessoal de famosos.",
        "exemplo": "Bastidores de festas de influenciadores, término de namoro de artistas."
    },
    "Tecnologia": {
        "definicao": "Inteligência artificial, smartphones, software, cibersegurança e inovação tecnológica.",
        "exemplo": "Lançamento de novos modelos de smartphones, atualizações de IA."
    },
    "Games": {
        "definicao": "Jogos eletrônicos, consoles, eSports, lançamentos e indústria dos videogames.",
        "exemplo": "Campeonato mundial de League of Legends, anúncio de novo console."
    },
    "Ciência": {
        "definicao": "Descobertas científicas, astronomia, física, biologia e exploração espacial.",
        "exemplo": "Imagens capturadas por telescópios espaciais, descoberta de nova espécie."
    },
    "Futebol": {
        "definicao": "Notícias, jogos, transferências, campeonatos e clubes de futebol.",
        "exemplo": "Resultado da final da Libertadores, contratação de jogador por clube."
    },
    "Outros Esportes": {
        "definicao": "Basquete, vôlei, tênis, atletismo, natação e demais modalidades esportivas.",
        "exemplo": "Etapa do circuito mundial de surfe, partidas da NBA."
    },
    "Automobilismo": {
        "definicao": "Fórmula 1, corridas de carros, motos, ralis e esportes a motor em geral.",
        "exemplo": "Grande Prêmio de Fórmula 1, treino classificatório de Stock Car."
    },
    "Fitness": {
        "definicao": "Exercícios físicos, musculação, rotina de treinos, vida saudável e nutrição esportiva.",
        "exemplo": "Dicas de treino hipertrofia, suplementação alimentar para atletas."
    },
    "Finanças e Carreira": {
        "definicao": "Investimentos pessoais, mercado de trabalho, empreendedorismo e finanças pessoais.",
        "exemplo": "Dicas para organizar o orçamento doméstico, vagas de emprego em alta."
    },
    "Gastronomia": {
        "definicao": "Culinária, receitas, restaurantes, alta gastronomia e experiências gastronômicas.",
        "exemplo": "Receitas para o fim de semana, avaliação de restaurantes renomados."
    },
    "Turismo e Viagens": {
        "definicao": "Destinos de viagem, companhias aéreas, hotéis e dicas para viajantes.",
        "exemplo": "Pacotes de viagem para as férias, dicas para emissão de passagens."
    },
    "Moda e Beleza": {
        "definicao": "Tendências de vestuário, maquiagem, cuidados com a pele (skincare) e desfiles.",
        "exemplo": "Tendências de roupas para o inverno, rotina de cuidados com a pele."
    },
    "Outros": {
        "definicao": "Postagens sem tema claro, irrelevantes ou que não se encaixam nas categorias acima.",
        "exemplo": "Textos aleatórios sem contexto claro ou posts muito genéricos."
    }
}

# -----------------------------------------------------------------------------
# 3. ESTRUTURA PYDANTIC PARA ROTULAGEM DOS CENTROIDS
# -----------------------------------------------------------------------------
class CategoriaCluster(BaseModel):
    cluster_id: int = Field(description="ID do grupo")
    nome_categoria: str = Field(description="Nome EXATO escolhido estritamente da lista de categorias permitidas")
    descricao: str = Field(description="Resumo do tema em 1 frase")

class ResultadoClusters(BaseModel):
    categorias: list[CategoriaCluster]

# -----------------------------------------------------------------------------
# 4. FUNÇÕES AUXILIARES
# -----------------------------------------------------------------------------
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

def calcular_confianca_centroids(X, kmeans_model):
    """
    Calcula a probabilidade de um ponto pertencer ao centroid escolhido
    usando a inversa das distâncias euclidianas + Softmax.
    """
    # Matriz de distâncias de cada ponto para TODOS os centroids (N_amostras, K_clusters)
    distancias = kmeans_model.transform(X)
    
    # Inverte a distância com Softmax negativo: quanto menor a distância, maior a probabilidade
    # Multiplica-se por um fator de escala para acentuar a confiança nos pontos mais próximos
    exp_dist = np.exp(-distancias / np.std(distancias))
    probs = exp_dist / np.sum(exp_dist, axis=1, keepdims=True)
    
    # Retorna a maior probabilidade para o cluster onde o ponto caiu
    confianca_maxima = np.max(probs, axis=1)
    return confianca_maxima

# -----------------------------------------------------------------------------
# 5. PIPELINE PRINCIPAL BASEADO EM CENTROIDS
# -----------------------------------------------------------------------------
def classificar_tabela_centroids(
    df_input: pd.DataFrame,
    coluna_id: str = 'id_post',
    coluna_texto: str = 'texto',
    coluna_perfil: str = 'profile',
    openrouter_api_key: str = '',
    n_componentes_reducao: int = 15,
    categorias_detalhadas: dict = None
) -> pd.DataFrame:
    
    if categorias_detalhadas is None:
        categorias_detalhadas = CATEGORIAS_DETALHADAS

    print("\n" + "="*80)
    print(" 🚀 INICIANDO CLASSIFICAÇÃO EXCLUSIVA POR CENTROIDS ")
    print("="*80)

    # --- ETAPA 1: Validação de Dados ---
    print("\n[Etapa 1/5] 📋 Preparando tabela de entrada...")
    df_validos = df_input[
        df_input[coluna_id].notna() & 
        df_input[coluna_texto].notna() & 
        (df_input[coluna_texto].astype(str).str.strip() != '')
    ].copy()

    df_validos[coluna_id] = df_validos[coluna_id].astype(str)
    textos = df_validos[coluna_texto].astype(str).tolist()
    has_perfil = coluna_perfil and coluna_perfil in df_input.columns

    # --- ETAPA 2: Embeddings & Redução de Dimensionalidade ---
    print("\n[Etapa 2/5] 🧠 Gerando embeddings e reduzindo dimensões...")
    X_raw = modelo_embedding.encode(textos, batch_size=64, show_progress_bar=True)
    
    # Redução de dimensão antes do K-Means
    if HAS_UMAP:
        print(f"  ✓ Aplicando redução de dimensionalidade UMAP para {n_componentes_reducao} componentes...")
        reducer = umap.UMAP(n_components=n_componentes_reducao, random_state=42)
        X = reducer.fit_transform(X_raw)
    else:
        print(f"  ✓ Aplicando redução de dimensionalidade PCA (UMAP não instalado) para {n_componentes_reducao} componentes...")
        reducer = PCA(n_components=n_componentes_reducao, random_state=42)
        X = reducer.fit_transform(X_raw)

    # --- ETAPA 3: K-Means & Métricas dos Centroids ---
    print("\n[Etapa 3/5] 🧩 Agrupando via K-Means e calculando confiança...")
    k_otimo = encontrar_k_otimo_cotovelo(X, k_min=5, k_max=25)
    print(f"  ✓ Número de centroids calculado (Método Cotovelo): K = {k_otimo}")
    
    km_final = KMeans(n_clusters=k_otimo, random_state=42, n_init=5)
    labels = km_final.fit_predict(X)
    
    df_validos['cluster_id'] = labels
    df_validos['qualidade_silhueta'] = silhouette_samples(X, labels)
    df_validos['taxa_confianca'] = calcular_confianca_centroids(X, km_final)

    # --- ETAPA 4: LLM Identifica a Categoria dos Centroids ---
    print("\n[Etapa 4/5] 🤖 Identificando categorias dos Centroids via LLM...")
    llm = ChatOpenAI(
        model="google/gemini-3.5-flash-lite",
        openai_api_key=openrouter_api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.1,
        request_timeout=400
    )

    linhas_cat = [f"- CATEGORIA: {k}\n  DEFINIÇÃO: {v['definicao']}\n  EXEMPLO: {v['exemplo']}" for k, v in categorias_detalhadas.items()]
    lista_formatada = "\n\n".join(linhas_cat)

    # Amostramos os posts mais próximos de cada centroid (com base no Silhouette score)
    grupos_amostras = {}
    for cid in np.unique(labels):
        sub = df_validos[df_validos['cluster_id'] == cid].sort_values(by='qualidade_silhueta', ascending=False)
        top_indices = sub.index[:15]
        
        amostras = []
        for idx in top_indices:
            txt = df_validos.loc[idx, coluna_texto]
            pref = f"[{str(df_validos.loc[idx, coluna_perfil]).strip()}] " if has_perfil else ""
            amostras.append(f"{pref}{txt}")
            
        grupos_amostras[int(cid)] = amostras

    parser_cluster = JsonOutputParser(pydantic_object=ResultadoClusters)
    prompt_cluster = ChatPromptTemplate.from_messages([
        ("system", 
         "Classifique os grupos de postagens abaixo escolhendo EXATAMENTE UMA categoria da lista fornecida para cada grupo, só repita se inevitavelmente\n\n"
         "LISTA DE CATEGORIAS:\n{lista_categorias}\n\n"
         "REGRAS:\n1. O campo 'nome_categoria' deve conter EXATAMENTE o nome de uma das categorias acima.\n2. Retorne APENAS o JSON no formato solicitado.\n\n{format_instructions}"),
        ("human", "Classifique estes grupos de posts (centroids):\n{dados_grupos}")
    ])

    res_raw = (prompt_cluster | llm).invoke({
        "format_instructions": parser_cluster.get_format_instructions(),
        "dados_grupos": json.dumps(grupos_amostras, ensure_ascii=False),
        "lista_categorias": lista_formatada
    }).content
    
    match = re.search(r'\{.*\}', str(res_raw), re.DOTALL)
    res_json = parser_cluster.parse(match.group(0) if match else str(res_raw))
    
    cats = res_json.get('categorias', []) if isinstance(res_json, dict) else res_json
    mapa_categorias_clusters = {c['cluster_id']: c['nome_categoria'] for c in cats if isinstance(c, dict)}

    # Atribuição da categoria aos centroids
    df_validos['categoria_tema'] = df_validos['cluster_id'].map(mapa_categorias_clusters).fillna('Outros')

    # --- ETAPA 5: Consolidação Final ---
    print("\n[Etapa 5/5] 📦 Unindo resultados e ajustando formato...")
    df_chaves = df_validos[[coluna_id, 'cluster_id', 'categoria_tema', 'taxa_confianca', 'qualidade_silhueta']]
    
    df_input[coluna_id] = df_input[coluna_id].astype(str)
    df_output = pd.merge(df_input, df_chaves, on=coluna_id, how='left')

    # Formata a taxa de confiança em porcentagem string (ex: "87.4%")
    df_output['taxa_confianca_pct'] = (df_output['taxa_confianca'] * 100).round(2).astype(str) + "%"

    print("\n" + "="*80)
    print(" ✨ PROCESSAMENTO CONCLUÍDO COM SUCESSO! ")
    print("="*80 + "\n")
    return df_output

# -----------------------------------------------------------------------------
# 6. EXECUÇÃO DO SCRIPT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    caminho_entrada = r"C:\Users\gabri\Desktop\itau.xlsx"
    df_input = pd.read_excel(caminho_entrada)

    df_resultado = classificar_tabela_centroids(
        df_input=df_input,
        coluna_id="id_post",
        coluna_texto="texto",
        coluna_perfil="profile",
        openrouter_api_key="",
        n_componentes_reducao=30  # Reduz a dimensão para 15 componentes antes do K-Means
    )

    caminho_saida_csv = "planilha_categorizada.csv"
    df_resultado.to_csv(caminho_saida_csv, index=False, encoding='utf-8-sig', sep=',')
    print(f"📁 Arquivo salvo em: {caminho_saida_csv}")