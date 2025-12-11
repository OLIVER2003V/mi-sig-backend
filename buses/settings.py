import os
import dj_database_url
from pathlib import Path

# =========================================================
# CONFIGURACIÓN GENERAL
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# SEGURIDAD:
# En Producción (Render) usará la variable de entorno SECRET_KEY.
# En tu PC usará la clave insegura por defecto para que no te compliques.
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-w+#o!*06qqjpzcnx!444$z_$l3@(x!gzc_*h6&yo_e^$k05vg0')

# DEBUG:
# Si existe la variable RENDER, Debug se apaga (False). Si no (tu PC), se enciende (True).
DEBUG = 'RENDER' not in os.environ

# HOSTS PERMITIDOS:
ALLOWED_HOSTS = []
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
else:
    ALLOWED_HOSTS.append('*')  # Permite todo en local

# =========================================================
# APLICACIONES INSTALADAS
# =========================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Tus librerías
    'rest_framework',
    'drf_spectacular',
    'corsheaders',
    
    # Tu App
    'routes',
]

# =========================================================
# MIDDLEWARE (Ojo al orden, WhiteNoise va segundo)
# =========================================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', # 👈 IMPORTANTE PARA RENDER
    'django.contrib.sessions.middleware.SessionMiddleware',
    
    'corsheaders.middleware.CorsMiddleware', 
    
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'buses.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'buses.wsgi.application'

# =========================================================
# BASE DE DATOS (Híbrida: SQLite Local / Postgres Render)
# =========================================================

DATABASES = {
    'default': dj_database_url.config(
        # Si no hay variable DATABASE_URL (tu PC), usa este archivo local:
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600
    )
}

# =========================================================
# VALIDADORES DE CONTRASEÑA
# =========================================================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =========================================================
# INTERNACIONALIZACIÓN
# =========================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# =========================================================
# ARCHIVOS ESTÁTICOS (CSS, JS, Imágenes)
# =========================================================

STATIC_URL = 'static/'

# 👇 ESTO ES LO QUE ARREGLA TU ERROR "ImproperlyConfigured"
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Esto asegura que WhiteNoise sirva los archivos en producción
if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# =========================================================
# DRF & SWAGGER (SPECTACULAR)
# =========================================================

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}

SPECTACULAR_SETTINGS = {    
    'TITLE': 'API de Líneas de Microbuses',
    'DESCRIPTION': 'Documentación de líneas, rutas y puntos (ida/vuelta).',
    'VERSION': '1.0.0',
}

# =========================================================
# CORS (Permisos de acceso)
# =========================================================

CORS_ALLOW_ALL_ORIGINS = True  
# CORS_ALLOW_CREDENTIALS = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'