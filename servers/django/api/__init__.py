import time

# Imported during django.setup() via INSTALLED_APPS, so this marks worker start, not first request
START = time.monotonic()
