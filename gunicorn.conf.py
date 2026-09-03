# Gunicorn Production Configuration for High Concurrency (10,000+ Concurrent Streams)
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = min(multiprocessing.cpu_count() * 2 + 1, 8)
try:
    import gevent
    worker_class = "gevent"
    worker_connections = 2000
except ImportError:
    worker_class = "gthread"
    threads = 4

timeout = 300
keepalive = 10

# Hygiene: auto-recycle workers to keep RAM 100% clean
max_requests = 1000
max_requests_jitter = 50
