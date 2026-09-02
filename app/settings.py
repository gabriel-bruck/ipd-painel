import os
from pathlib import Path
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv


# =============================================================================
# BASE
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =============================================================================
# CARREGA O .ENV
# =============================================================================

load_dotenv(BASE_DIR / ".env")


# =============================================================================
# SEGURANÇA / AMBIENTE
# =============================================================================

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-dev-only-change-me",
)

DEBUG = os.environ.get(
    "DEBUG",
    "False",
).lower() in ("true", "1", "yes")


ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1",
    ).split(",")
    if host.strip()
]


CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]


# =============================================================================
# APPLICATIONS
# =============================================================================

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Projeto
    "client",
    "score",
    "user",

    # Terceiros
    "import_export",
    "rest_framework",
]


# =============================================================================
# MIDDLEWARE
# =============================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # Static files em produção
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =============================================================================
# URLS / WSGI
# =============================================================================

ROOT_URLCONF = "app.urls"

WSGI_APPLICATION = "app.wsgi.application"


# =============================================================================
# TEMPLATES
# =============================================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =============================================================================
# POSTGRESQL / DATABASE
# =============================================================================
#
# PRODUÇÃO:
#
# DATABASE_URL=postgresql://usuario:senha@host:5432/banco
#
# Exemplo:
#
# DATABASE_URL=postgresql://postgres:minhasenha@postgres:5432/app
#
# Se DATABASE_URL não existir, usa SQLite.
# =============================================================================

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


if DATABASE_URL:

    db_url = urlparse(DATABASE_URL)

    if db_url.scheme not in (
        "postgres",
        "postgresql",
        "postgresql+psycopg",
    ):
        raise ValueError(
            "DATABASE_URL precisa ser uma URL PostgreSQL válida."
        )

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",

            "NAME": unquote(
                db_url.path.lstrip("/")
            ),

            "USER": unquote(
                db_url.username or ""
            ),

            "PASSWORD": unquote(
                db_url.password or ""
            ),

            "HOST": db_url.hostname or "",

            "PORT": str(
                db_url.port or 5432
            ),

            # Reutiliza conexões.
            "CONN_MAX_AGE": int(
                os.environ.get(
                    "DB_CONN_MAX_AGE",
                    "60",
                )
            ),

            "CONN_HEALTH_CHECKS": True,
        }
    }

else:

    # Desenvolvimento local sem DATABASE_URL.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# =============================================================================
# REDIS / CACHE
# =============================================================================
#
# PRODUÇÃO:
#
# REDIS_URL=redis://host:6379/0
#
# Com senha:
#
# REDIS_URL=redis://:senha@host:6379/0
#
# Se o Easypanel fornecer uma URL, basta colocar a URL diretamente.
#
# =============================================================================

REDIS_URL = os.environ.get(
    "REDIS_URL",
    "",
).strip()


if REDIS_URL:

    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",

            "LOCATION": REDIS_URL,

            "OPTIONS": {
                "socket_connect_timeout": 5,
                "socket_timeout": 5,
            },

            # Cache padrão: 5 minutos.
            "TIMEOUT": int(
                os.environ.get(
                    "CACHE_TIMEOUT",
                    "300",
                )
            ),
        }
    }

else:

    # Se não existir Redis, usa cache local.
    # Útil durante desenvolvimento.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "app-local-cache",
        }
    }


# =============================================================================
# SESSÕES
# =============================================================================
#
# Sessões permanecem no PostgreSQL.
#
# Redis está sendo utilizado somente como CACHE.
# =============================================================================

SESSION_ENGINE = "django.contrib.sessions.backends.db"


# =============================================================================
# PASSWORD VALIDATION
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# =============================================================================
# INTERNACIONALIZAÇÃO
# =============================================================================

LANGUAGE_CODE = "pt-br"

TIME_ZONE = "America/Sao_Paulo"

USE_I18N = True

USE_TZ = True


# =============================================================================
# STATIC FILES
# =============================================================================
#
# No deploy:
#
# python manage.py collectstatic --noinput
#
# WhiteNoise servirá /static/
# =============================================================================

STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"


STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage."
            "CompressedManifestStaticFilesStorage"
        ),
    },
}


# =============================================================================
# MEDIA
# =============================================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"


# =============================================================================
# EASYPANEL / TRAEFIK / HTTPS
# =============================================================================

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)


# =============================================================================
# SEGURANÇA DE PRODUÇÃO
# =============================================================================

if not DEBUG:

    SESSION_COOKIE_SECURE = True

    CSRF_COOKIE_SECURE = True

    SECURE_CONTENT_TYPE_NOSNIFF = True

    SECURE_REFERRER_POLICY = "same-origin"

    # Comece baixo durante a configuração.
    # Depois pode aumentar para 31536000.
    SECURE_HSTS_SECONDS = int(
        os.environ.get(
            "SECURE_HSTS_SECONDS",
            "3600",
        )
    )

    SECURE_HSTS_INCLUDE_SUBDOMAINS = (
        os.environ.get(
            "SECURE_HSTS_INCLUDE_SUBDOMAINS",
            "False",
        ).lower()
        in ("true", "1", "yes")
    )

    SECURE_HSTS_PRELOAD = (
        os.environ.get(
            "SECURE_HSTS_PRELOAD",
            "False",
        ).lower()
        in ("true", "1", "yes")
    )


# =============================================================================
# DEFAULT PRIMARY KEY
# =============================================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
