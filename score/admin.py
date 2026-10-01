from datetime import datetime, date, timedelta, time

import hashlib

from decimal import Decimal, InvalidOperation

import re

import csv

import traceback



from django import forms

from django.contrib import admin, messages

from django.contrib.admin.actions import delete_selected

from django.contrib.admin.views.main import ChangeList

from django.core.cache import cache

from django.core.exceptions import PermissionDenied, ValidationError

from django.db import transaction, models

from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import redirect

from django.template import engines

from django.template.response import TemplateResponse

from django.urls import path, reverse

from django.utils import timezone

from django.conf import settings

from django.utils.safestring import mark_safe



from tablib import Dataset

from import_export import resources, fields

from import_export.admin import ImportExportModelAdmin

from import_export.forms import ImportForm, ConfirmImportForm

from import_export.widgets import (

    ForeignKeyWidget,

    ManyToManyWidget,

    IntegerWidget,

)



from .models import IPD, Conteudo, ResumoExecutivo

from client.models import ProjetoIPD, ProjetoCliente, ProjetoClienteIPD





# =============================================================================

# WIDGETS

# =============================================================================



class SmartForeignKeyWidget(ForeignKeyWidget):

    """Aceita tanto ID numérico quanto nome do projeto."""

    def clean(self, value, row=None, *args, **kwargs):

        if not value:

            return None

        val_str = str(value).strip()

        if val_str.isdigit():

            return self.model.objects.filter(pk=int(val_str)).first()

        return self.model.objects.filter(nome__iexact=val_str).first()





class SmartManyToManyWidget(ManyToManyWidget):

    """Aceita IDs ou nomes separados por vírgula ou ponto e vírgula."""

    def clean(self, value, row=None, *args, **kwargs):

        if not value:

            return self.model.objects.none()



        raw_values = [

            valor.strip()

            for valor in str(value).replace(';', ',').split(',')

            if valor.strip()

        ]



        pks = []

        names = []



        for valor in raw_values:

            if valor.isdigit():

                pks.append(int(valor))

            else:

                names.append(valor)



        qs_pk = (

            self.model.objects.filter(pk__in=pks)

            if pks

            else self.model.objects.none()

        )



        qs_name = (

            self.model.objects.filter(nome__in=names)

            if names

            else self.model.objects.none()

        )



        return (qs_pk | qs_name).distinct()





# =============================================================================

# FUNÇÃO AUXILIAR DE DATA

# =============================================================================



def normalizar_data_importacao(valor):
    """Normaliza datas vindas de CSV/Excel.

    Proteções mantidas:
    - ignora hora em valores como 2026-02-24 13:45:00 ou 2026-02-24T13:45:00;
    - aceita date/datetime do XLSX;
    - aceita dd/mm/aaaa, dd-mm-aaaa, aaaa-mm-dd e aaaa/mm/dd;
    - aceita número serial do Excel quando vier como número;
    - aceita aaaaMMdd quando vier como número/texto, inclusive se o CSV transformar em notação científica.

    Retorna string em YYYY-MM-DD quando consegue normalizar.
    Se não conseguir, retorna o valor limpo para o import-export acusar o erro da linha.
    """
    if valor in (None, ''):
        return valor

    if isinstance(valor, datetime):
        return valor.date().strftime('%Y-%m-%d')

    if isinstance(valor, date):
        return valor.strftime('%Y-%m-%d')

    # Datas que vierem como número do Excel ou como 20260224 / 2.0260224E7.
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        try:
            numero = Decimal(str(valor)).normalize()
            if numero.is_finite():
                inteiro = int(numero.to_integral_value())

                # Serial de data do Excel/LibreOffice. 20000-80000 cobre datas modernas.
                if Decimal(inteiro) == numero.to_integral_value() and 20000 <= inteiro <= 80000:
                    data_excel = date(1899, 12, 30) + timedelta(days=inteiro)
                    return data_excel.strftime('%Y-%m-%d')

                # Formato aaaaMMdd.
                inteiro_str = str(inteiro)
                if len(inteiro_str) == 8:
                    try:
                        return datetime.strptime(inteiro_str, '%Y%m%d').date().strftime('%Y-%m-%d')
                    except ValueError:
                        pass
        except Exception:
            pass

    data_str = str(valor).strip().lstrip('\ufeff')

    if not data_str:
        return data_str

    # Se vier em notação científica por conversão do CSV, tenta recuperar número inteiro.
    if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)', data_str):
        try:
            numero = Decimal(data_str)
            inteiro = int(numero.to_integral_value())
            inteiro_str = str(inteiro)
            if len(inteiro_str) == 8:
                return datetime.strptime(inteiro_str, '%Y%m%d').date().strftime('%Y-%m-%d')
            if 20000 <= inteiro <= 80000:
                data_excel = date(1899, 12, 30) + timedelta(days=inteiro)
                return data_excel.strftime('%Y-%m-%d')
        except Exception:
            pass

    # Remove hora sem perder data.
    # ATENCAO: nao use `if 'T' in data_str` puro, porque UTC contem a letra T.
    # Primeiro tenta extrair a data real de dentro do texto.
    match_iso = re.search(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b', data_str)
    match_br = re.search(r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b', data_str)

    if match_iso:
        data_str = match_iso.group(0)
    elif match_br:
        data_str = match_br.group(0)
    elif ' ' in data_str:
        data_str = data_str.split(' ')[0]
    elif re.search(r'\dT\d', data_str):
        data_str = data_str.split('T', 1)[0]

    data_str = data_str.strip().replace('/', '-')

    # Remove .0 de valores que vierem do Excel/CSV como texto.
    if re.fullmatch(r'\d+\.0+', data_str):
        data_str = data_str.split('.')[0]

    # aaaaMMdd em texto.
    if re.fullmatch(r'\d{8}', data_str):
        try:
            return datetime.strptime(data_str, '%Y%m%d').date().strftime('%Y-%m-%d')
        except ValueError:
            pass

    if '-' in data_str:
        partes = data_str.split('-')
        if len(partes) == 3:
            # dd-mm-aaaa
            if len(partes[2]) == 4:
                candidato = f"{partes[2]}-{partes[1].zfill(2)}-{partes[0].zfill(2)}"
                try:
                    return datetime.strptime(candidato, '%Y-%m-%d').date().strftime('%Y-%m-%d')
                except ValueError:
                    return candidato

            # aaaa-mm-dd
            if len(partes[0]) == 4:
                candidato = f"{partes[0]}-{partes[1].zfill(2)}-{partes[2].zfill(2)}"
                try:
                    return datetime.strptime(candidato, '%Y-%m-%d').date().strftime('%Y-%m-%d')
                except ValueError:
                    return candidato

    return data_str


def normalizar_nome_coluna_importacao(nome):
    """Normaliza nomes de colunas para CSV/XLSX antes da validação e importação."""
    if nome is None:
        return ''

    texto = str(nome).strip().lstrip('\ufeff').lower()
    texto = texto.replace('-', '_').replace(' ', '_')
    texto = re.sub(r'_+', '_', texto).strip('_')

    aliases = {
        'pd': 'projeto_ipd',
        'projeto': 'projeto_ipd',
        'projeto_ipd': 'projeto_ipd',
        'projeto_ipd_id': 'projeto_ipd',
        'id_projeto_ipd': 'projeto_ipd',
        'id_projeto': 'projeto_ipd',

        'data': 'data',
        'date': 'data',
        'dt': 'data',
        'dia': 'data',

        'profile': 'profile',
        'perfil': 'profile',
        'usuario': 'profile',
        'user': 'profile',

        'idpost': 'id_post',
        'id_post': 'id_post',
        'post_id': 'id_post',
        'id_do_post': 'id_post',

        'curtida': 'curtidas',
        'curtidas': 'curtidas',
        'likes': 'curtidas',

        'comentario': 'comentarios',
        'comentarios': 'comentarios',
        'comentários': 'comentarios',
        'comments': 'comentarios',
    }

    return aliases.get(texto, texto)


def carregar_csv_texto_em_dataset(texto):
    """
    Carrega CSV manualmente para conseguir corrigir arquivos exportados
    com a linha inteira dentro de uma única célula.

    Exemplo real que isso corrige:
    header normal: projeto_ipd,profile,pd,...,data
    linha: "3,Dove,5,4,4,...,2025-07-01 00:00:00 UTC"

    O csv padrão lê essa linha como 1 coluna só. Depois a função
    normalizar_headers_dataset abre essa coluna interna e transforma em 10 colunas.
    """
    linhas_texto = texto.splitlines()
    linhas_validas = [linha for linha in linhas_texto if str(linha).strip()]

    dataset = Dataset()

    if not linhas_validas:
        return dataset

    primeira_linha = linhas_validas[0]
    delimitador = ';' if primeira_linha.count(';') > primeira_linha.count(',') else ','

    reader = csv.reader(linhas_validas, delimiter=delimitador)

    try:
        headers = next(reader)
    except StopIteration:
        return dataset

    dataset.headers = headers

    for row in reader:
        dataset.append(row)

    return dataset


def reparar_linha_importacao(row, qtd_colunas):
    """
    Corrige linhas que chegaram com CSV inteiro dentro da primeira coluna.

    Cobre dois casos comuns:
    1) A linha veio com apenas 1 coluna:
       ["3,Dove,5,4,4,4,4,4,\"83,32594444\",2025-07-01 00:00:00 UTC"]

    2) A linha veio com o número certo de colunas no tablib/pandas, mas só a
       primeira coluna tem conteúdo e as demais estão vazias/None.

    Esse segundo caso era o motivo de a planilha passar 100% sem aviso:
    o header existia, mas row['data'] ficava vazio porque a data estava presa
    dentro da primeira célula.
    """
    valores = list(row)

    primeira_coluna = str(valores[0]).strip() if valores else ''
    resto_vazio = True

    if len(valores) > 1:
        resto_vazio = all(
            v is None or str(v).strip() == ''
            for v in valores[1:]
        )

    linha_parece_csv_embutido = (
        qtd_colunas > 1
        and primeira_coluna
        and (len(valores) == 1 or resto_vazio)
        and (',' in primeira_coluna or ';' in primeira_coluna)
    )

    if linha_parece_csv_embutido:
        for delimitador in (',', ';'):
            try:
                reparada = next(csv.reader([primeira_coluna], delimiter=delimitador))
            except Exception:
                continue

            if len(reparada) == qtd_colunas:
                return reparada

    if len(valores) < qtd_colunas:
        valores = valores + [''] * (qtd_colunas - len(valores))
    elif len(valores) > qtd_colunas:
        valores = valores[:qtd_colunas]

    return valores


def normalizar_headers_dataset(dataset):
    """
    Normaliza headers e também corrige linhas malformed.

    Resolve:
    - Data, DATA, data com espaço, BOM;
    - projeto ipd, projeto-ipd, projeto_ipd_id, pd;
    - Perfil -> profile;
    - comentários -> comentarios;
    - CSV em que a linha inteira veio dentro de uma única célula.
    """
    if not dataset.headers:
        return dataset

    headers_antigos = list(dataset.headers)
    headers_novos = []
    indices_validos = []
    usados = {}

    for i, header in enumerate(headers_antigos):
        h_norm = normalizar_nome_coluna_importacao(header)

        if not h_norm:
            continue

        if h_norm in usados:
            usados[h_norm] += 1
            h_norm = f'{h_norm}_{usados[h_norm]}'
        else:
            usados[h_norm] = 0

        headers_novos.append(h_norm)
        indices_validos.append(i)

    linhas = []
    qtd_headers_antigos = len(headers_antigos)

    for row in dataset:
        row_reparada = reparar_linha_importacao(row, qtd_headers_antigos)
        linhas.append([row_reparada[i] if i < len(row_reparada) else '' for i in indices_validos])

    dataset.wipe()
    dataset.headers = headers_novos

    for row in linhas:
        dataset.append(row)

    return dataset


def carregar_dataset_importacao(arquivo):
    """
    Carrega CSV, XLSX ou XLS em tablib.Dataset.

    CSV:
    - tenta utf-8-sig, utf-8, cp1252 e latin-1.

    XLSX/XLS:
    - usa tablib; para XLSX, confirme openpyxl no requirements.txt.
    """
    nome = getattr(arquivo, 'name', '') or ''
    nome_lower = nome.lower().strip()

    dataset = Dataset()
    conteudo = arquivo.read()

    if nome_lower.endswith('.xlsx'):
        try:
            dataset.load(conteudo, format='xlsx')
        except Exception as exc:
            raise ValueError(
                'Não foi possível ler o XLSX. Confirme se o arquivo está salvo como .xlsx válido '
                'e se o pacote openpyxl está instalado no ambiente.'
            ) from exc
        return normalizar_headers_dataset(dataset)

    if nome_lower.endswith('.xls'):
        try:
            dataset.load(conteudo, format='xls')
        except Exception as exc:
            raise ValueError(
                'Não foi possível ler o XLS. Se der erro, salve o arquivo como .xlsx ou .csv.'
            ) from exc
        return normalizar_headers_dataset(dataset)

    if nome_lower.endswith('.csv') or not nome_lower:
        texto = None
        for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
            try:
                texto = conteudo.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if texto is None:
            raise ValueError('Não foi possível ler o CSV. Salve o arquivo em UTF-8 ou CP1252/Latin-1.')

        dataset = carregar_csv_texto_em_dataset(texto)
        return normalizar_headers_dataset(dataset)

    raise ValueError('Formato não suportado. Envie um arquivo .csv, .xlsx ou .xls.')


def parse_data_importacao_para_date(valor):
    """
    Converte data do CSV/XLSX para date.

    Proteções mantidas:
    - ignora hora;
    - aceita datetime/date nativo do XLSX;
    - aceita serial numérico do Excel;
    - aceita data em notação científica quando for aaaammdd ou serial;
    - aceita dd/mm/aaaa, dd-mm-aaaa, aaaa-mm-dd e aaaa/mm/dd;
    - aceita sufixos como UTC sem quebrar o parse.

    Correção importante:
    - Não usa mais `if 'T' in texto` de forma cega, porque a palavra UTC
      contém a letra T e quebrava datas como `2025-07-01 00:00:00 UTC`.
    """
    if valor in (None, ''):
        return None

    if isinstance(valor, datetime):
        return valor.date()

    if isinstance(valor, date):
        return valor

    # Serial do Excel ou aaaammdd numérico.
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        try:
            numero = Decimal(str(valor))
            inteiro = int(numero.to_integral_value())
            if 20000 <= inteiro <= 80000:
                return date(1899, 12, 30) + timedelta(days=inteiro)
            inteiro_str = str(inteiro)
            if len(inteiro_str) == 8:
                return datetime.strptime(inteiro_str, '%Y%m%d').date()
        except Exception:
            pass

    texto_original = str(valor).strip().lstrip('\ufeff')

    if not texto_original:
        return None

    texto = texto_original.strip()

    # Se vier notação científica, tenta recuperar número inteiro.
    if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)', texto):
        try:
            numero = Decimal(texto)
            inteiro = int(numero.to_integral_value())
            inteiro_str = str(inteiro)
            if len(inteiro_str) == 8:
                return datetime.strptime(inteiro_str, '%Y%m%d').date()
            if 20000 <= inteiro <= 80000:
                return date(1899, 12, 30) + timedelta(days=inteiro)
        except Exception:
            pass

    # Número puro em texto: pode ser serial do Excel ou aaaammdd.
    if re.fullmatch(r'\d+(?:\.0+)?', texto):
        try:
            inteiro = int(Decimal(texto).to_integral_value())
            if 20000 <= inteiro <= 80000:
                return date(1899, 12, 30) + timedelta(days=inteiro)
            inteiro_str = str(inteiro)
            if len(inteiro_str) == 8:
                return datetime.strptime(inteiro_str, '%Y%m%d').date()
        except Exception:
            pass

    # Primeiro tenta extrair uma data ISO dentro do texto.
    # Ex: 2025-07-01 00:00:00 UTC -> 2025-07-01
    match_iso = re.search(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b', texto)
    if match_iso:
        candidato = match_iso.group(0).replace('/', '-')
        try:
            return datetime.strptime(candidato, '%Y-%m-%d').date()
        except Exception:
            pass

    # Depois tenta extrair data brasileira dentro do texto.
    # Ex: 01/07/2025 00:00:00 UTC -> 01/07/2025
    match_br = re.search(r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b', texto)
    if match_br:
        candidato = match_br.group(0).replace('/', '-')
        for formato in ('%d-%m-%Y', '%d-%m-%y'):
            try:
                return datetime.strptime(candidato, formato).date()
            except Exception:
                pass

    # Remove hora somente quando houver separador real de data/hora.
    texto_sem_hora = texto
    if ' ' in texto_sem_hora:
        texto_sem_hora = texto_sem_hora.split(' ')[0]
    elif re.search(r'\dT\d', texto_sem_hora):
        texto_sem_hora = re.split(r'T', texto_sem_hora, maxsplit=1)[0]

    texto_sem_hora = texto_sem_hora.strip().replace('/', '-')

    if re.fullmatch(r'\d+\.0+', texto_sem_hora):
        texto_sem_hora = texto_sem_hora.split('.')[0]

    for formato in ('%Y-%m-%d', '%d-%m-%Y', '%d-%m-%y', '%Y%m%d'):
        try:
            return datetime.strptime(texto_sem_hora, formato).date()
        except Exception:
            pass

    try:
        normalizada = normalizar_data_importacao(valor)
        if not normalizada:
            return None
        return datetime.strptime(str(normalizada), '%Y-%m-%d').date()
    except Exception:
        return None

def normalizar_projetos_para_buracos(valor):
    """
    Normaliza projeto_ipd para a validação de buracos de data.

    Funciona para:
    - IPD: projeto_ipd único;
    - Conteúdo: projeto_ipd único ou vários IDs separados por vírgula/ponto e vírgula;
    - IDs em notação científica.
    """
    if valor in (None, ''):
        return ('SEM_PROJETO',)

    bruto = str(valor).strip()

    if not bruto:
        return ('SEM_PROJETO',)

    partes = bruto.replace(';', ',').split(',')
    projetos = []

    for parte in partes:
        parte = parte.strip()

        if not parte:
            continue

        try:
            numero = Decimal(parte)
            if numero.is_finite():
                projetos.append(str(int(numero.to_integral_value())))
            else:
                projetos.append(parte)
        except Exception:
            try:
                projetos.append(str(int(float(parte))))
            except Exception:
                projetos.append(parte)

    return tuple(sorted(set(projetos))) or ('SEM_PROJETO',)


def formatar_datas_faltantes(datas, limite=20):
    datas = sorted(datas)

    if len(datas) <= limite:
        return [d.strftime('%d/%m/%Y') for d in datas]

    primeiras = [d.strftime('%d/%m/%Y') for d in datas[:limite]]
    primeiras.append(f'... +{len(datas) - limite} datas')
    return primeiras


def montar_buracos_datas(datas_presentes):
    if len(datas_presentes) <= 1:
        return []

    data_min = min(datas_presentes)
    data_max = max(datas_presentes)

    datas_esperadas = {
        data_min + timedelta(days=i)
        for i in range((data_max - data_min).days + 1)
    }

    return sorted(datas_esperadas - datas_presentes)


def detectar_buracos_datas_dataset(dataset):
    """
    Detecta buracos antes da importação.

    Agora faz duas checagens:
    1. Buraco geral no arquivo inteiro.
    2. Buraco por projeto_ipd + profile.

    Exemplo:
    - arquivo tem 24/02, 26/02, 27/02 e 28/02;
    - falta 25/02;
    - o admin responde 409 e só importa se o usuário confirmar.
    """
    normalizar_headers_dataset(dataset)
    headers = list(dataset.headers or [])

    if 'data' not in headers:
        return {
            'tem_buracos': False,
            'erro': f"Coluna 'data' não encontrada. Colunas lidas: {', '.join(headers)}",
            'grupos_com_buraco': [],
            'total_grupos_com_buraco': 0,
            'total_datas_faltantes': 0,
        }

    grupos = {}
    datas_gerais = set()
    linhas_com_data_invalida = []

    for indice, row in enumerate(dataset.dict, start=2):
        data_obj = parse_data_importacao_para_date(row.get('data'))

        if not data_obj:
            if row.get('data') not in (None, ''):
                linhas_com_data_invalida.append(indice)
            continue

        datas_gerais.add(data_obj)

        projetos = normalizar_projetos_para_buracos(row.get('projeto_ipd'))

        profile = row.get('profile')
        profile = str(profile).strip() if profile not in (None, '') else 'SEM_PROFILE'

        for projeto_ipd in projetos:
            chave = (projeto_ipd, profile)
            grupos.setdefault(chave, set()).add(data_obj)

    grupos_com_buraco = []
    total_datas_faltantes = 0

    # Checagem geral do arquivo. Isso garante aviso mesmo quando há vários perfis
    # e o buraco só aparece olhando o período total enviado.
    buracos_gerais = montar_buracos_datas(datas_gerais)

    if buracos_gerais and datas_gerais:
        total_datas_faltantes += len(buracos_gerais)
        grupos_com_buraco.append({
            'projeto_ipd': 'ARQUIVO_GERAL',
            'profile': 'TODOS',
            'data_inicio': min(datas_gerais).strftime('%d/%m/%Y'),
            'data_fim': max(datas_gerais).strftime('%d/%m/%Y'),
            'qtd_datas_faltantes': len(buracos_gerais),
            'datas_faltantes': formatar_datas_faltantes(buracos_gerais),
        })

    # Checagem específica por projeto/profile.
    for chave, datas_presentes in grupos.items():
        datas_faltantes = montar_buracos_datas(datas_presentes)

        if not datas_faltantes:
            continue

        total_datas_faltantes += len(datas_faltantes)
        projeto_ipd, profile = chave

        grupos_com_buraco.append({
            'projeto_ipd': projeto_ipd,
            'profile': profile,
            'data_inicio': min(datas_presentes).strftime('%d/%m/%Y'),
            'data_fim': max(datas_presentes).strftime('%d/%m/%Y'),
            'qtd_datas_faltantes': len(datas_faltantes),
            'datas_faltantes': formatar_datas_faltantes(datas_faltantes),
        })

    grupos_com_buraco = sorted(
        grupos_com_buraco,
        key=lambda item: item['qtd_datas_faltantes'],
        reverse=True,
    )

    erro = None

    if linhas_com_data_invalida:
        erro = (
            'Existem datas inválidas no arquivo. ' 
            f'Linhas com problema: {linhas_com_data_invalida[:20]}. ' 
            'Corrija essas datas antes de importar.'
        )

    if not datas_gerais and len(dataset) > 0:
        erro = (
            "Nenhuma data válida foi encontrada no arquivo. "
            "Confira se a coluna de data está correta e se as datas estão em formato reconhecido."
        )

    return {
        'tem_buracos': bool(grupos_com_buraco),
        'erro': erro,
        'grupos_com_buraco': grupos_com_buraco[:30],
        'total_grupos_com_buraco': len(grupos_com_buraco),
        'total_datas_faltantes': total_datas_faltantes,
        'linhas_com_data_invalida': linhas_com_data_invalida[:20],
    }


def normalizar_decimal_importacao(valor):
    """
    Normaliza decimal vindo de CSV/XLSX.

    Mantém números normais e corrige decimal com vírgula:
    - "83,32594444" -> "83.32594444"
    - "1.234,56" -> "1234.56"
    - vazio -> None
    """
    if valor in (None, ''):
        return None

    if isinstance(valor, Decimal):
        return valor

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return valor

    texto = str(valor).strip()

    if not texto:
        return None

    texto = texto.replace(' ', '')

    if ',' in texto and '.' in texto:
        # Assume padrão brasileiro: 1.234,56
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')

    return texto



def normalizar_decimal_obrigatorio_importacao(valor, nome_campo):
    valor_norm = normalizar_decimal_importacao(valor)

    if valor_norm in (None, ''):
        raise ValueError(f'{nome_campo} vazio.')

    try:
        numero = Decimal(str(valor_norm))
    except InvalidOperation:
        raise ValueError(f'{nome_campo} deve ser numerico.')

    if not numero.is_finite():
        raise ValueError(f'{nome_campo} invalido.')

    return str(valor_norm)



def normalizar_decimal_opcional_importacao(valor, nome_campo):
    valor_norm = normalizar_decimal_importacao(valor)

    if valor_norm in (None, ''):
        return None

    try:
        numero = Decimal(str(valor_norm))
    except InvalidOperation:
        raise ValueError(f'{nome_campo} deve ser numerico.')

    if not numero.is_finite():
        raise ValueError(f'{nome_campo} invalido.')

    return str(valor_norm)



def preparar_dataset_para_importacao(dataset, tipo_importacao):
    """
    Valida e normaliza o dataset ANTES de chamar import_export.

    Evita o erro PostgreSQL:
    "current transaction is aborted, commands ignored until end of transaction block".
    """
    normalizar_headers_dataset(dataset)

    erros = []
    projetos_usados = set()

    headers = set(dataset.headers or [])

    if tipo_importacao == 'ipd':
        obrigatorias = {'projeto_ipd', 'profile', 'data', 'ipd'}
    else:
        obrigatorias = {'projeto_ipd', 'id_post', 'data'}

    faltando = sorted(obrigatorias - headers)
    if faltando:
        return [f"Colunas obrigatorias ausentes: {', '.join(faltando)}."]

    linhas_normalizadas = []

    for numero_linha, row in enumerate(dataset.dict, start=2):
        r = dict(row)

        try:
            if tipo_importacao == 'ipd':
                projeto_id = normalizar_inteiro_importacao(r.get('projeto_ipd'), 'projeto_ipd')
                projetos_usados.add(projeto_id)
                r['projeto_ipd'] = projeto_id

                profile = str(r.get('profile') or '').strip()
                if not profile:
                    raise ValueError('profile vazio.')
                r['profile'] = profile

                data_obj = parse_data_importacao_para_date(r.get('data'))
                if not data_obj:
                    raise ValueError(f"data invalida: {r.get('data')!r}")
                r['data'] = data_obj.strftime('%Y-%m-%d')

                for campo in ('fama', 'engaj', 'valencia', 'mob', 'ipd'):
                    if campo in r:
                        r[campo] = normalizar_decimal_obrigatorio_importacao(r.get(campo), campo)

                if 'interesse' in r:
                    r['interesse'] = normalizar_decimal_opcional_importacao(r.get('interesse'), 'interesse')

            else:
                id_post_norm, _ = normalizar_id_conteudo(r.get('id_post'))
                r['id_post'] = id_post_norm

                projetos = normalizar_projetos_conteudo(r.get('projeto_ipd'))
                if not projetos:
                    raise ValueError('projeto_ipd vazio.')
                projetos_usados.update(projetos)
                r['projeto_ipd'] = ','.join(str(p) for p in projetos)

                profile = str(r.get('profile') or '').strip()
                r['profile'] = profile

                data_obj = parse_data_importacao_para_date(r.get('data'))
                if not data_obj:
                    raise ValueError(f"data invalida: {r.get('data')!r}")
                r['data'] = data_obj.strftime('%Y-%m-%d')

                for campo in ('curtidas', 'comentarios'):
                    r[campo] = normalizar_inteiro_importacao(r.get(campo), campo, permitir_vazio=True)
                    if r[campo] is None:
                        r[campo] = 0

                if not r.get('categoria_tema'):
                    r['categoria_tema'] = 'Outros'

        except Exception as exc:
            erros.append(f'Linha {numero_linha}: {exc}')

        linhas_normalizadas.append(r)

        if len(erros) >= 30:
            break

    if erros:
        return erros

    if not linhas_normalizadas:
        return ['Arquivo sem linhas validas para importar.']

    if projetos_usados:
        existentes = set(
            ProjetoIPD.objects.filter(pk__in=projetos_usados).values_list('pk', flat=True)
        )
        ausentes = sorted(projetos_usados - existentes)
        if ausentes:
            return [f'ProjetoIPD inexistente no banco: {ausentes[:30]}']

    dataset.wipe()
    dataset.headers = list(linhas_normalizadas[0].keys())
    dataset.dict = linhas_normalizadas

    return []



def mensagens_resultado_importacao(result):
    mensagens = []

    for erro in getattr(result, 'base_errors', []):
        mensagens.append(str(getattr(erro, 'error', erro)))

    try:
        for linha, erros in result.row_errors():
            for erro in erros:
                mensagens.append(f'Linha {linha}: {getattr(erro, "error", erro)}')
                if len(mensagens) >= 10:
                    break
            if len(mensagens) >= 10:
                break
    except Exception:
        pass

    try:
        for linha_invalida in getattr(result, 'invalid_rows', [])[:10]:
            mensagens.append(f'Linha {linha_invalida.number}: {linha_invalida.error}')
    except Exception:
        pass

    return mensagens



def resposta_erro_critico(prefixo, exc):
    erro_python = traceback.format_exc()
    print(prefixo, erro_python)

    nome_erro = exc.__class__.__name__
    mensagem_erro = str(exc).strip() or repr(exc) or 'erro sem mensagem'

    return JsonResponse({
        'erro': f'{prefixo}: {nome_erro}: {mensagem_erro}',
        'detalhe': erro_python[-4000:],
    }, status=400)


# =============================================================================

# IPD RESOURCE

# =============================================================================



class IPDResource(resources.ModelResource):



    hash_indice = fields.Field(

        column_name='hash_indice',

        attribute='hash_indice',

        readonly=True,

    )



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

        import_id_fields = (

            'projeto_ipd',

            'profile',

            'data',

        )

        ignore_unknown_fields = True

        use_bulk = True

        batch_size = 1000

        skip_diff = True

        skip_unchanged = False

        report_skipped = False

        store_instance = False



    def _progress_key(self):

        request = getattr(self, '_progress_request', None)

        job_id = getattr(self, '_progress_job_id', None)



        if not request or not job_id:

            return None



        if not getattr(request, 'user', None):

            return None



        return f"ipd_import_progress:{request.user.pk}:{job_id}"



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



        total = getattr(self, '_progress_total', 0)



        if processados is None:

            processados = getattr(self, '_progress_processados', 0)



        if percentual is None:

            if total:

                percentual = int((processados / total) * 100)

                percentual = min(percentual, 99)

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

            print(f"Erro ao salvar progresso da importação IPD: {exc}")



    def import_data(self, dataset, *args, **kwargs):

        request = kwargs.get('request')

        self._progress_request = request

        self._progress_job_id = request.POST.get('import_job_id') if request else None

        self._progress_total = len(dataset)

        self._progress_processados = 0



        self._salvar_progresso(

            status='processando',

            processados=0,

            percentual=0,

            mensagem=f"Preparando {self._progress_total:,} registros...",

        )



        try:

            result = super().import_data(dataset, *args, **kwargs)



            if result.has_errors():

                self._salvar_progresso(

                    status='concluido_com_erros',

                    processados=self._progress_total,

                    percentual=100,

                    mensagem="Importação finalizada, mas existem registros com erro.",

                )

            else:

                self._salvar_progresso(

                    status='concluido',

                    processados=self._progress_total,

                    percentual=100,

                    mensagem=f"Importação concluída. {self._progress_total:,} registros processados.",

                )



            return result



        except Exception as exc:

            self._salvar_progresso(

                status='erro',

                processados=getattr(self, '_progress_processados', 0),

                mensagem=str(exc)[:500],

            )

            raise



    def before_import(self, dataset, **kwargs):

        normalizar_headers_dataset(dataset)

        super().before_import(dataset, **kwargs)

        self.projetos_ipd_alterados = set()



        hashes_arquivo = set()

        for row in dataset.dict:

            projeto_ipd_id = row.get('projeto_ipd')

            profile = row.get('profile')

            data = row.get('data')



            if not (projeto_ipd_id and profile and data):

                continue



            try:

                projeto_ipd_id = normalizar_inteiro_importacao(projeto_ipd_id, 'projeto_ipd')

            except (TypeError, ValueError):

                continue



            profile = str(profile).strip()

            data = normalizar_data_importacao(data)



            if not data:

                continue



            raw_string = f"{projeto_ipd_id}-{profile}-{data}"

            hash_indice = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

            hashes_arquivo.add(hash_indice)



        if hashes_arquivo:

            self.ipds_existentes = IPD.objects.in_bulk(

                hashes_arquivo, field_name='hash_indice'

            )

        else:

            self.ipds_existentes = {}



    def before_import_row(self, row, **kwargs):

        if row.get('projeto_ipd') not in (None, ''):

            row['projeto_ipd'] = normalizar_inteiro_importacao(row['projeto_ipd'], 'projeto_ipd')



        if row.get('profile') is not None:

            row['profile'] = str(row['profile']).strip()



        for campo_decimal in ('fama', 'engaj', 'valencia', 'mob', 'interesse', 'ipd'):

            if campo_decimal in row:

                row[campo_decimal] = normalizar_decimal_importacao(row.get(campo_decimal))



        if 'interesse' not in row or row.get('interesse') in ('', None):

            row['interesse'] = None



        if row.get('data'):

            row['data'] = normalizar_data_importacao(row['data'])



    def get_instance(self, instance_loader, row):

        projeto_ipd_id = row.get('projeto_ipd')

        profile = row.get('profile')

        data = row.get('data')



        if not (projeto_ipd_id and profile and data):

            return None



        try:

            projeto_ipd_id = normalizar_inteiro_importacao(projeto_ipd_id, 'projeto_ipd')

        except (TypeError, ValueError):

            return None



        profile = str(profile).strip()

        data = normalizar_data_importacao(data)



        raw_string = f"{projeto_ipd_id}-{profile}-{data}"

        hash_indice = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()



        return self.ipds_existentes.get(hash_indice)



    def after_import_row(self, row, row_result, **kwargs):

        super().after_import_row(row, row_result, **kwargs)

        self._progress_processados += 1



        processados = self._progress_processados

        total = self._progress_total



        if processados % 100 == 0 or processados == total:

            percentual = int((processados / total) * 100) if total else 0

            percentual = min(percentual, 99)



            mensagem = (

                "Finalizando gravação no banco de dados..."

                if processados >= total

                else f"Processando {processados:,} de {total:,} registros..."

            )



            self._salvar_progresso(

                status='processando',

                processados=processados,

                percentual=percentual,

                mensagem=mensagem,

            )



    def before_save_instance(self, instance, row, **kwargs):

        instance.profile = str(instance.profile or '').strip()



        if instance.projeto_ipd_id and instance.profile and instance.data:

            raw_string = (

                f"{instance.projeto_ipd_id}-"

                f"{instance.profile}-"

                f"{instance.data.strftime('%Y-%m-%d')}"

            )

            instance.hash_indice = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

            self.projetos_ipd_alterados.add(instance.projeto_ipd_id)



        if hasattr(instance, 'projeto_cliente_id') and not instance.projeto_cliente_id:

            primeiro_cliente = instance.projeto_ipd.projetos_cliente.first()

            if primeiro_cliente:

                instance.projeto_cliente = primeiro_cliente



        super().before_save_instance(instance, row, **kwargs)



    def after_import(self, dataset, result, **kwargs):

        super().after_import(dataset, result, **kwargs)

        projetos_ipd_ids = getattr(self, 'projetos_ipd_alterados', set())



        if not projetos_ipd_ids:

            return



        projetos_cliente_ids = (

            ProjetoIPD.objects.filter(id__in=projetos_ipd_ids)

            .values_list('projetos_cliente__id', flat=True)

            .exclude(projetos_cliente__id=None)

            .distinct()

        )



        for projeto_id in projetos_cliente_ids:

            cache_key = f"projeto_profiles:v1:projeto:{projeto_id}"

            try:

                cache.delete(cache_key)

            except Exception as exc:

                print(f"Erro ao invalidar cache {cache_key}: {exc}")





# =============================================================================

# INLINES E CLIENTE (com desregistro seguro para evitar AlreadyRegistered)

# =============================================================================



class ProjetoClienteIPDInline(admin.TabularInline):

    model = ProjetoClienteIPD

    extra = 1

    verbose_name = "Projeto IPD e Perfis"

    verbose_name_plural = "Projetos IPD Vinculados"





try:

    admin.site.unregister(ProjetoCliente)

except admin.sites.NotRegistered:

    pass





@admin.register(ProjetoCliente)

class ProjetoClienteAdmin(admin.ModelAdmin):

    list_display = ('id', 'nome', 'cliente', 'descricao', 'get_tipo_ipd')

    prepopulated_fields = {'slug': ('nome',)}

    search_fields = ('nome', 'cliente')

    inlines = [ProjetoClienteIPDInline]



    @admin.display(description='Tipo IPD')

    def get_tipo_ipd(self, obj):

        return obj.get_tipo_ipd_display()



    def save_formset(self, request, form, formset, change):

        super().save_formset(request, form, formset, change)

        cache.delete(f"projeto_profiles:v2:projeto:{form.instance.id}")





class ProjetoIPDLoteForm(forms.Form):

    nomes = forms.CharField(

        label='Nomes dos Projetos (um por linha)',

        widget=forms.Textarea(attrs={'rows': 15, 'cols': 80, 'placeholder': 'Ex:\nProjeto A\nProjeto B\nProjeto C'}),

        required=True,

        help_text="Insira os nomes dos projetos que deseja criar, um por linha. Projetos com nomes idênticos aos que já existem não serão duplicados."

    )



LISTA_PROJETO_IPD_TEMPLATE = '''{% extends "admin/change_list.html" %}

{% block object-tools-items %}

<li><a href="adicionar-lote/" class="addlink">Adicionar Projetos em Lote</a></li>

{{ block.super }}

{% endblock %}'''



FORM_LOTE_TEMPLATE = '''{% extends "admin/base_site.html" %}

{% block content %}

<style>

    #loading-overlay {

        display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;

        background: rgba(0, 0, 0, 0.6); z-index: 9999; text-align: center; color: #ffffff; font-family: sans-serif;

    }

    .spinner {

        border: 8px solid rgba(255,255,255, 0.3); border-top: 8px solid #ffffff; border-radius: 50%;

        width: 60px; height: 60px; animation: spin 1s linear infinite; margin: 25vh auto 20px auto;

    }

    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

</style>

<div id="loading-overlay">

    <div class="spinner"></div>

    <h2>Processando, por favor aguarde...</h2>

</div>

<p><a href="../">&lsaquo; Voltar para a lista de Projetos IPD</a></p>

<h1>Adicionar Projetos IPD em Lote</h1>

<form method="post" id="lote-form">

    {% csrf_token %}

    {{ form.as_p }}

    <div class="submit-row" style="text-align: left;">

        <input type="submit" value="Criar Projetos" class="default" id="submit-btn">

    </div>

</form>

<script>

    document.getElementById('lote-form').addEventListener('submit', function() {

        document.getElementById('loading-overlay').style.display = 'block';

        var btn = document.getElementById('submit-btn');

        btn.style.pointerEvents = 'none'; btn.style.opacity = '0.6'; btn.value = 'Processando...';

    });

</script>

{% endblock %}'''





# =============================================================================

# GESTÃO MANUAL E ADMIN MIXIN

# =============================================================================



MODOS_PROJETOS = (

    ('adicionar', 'Adicionar aos projetos atuais'),

    ('substituir', 'Substituir todos os projetos atuais'),

)



class ConteudoAdminForm(forms.ModelForm):

    modo_projetos = forms.ChoiceField(

        label='Como salvar os projetos IPD',

        choices=MODOS_PROJETOS,

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

        label='IDs dos projetos de destino',

        required=False,

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

        total = 0

        with transaction.atomic(using=queryset.db):

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



try:

    admin.site.unregister(ProjetoIPD)

except admin.sites.NotRegistered:

    pass





@admin.register(ProjetoIPD)

class ProjetoIPDAdmin(admin.ModelAdmin):

    list_display = ('id', 'nome')

    search_fields = ('nome',)



    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [

            path(

                'adicionar-lote/',

                self.admin_site.admin_view(self.adicionar_lote_view),

                name=f'{self.model._meta.app_label}_{self.model._meta.model_name}_lote'

            ),

        ]

        return custom_urls + urls



    def changelist_view(self, request, extra_context=None):

        response = super().changelist_view(request, extra_context)

        if hasattr(response, 'template_name'):

            response.template_name = engines['django'].from_string(LISTA_PROJETO_IPD_TEMPLATE)

        return response



    def adicionar_lote_view(self, request):

        if not self.has_add_permission(request):

            raise PermissionDenied



        form = ProjetoIPDLoteForm(request.POST or None)



        if request.method == 'POST' and form.is_valid():

            nomes_raw = form.cleaned_data['nomes']

            nomes = [n.strip() for n in nomes_raw.split('\n') if n.strip()]



            criados = []

            existentes = []



            for nome in nomes:

                obj, created = ProjetoIPD.objects.get_or_create(nome=nome)

                if created:

                    criados.append(obj)

                else:

                    existentes.append(obj)



            msg_partes = []

            if criados:

                lista_criados = " | ".join([f"{p.nome} (ID: {p.id})" for p in criados])

                msg_partes.append(f"<b>{len(criados)} CRIADOS:</b> {lista_criados}.")



            if existentes:

                lista_existentes = " | ".join([f"{p.nome} (ID: {p.id})" for p in existentes])

                msg_partes.append(f"<b>{len(existentes)} JÁ EXISTIAM:</b> {lista_existentes}.")



            if msg_partes:

                self.message_user(request, mark_safe("<br><br>".join(msg_partes)), messages.SUCCESS)

            else:

                self.message_user(request, "Nenhum nome válido foi inserido.", messages.WARNING)



            return redirect(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist')



        context = {

            **self.admin_site.each_context(request),

            'form': form,

            'opts': self.model._meta,

            'title': 'Adicionar Projetos IPD em Lote',

        }



        return TemplateResponse(request, engines['django'].from_string(FORM_LOTE_TEMPLATE), context)





@admin.register(IPD)

class IPDAdmin(GestaoAdminMixin, ImportExportModelAdmin):



    resource_classes = [IPDResource]

    skip_import_confirm = True

    import_template_name = "ipd_import.html"



    # =========================================================================

    # URL DO PROGRESSO E FATIAMENTO

    # =========================================================================



    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [

            path(

                'import-progress/',

                self.admin_site.admin_view(self.import_progress),

                name='score_ipd_import_progress',

            ),

            path(

                'importar-fatiado/',

                self.admin_site.admin_view(self.importar_fatiado_view),

                name='score_ipd_importar_fatiado',

            ),

        ]

        return custom_urls + urls



    def importar_fatiado_view(self, request):
        if request.method == 'POST':
            arquivo = request.FILES.get('file')

            if not arquivo:
                return JsonResponse({'erro': 'Nenhum arquivo enviado'}, status=400)

            try:
                dataset = carregar_dataset_importacao(arquivo)

                erros_pre_importacao = preparar_dataset_para_importacao(dataset, 'ipd')
                if erros_pre_importacao:
                    return JsonResponse({
                        'erro': 'Erros encontrados antes de importar: ' + ' | '.join(erros_pre_importacao[:10])
                    }, status=400)

                confirmar_buracos = request.POST.get('confirmar_buracos_datas') == '1'

                if not confirmar_buracos:
                    analise_buracos = detectar_buracos_datas_dataset(dataset)

                    if analise_buracos.get('erro'):
                        return JsonResponse({'erro': analise_buracos['erro']}, status=400)

                    if analise_buracos.get('tem_buracos'):
                        return JsonResponse({
                            'precisa_confirmar_buracos_datas': True,
                            'tipo_importacao': 'ipd',
                            'mensagem': 'Foram encontradas datas faltantes no CSV/XLSX de IPD. Deseja continuar a importação mesmo assim?',
                            **analise_buracos,
                        }, status=409)

                resource = IPDResource()
                result = resource.import_data(
                    dataset,
                    request=request,
                    raise_errors=False,
                    use_transactions=False,
                    rollback_on_validation_errors=False,
                )

                if result.has_errors() or result.has_validation_errors():
                    mensagens = mensagens_resultado_importacao(result)
                    erro_msg = ' | '.join(mensagens) if mensagens else 'Erro de validação ao processar lote.'
                    return JsonResponse({'erro': erro_msg}, status=400)

                return JsonResponse({
                    'status': 'ok',
                    'total_importado': len(dataset),
                    'mensagem': f'Importação de IPD concluída com sucesso. {len(dataset):,} registros processados.',
                })

            except Exception as e:
                return resposta_erro_critico('Erro Crítico no Servidor', e)

        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    def import_progress(self, request):

        job_id = request.GET.get('job_id')



        if not job_id:

            response = JsonResponse({

                'status': 'aguardando',

                'percentual': 0,

                'processados': 0,

                'total': 0,

                'mensagem': 'Aguardando importação...',

            })

            response['Cache-Control'] = 'no-store'

            return response



        chave = f"ipd_import_progress:{request.user.pk}:{job_id}"



        try:

            progresso = cache.get(chave)

        except Exception:

            progresso = None



        if progresso is None:

            progresso = {

                'status': 'aguardando',

                'percentual': 0,

                'processados': 0,

                'total': 0,

                'mensagem': 'Enviando e preparando arquivo...',

            }



        response = JsonResponse(progresso)

        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'

        return response



    def get_changelist(self, request, **kwargs):

        return LimitedAdminChangeList



    actions = ['excluir_selecionados_limitados']



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

# CONTEÚDO RESOURCE

# =============================================================================

# FUNÇÕES AUXILIARES DE CONTEÚDO

# =============================================================================



def normalizar_id_conteudo(valor):
    """
    Normaliza id_post vindo de CSV/XLSX.

    Protecoes:
    - nao deixa id_post vazio;
    - remove notacao cientifica quando o Excel/CSV transforma ID em numero;
    - evita quebrar IDs textuais que nao sao puramente numericos;
    - aceita valores tipo 123.0, 1.23E+18 e Decimal.
    """
    if valor is None or isinstance(valor, bool):
        raise ValueError('id_post vazio ou invalido.')

    texto = str(valor).strip().lstrip('\ufeff')

    if not texto:
        raise ValueError('id_post vazio.')

    # Quando e texto numerico sem notacao cientifica, preserva exatamente os digitos.
    if isinstance(valor, str) and re.fullmatch(r'\d+', texto):
        normalizado = texto
    else:
        texto_num = texto.replace(' ', '')
        eh_numero = isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)
        eh_numero_texto = re.fullmatch(
            r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',
            texto_num,
        ) is not None

        if eh_numero or eh_numero_texto:
            try:
                numero = Decimal(str(texto_num))
            except InvalidOperation:
                raise ValueError('id_post numerico invalido.')

            if not numero.is_finite():
                raise ValueError('id_post numerico invalido.')

            # ID nao deve ter casas decimais reais. Se vier 123.0, vira 123.
            inteiro = numero.to_integral_value()
            normalizado = format(inteiro, 'f').split('.')[0]
        else:
            normalizado = texto

    normalizado = str(normalizado).strip()

    if not normalizado:
        raise ValueError('id_post vazio apos normalizacao.')

    if len(normalizado) > 255:
        raise ValueError('id_post excede 255 caracteres.')

    risco = isinstance(valor, float) and len(normalizado.lstrip('0')) > 15
    return normalizado, risco



def normalizar_inteiro_importacao(valor, nome_campo='valor', permitir_vazio=False):
    """
    Normaliza inteiros vindos de CSV/XLSX.

    Aceita 3, 3.0, 3E+0 e Decimal('3').
    Rejeita vazio obrigatorio, 3.5 e texto nao numerico.
    """
    if valor in (None, ''):
        if permitir_vazio:
            return None
        raise ValueError(f'{nome_campo} vazio.')

    if isinstance(valor, bool):
        raise ValueError(f'{nome_campo} invalido.')

    texto = str(valor).strip().lstrip('\ufeff')

    if not texto:
        if permitir_vazio:
            return None
        raise ValueError(f'{nome_campo} vazio.')

    texto = texto.replace(' ', '')

    try:
        numero = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f'{nome_campo} deve ser numerico.')

    if not numero.is_finite():
        raise ValueError(f'{nome_campo} invalido.')

    inteiro = numero.to_integral_value()

    if numero != inteiro:
        raise ValueError(f'{nome_campo} deve ser inteiro.')

    return int(inteiro)



def normalizar_projetos_conteudo(valor):
    if valor is None or str(valor).strip() == '':
        return ()

    valores = str(valor).replace(';', ',').split(',') if isinstance(valor, str) else [valor]
    ids = set()

    for item in valores:
        if not str(item).strip():
            continue

        ids.add(normalizar_inteiro_importacao(item, 'projeto_ipd'))

    return tuple(sorted(ids))





# =============================================================================

# CONTEÚDO RESOURCE

# =============================================================================

class ConteudoResource(resources.ModelResource):

    id = fields.Field(column_name='id', attribute='id')

    curtidas = fields.Field(column_name='curtidas', attribute='curtidas', widget=IntegerWidget(), default=0)

    comentarios = fields.Field(column_name='comentarios', attribute='comentarios', widget=IntegerWidget(), default=0)

    projeto_ipd = fields.Field(column_name='projeto_ipd', attribute='projeto_ipd_id', widget=IntegerWidget())



    class Meta:

        model = Conteudo

        import_id_fields = ('id',)

        fields = (

            'id', 'id_post', 'projeto_ipd', 'profile', 'texto', 'link_post',

            'curtidas', 'comentarios', 'data', 'categoria_tema'

        )

        ignore_unknown_fields = True

        use_bulk = True        # Salva em lote instantaneamente (igual ao IPD)

        batch_size = 1000      # Lotes de 1000 no banco

        skip_diff = True

        skip_unchanged = False

        report_skipped = False

        store_instance = False



    def get_bulk_update_fields(self):

        return [

            'id_post', 'projeto_ipd', 'profile', 'texto', 'data',

            'curtidas', 'comentarios', 'link_post', 'categoria_tema'

        ]



    def before_import(self, dataset, **kwargs):

        normalizar_headers_dataset(dataset)

        super().before_import(dataset, **kwargs)



        headers = [str(h).strip().lstrip('\ufeff') if h else '' for h in (dataset.headers or [])]

        novos_dados = []

        ids_para_buscar = set()



        # Desmembra os registros e gera a chave id = id_post_projeto_ipd

        for row in dataset.dict:

            id_post_raw = row.get('id_post')

            proj_raw = row.get('projeto_ipd')

            data_raw = row.get('data')



            if not (id_post_raw and proj_raw and data_raw):

                continue



            try:

                id_post_norm, _ = normalizar_id_conteudo(id_post_raw)

                projetos_ids = normalizar_projetos_conteudo(proj_raw)

                data_norm = normalizar_data_importacao(data_raw)

            except Exception:

                continue



            for proj_id in projetos_ids:

                r = dict(row)

                r['id_post'] = id_post_norm

                r['projeto_ipd'] = proj_id

                r['data'] = data_norm

                pk_composta = f"{id_post_norm}_{proj_id}"

                r['id'] = pk_composta

                novos_dados.append(r)

                ids_para_buscar.add(pk_composta)



        # Atualiza a planilha de uma só vez na memória

        dataset.wipe()

        if novos_dados:

            dataset.dict = novos_dados



        # CORREÇÃO CRÍTICA: Busca instâncias completas no banco (IGUAL AO IPD), sem .only()

        if ids_para_buscar:

            self.conteudos_existentes = Conteudo.objects.in_bulk(ids_para_buscar)

        else:

            self.conteudos_existentes = {}



    def before_import_row(self, row, **kwargs):
        if row.get('id_post'):
            row['id_post'], _ = normalizar_id_conteudo(row.get('id_post'))

        if row.get('projeto_ipd') not in (None, ''):
            row['projeto_ipd'] = normalizar_inteiro_importacao(row.get('projeto_ipd'), 'projeto_ipd')

        if row.get('data'):
            row['data'] = normalizar_data_importacao(row.get('data'))

        if not row.get('id') and row.get('id_post') and row.get('projeto_ipd'):
            row['id'] = f"{row['id_post']}_{row['projeto_ipd']}"

        for campo in ('curtidas', 'comentarios'):
            valor = row.get(campo)
            if valor in (None, ''):
                row[campo] = 0
            else:
                row[campo] = normalizar_inteiro_importacao(valor, campo)

        if not row.get('categoria_tema'):
            row['categoria_tema'] = 'Outros'

    def get_instance(self, instance_loader, row):

        return self.conteudos_existentes.get(row.get('id'))



    def before_save_instance(self, instance, row, **kwargs):

        if not instance.id and row.get('id'):

            instance.id = row['id']

        elif not instance.id and instance.id_post and instance.projeto_ipd_id:

            instance.id = f"{instance.id_post}_{instance.projeto_ipd_id}"

        super().before_save_instance(instance, row, **kwargs)





# =============================================================================

# ADMIN CONTEÚDO

# =============================================================================



@admin.register(Conteudo)

class ConteudoAdmin(GestaoAdminMixin, ImportExportModelAdmin):

    resource_classes = [ConteudoResource]

    import_template_name = "conteudo_import.html"

    skip_import_confirm = True



    def get_urls(self):

        urls = super().get_urls()

        custom_urls = [

            path(

                'import-progress/',

                self.admin_site.admin_view(self.import_progress),

                name='score_conteudo_import_progress',

            ),

            path(

                'importar-fatiado/',

                self.admin_site.admin_view(self.importar_fatiado_view),

                name='score_conteudo_importar_fatiado',

            ),

        ]

        return custom_urls + urls



    def importar_fatiado_view(self, request):
        if request.method == 'POST':
            arquivo = request.FILES.get('file')

            if not arquivo:
                return JsonResponse({'erro': 'Nenhum arquivo enviado'}, status=400)

            try:
                dataset = carregar_dataset_importacao(arquivo)

                erros_pre_importacao = preparar_dataset_para_importacao(dataset, 'conteudo')
                if erros_pre_importacao:
                    return JsonResponse({
                        'erro': 'Erros encontrados antes de importar: ' + ' | '.join(erros_pre_importacao[:10])
                    }, status=400)

                confirmar_buracos = request.POST.get('confirmar_buracos_datas') == '1'

                if not confirmar_buracos:
                    analise_buracos = detectar_buracos_datas_dataset(dataset)

                    if analise_buracos.get('erro'):
                        return JsonResponse({'erro': analise_buracos['erro']}, status=400)

                    if analise_buracos.get('tem_buracos'):
                        return JsonResponse({
                            'precisa_confirmar_buracos_datas': True,
                            'tipo_importacao': 'conteudo',
                            'mensagem': 'Foram encontradas datas faltantes no CSV/XLSX de Conteúdo. Deseja continuar a importação mesmo assim?',
                            **analise_buracos,
                        }, status=409)

                resource = ConteudoResource()
                result = resource.import_data(
                    dataset,
                    request=request,
                    raise_errors=False,
                    use_transactions=False,
                    rollback_on_validation_errors=False,
                )

                if result.has_errors() or result.has_validation_errors():
                    mensagens = mensagens_resultado_importacao(result)
                    erro_msg = ' | '.join(mensagens) if mensagens else 'Erro de validação ao processar lote.'
                    return JsonResponse({'erro': erro_msg}, status=400)

                return JsonResponse({
                    'status': 'ok',
                    'total_importado': len(dataset),
                    'mensagem': f'Importação de Conteúdo concluída com sucesso. {len(dataset):,} registros processados.',
                })

            except Exception as e:
                return resposta_erro_critico('Erro no Servidor', e)

        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    def import_progress(self, request):

        job_id = request.GET.get('job_id')

        if not job_id:

            return JsonResponse({'status': 'aguardando', 'percentual': 0, 'processados': 0, 'total': 0, 'mensagem': 'Aguardando...'})



        chave = f"conteudo_import_progress:{request.user.pk}:{job_id}"

        progresso = cache.get(chave) or {'status': 'aguardando', 'percentual': 0, 'processados': 0, 'total': 0, 'mensagem': 'Preparando...'}

        return JsonResponse(progresso)



    def get_changelist(self, request, **kwargs):

        return LimitedAdminChangeList



    list_display = (

        'id',

        'id_post',

        'projeto_ipd',

        'profile',

        'categoria_tema',

        'data',

    )



    list_filter = (

        'projeto_ipd',

        'categoria_tema',

        'data',

    )



    search_fields = (

        '=id',

        '=id_post',

        'profile',

    )



    ordering = (

        '-data',

    )



    raw_id_fields = ("projeto_ipd",)

    list_per_page = 50

    list_max_show_all = 0

    show_full_result_count = False

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
