DEBUG = True

TIME_ZONE = "GMT"
LANGUAGE_CODE = "en-us"
SITE_ID = 1
USE_TZ = True
USE_I18N = True

SECRET_KEY = "test-secret-key-not-for-production"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "test.sqlite3",
    }
}

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "debug": DEBUG,
        },
    },
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.auth",
    "django.contrib.admin",
    "feeds",
]

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

FEEDS_USER_AGENT = "TestReader/1.0"
FEEDS_SERVER = "https://test.example.com"
FEEDS_CLOUDFLARE_WORKER = "https://reader.test.workers.dev"
