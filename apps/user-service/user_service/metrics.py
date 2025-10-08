from prometheus_client import Counter, Histogram, Gauge
import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Database metrics
DB_CONNECTIONS = Gauge('db_connections_active', 'Active database connections')
DB_ERRORS = Counter('db_errors_total', 'Total database errors', ['error_type'])

# Business metrics
USER_CREATED = Counter('user_created_total', 'Total users created')
USER_READ = Counter('user_read_total', 'Total user read operations')
ACTIVE_USERS = Gauge('active_users_count', 'Number of active users')

# HTTP metrics
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)
REQUEST_DURATION = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint']
)

# Application health metrics
APP_HEALTH = Gauge('app_health_status', 'Application health status (1=healthy, 0=unhealthy)')

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        method = request.method
        endpoint = request.url.path
        
        # Skip metrics endpoint to avoid self-measurement
        if endpoint == '/metrics':
            return await call_next(request)
            
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time
        
        record_request_metrics(method, endpoint, response.status_code, duration)
        return response

def record_request_metrics(method: str, endpoint: str, status_code: int, duration: float):
    """Record HTTP request metrics"""
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

# Export all metrics and the middleware class
__all__ = [
    'DB_CONNECTIONS',
    'DB_ERRORS', 
    'USER_CREATED',
    'USER_READ',
    'ACTIVE_USERS',
    'REQUEST_COUNT',
    'REQUEST_DURATION', 
    'APP_HEALTH',
    'MetricsMiddleware',
    'record_request_metrics'
]