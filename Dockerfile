# ============================================================
# PYTHON
# ============================================================

FROM python:3.13-slim


# ============================================================
# VARIÁVEIS PYTHON
# ============================================================

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1


# ============================================================
# DIRETÓRIO DA APLICAÇÃO
# ============================================================

WORKDIR /app


# ============================================================
# DEPENDÊNCIAS DO SISTEMA
# ============================================================

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*


# ============================================================
# REQUIREMENTS
# ============================================================

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ============================================================
# CÓDIGO DJANGO
# ============================================================

COPY . .


# ============================================================
# PORTA
# ============================================================

EXPOSE 8000


# ============================================================
# START PRODUÇÃO
# ============================================================
#
# Quando o container iniciar:
#
# 1. Aplica migrations
# 2. Executa collectstatic
# 3. Inicia Gunicorn
#
# ============================================================

CMD ["sh", "-c", "python manage.py migrate && python manage.py collectstatic --noinput && exec gunicorn app.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120"]