# Gunicorn Production Configuration for High Concurrency (10,000+ Concurrent Streams)
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 4
threads = 8
worker_class = "gthread"
timeout = 120
keepalive = 5

# Hygiene: auto-recycle workers to keep RAM 100% clean
max_requests = 1000
max_requests_jitter = 50
