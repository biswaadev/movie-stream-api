# Gunicorn Production Configuration for High Concurrency (10,000+ Concurrent Streams)
import multiprocessing

bind = "0.0.0.0:5000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "gevent"
worker_connections = 2000
timeout = 60
keepalive = 5

# Hygiene: auto-recycle workers to keep RAM 100% clean
max_requests = 1000
max_requests_jitter = 50
