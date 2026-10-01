import os

# Flask is WSGI and the GIL keeps one process on one core, so scale out with worker processes
bind = f"0.0.0.0:{os.environ.get('PORT', 3000)}"
workers = int(os.environ.get("WORKERS", os.cpu_count() or 1))
wsgi_app = "app:app"
accesslog = None
