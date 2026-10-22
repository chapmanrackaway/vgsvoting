import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
# Threads handle many simultaneous clerks/viewers without much memory per worker.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 60
graceful_timeout = 30
# Recycle workers periodically so a slow leak can never take the site down.
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
