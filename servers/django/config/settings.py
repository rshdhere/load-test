import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SPEC_PATH = BASE_DIR.parent / "openapi.json"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-load-test-key")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = ["*"]

# A bare JSON API: no database, sessions, auth, or templates
INSTALLED_APPS = ["api"]
MIDDLEWARE = []
DATABASES = {}

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

APPEND_SLASH = False
USE_TZ = True
