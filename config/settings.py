"""
Django settings - OPTIMIZED FOR SPEED
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Loads .env for local dev only (production sets real env vars directly via
# the host, e.g. Render/systemd — .env is gitignored and won't exist there).
load_dotenv(BASE_DIR / ".env")

def env_list(key: str, default: str = ""):
    val = os.getenv(key, default)
    return [x.strip() for x in val.split(",") if x.strip()]

DEBUG = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes", "on")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1,primelendingphp.com,www.primelendingphp.com,loving-tenderness-production-2c60.up.railway.app"
)

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "https://primelendingphp.com,https://www.primelendingphp.com,https://loving-tenderness-production-2c60.up.railway.app"
)

# Render injects the public hostname (e.g. my-app.onrender.com) at runtime.
# Auto-allow it so the deploy works before a custom domain is attached.
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_HOST}")

INSTALLED_APPS = [
    "staffdash",
    "cloudinary",
    "jazzmin",
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "django.contrib.humanize",
]

# ✅ OPTIMIZED MIDDLEWARE - NO CACHE MIDDLEWARE (causes slowness)
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Static files
    "accounts.middleware.PortalSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    'accounts.middleware.CheckUserActiveMiddleware',
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ✅ DATABASE
# conn_max_age=0 -> fresh connection per request (cannot go stale / hang on a
#   dead socket). This is the fix for the recurring "WORKER TIMEOUT" outages:
#   a persistent connection (conn_max_age=600) was being dropped by the cloud
#   Postgres while Django still thought it was alive, so every request blocked
#   on a half-open socket until the gunicorn worker was killed.
# conn_health_checks=True -> if any pooled connection is ever reused, validate
#   it first and reconnect if dead (belt-and-suspenders).
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        # conn_max_age=0 -> a FRESH connection per request, governed by
        # connect_timeout. This is the definitive fix for the recurring outage:
        # any persistent connection (conn_max_age>0) can be dropped by the cloud
        # Postgres while Django still holds it; reusing that dead socket blocks
        # (statement_timeout cannot fire because the query never reaches the
        # server) until TCP keepalives notice ~60s later - exactly the gunicorn
        # worker timeout. With 0, connections never live long enough to go stale.
        # The per-request connect cost to Railway's in-region Postgres is a few
        # ms and is well worth guaranteed uptime.
        conn_max_age=0,
        conn_health_checks=True,
        ssl_require=False,
    )
}

# PostgreSQL safety options - applied ALWAYS for postgres (not gated behind
# DEBUG). These timeouts are what prevent a request from hanging forever on a
# slow/stuck database and tripping the gunicorn WORKER TIMEOUT crash loop, so
# they must apply in every environment.
db_engine = DATABASES["default"].get("ENGINE", "")
if "postgresql" in db_engine:
    DATABASES["default"].setdefault("OPTIONS", {})
    # Fail fast instead of hanging if the DB is briefly unreachable.
    DATABASES["default"]["OPTIONS"]["connect_timeout"] = 10
    # Kill any runaway / blocked query at 30s instead of letting it hang a worker.
    DATABASES["default"]["OPTIONS"]["options"] = "-c statement_timeout=30000"
    # TCP keepalives so a dead connection is detected at the socket level
    # quickly rather than blocking on a read forever.
    DATABASES["default"]["OPTIONS"]["keepalives"] = 1
    DATABASES["default"]["OPTIONS"]["keepalives_idle"] = 30
    DATABASES["default"]["OPTIONS"]["keepalives_interval"] = 10
    DATABASES["default"]["OPTIONS"]["keepalives_count"] = 3

# ✅ CACHES - SIMPLE (No complex options)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"

# ✅ STATIC FILES - OPTIMIZED
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ✅ CSRF & UPLOAD
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_USE_SESSIONS = False
CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'

DATA_UPLOAD_MAX_MEMORY_SIZE = 20971520  # 20MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 20971520  # 20MB
FILE_UPLOAD_MAX_NUMBER_FILES = 10

FILE_UPLOAD_HANDLERS = [
    'django.core.files.uploadhandler.MemoryFileUploadHandler',
    'django.core.files.uploadhandler.TemporaryFileUploadHandler',
]

WHITENOISE_MANIFEST_STRICT = False
WHITENOISE_MAX_AGE = 31536000

# ✅ CLOUDINARY
import cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.getenv("CLOUDINARY_API_KEY", ""),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    secure=True,
)

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    "API_KEY": os.getenv("CLOUDINARY_API_KEY", ""),
    "API_SECRET": os.getenv("CLOUDINARY_API_SECRET", ""),
}

# ✅ TELEGRAM (one bot for both — activity alerts go to a group/topic, login
# approvals go straight to the Boss's private chat(s), no topic).
# NOTE: this bot's token is shared with a separate polling-based bot on the
# same Telegram account, so this site must NEVER register a webhook for it
# (webhook and getUpdates polling can't coexist on one token) — button taps
# are instead handled by that polling bot, which calls DEVICE_ACTION endpoint.
TELEGRAM_ALERT_BOT_TOKEN = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
TELEGRAM_ALERT_THREAD_ID = os.getenv("TELEGRAM_ALERT_THREAD_ID", "")
TELEGRAM_ALERT_TZ = os.getenv("TELEGRAM_ALERT_TZ", "Asia/Phnom_Penh")
TELEGRAM_LOGIN_CHAT_IDS = env_list("TELEGRAM_LOGIN_CHAT_IDS")
DEVICE_ACTION_SECRET = os.getenv("DEVICE_ACTION_SECRET", "")

STAFF_DEVICE_TOKEN_COOKIE = "staff_device_token"
STAFF_PENDING_COOKIE = "staff_pending_token"
STAFF_LOGIN_APPROVAL_TIMEOUT_MINUTES = 15

# ✅ SECURITY
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ✅ LOGGING - MINIMAL (for speed)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,  # Disable all loggers for speed
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "ERROR",  # Only errors
            "propagate": False,
        },
    },
}

JAZZMIN_SETTINGS = {
    "site_title": "Loan Admin",
    "site_header": "Loan Admin",
    "site_brand": "Loan Admin",
    "welcome_sign": "Welcome",
    "copyright": "Loan",
    "show_sidebar": True,
    "navigation_expanded": True,
    "theme": "darkly",
    "custom_css": "css/admin_custom.css",
}