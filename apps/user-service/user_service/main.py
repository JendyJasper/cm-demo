from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
import asyncpg
import os
import logging
import time
import json
import sys
from pydantic import BaseModel
from prometheus_client import generate_latest, REGISTRY, CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

# Configure structured JSON logging
class StructuredLogger:
    def __init__(self):
        self.logger = logging.getLogger("user-service")
        self.logger.setLevel(logging.INFO)
        
        # Remove existing handlers to avoid duplicate logs
        self.logger.handlers = []
        
        # Create console handler with JSON formatter
        handler = logging.StreamHandler(sys.stdout)
        
        # Custom JSON formatter
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_entry = {
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "service": "user-service",
                    "environment": os.getenv("ENVIRONMENT", "development")
                }
                
                # Add extra fields if they exist
                if hasattr(record, 'extra_fields'):
                    log_entry.update(record.extra_fields)
                
                return json.dumps(log_entry)
        
        handler.setFormatter(JsonFormatter())
        self.logger.addHandler(handler)
    
    
    def _log_with_extra(self, level, message, **kwargs):
        extra_fields = {**kwargs}
        
        # Create a LogRecord with extra fields
        if self.logger.isEnabledFor(level):
            record = self.logger.makeRecord(
                self.logger.name, level, "(unknown file)", 0, 
                message, (), None, extra=extra_fields
            )
            # Store extra fields for the formatter
            record.extra_fields = extra_fields
            self.logger.handle(record)
    
    def info(self, message, **kwargs):
        self._log_with_extra(logging.INFO, message, **kwargs)
    
    def error(self, message, **kwargs):
        self._log_with_extra(logging.ERROR, message, **kwargs)
    
    def warning(self, message, **kwargs):
        self._log_with_extra(logging.WARNING, message, **kwargs)

# Initialize structured logger
logger = StructuredLogger()

from prometheus_client import Counter, Histogram, Gauge

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

def record_request_metrics(method: str, endpoint: str, status_code: int, duration: float):
    """Record HTTP request metrics"""
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

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

# Database connection pool
pool = None
startup_complete = False

async def connect_to_db():
    global pool, startup_complete
    try:
        # Get database connection details from environment
        db_user = os.getenv("DATABASE_USER", "postgres")
        db_password = os.getenv("DATABASE_PASSWORD", "postgres")
        db_host = os.getenv("DATABASE_HOST", "localhost")
        db_port = os.getenv("DATABASE_PORT", "5432")
        db_name = os.getenv("DATABASE_NAME", "user_service")
        
        logger.info("Attempting database connection", 
                   db_host=db_host, db_port=db_port, db_name=db_name, db_user=db_user)
        
        # Construct DATABASE_URL
        database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        pool = await asyncpg.create_pool(
            database_url,
            min_size=5,
            max_size=20
        )
        
        # Update metrics
        DB_CONNECTIONS.set(5)  # min_size
        
        # Create users table if it doesn't exist
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        logger.info("Database connection successful", 
                   table_created=True, connection_pool_size=5, operation="database_connect")
        startup_complete = True
        APP_HEALTH.set(1)
        
    except Exception as e:
        logger.error("Database connection failed", 
                    error=str(e), error_type=type(e).__name__, operation="database_connect")
        startup_complete = False
        APP_HEALTH.set(0)
        DB_ERRORS.labels(error_type='connection').inc()

async def close_db_connection():
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection closed", operation="database_disconnect")
        DB_CONNECTIONS.set(0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_db()
    yield
    # Shutdown
    await close_db_connection()

app = FastAPI(
    title="User Service",
    description="User management microservice with metrics and structured logging",
    version=os.getenv("VERSION", "1.0.0"),
    lifespan=lifespan
)

# Add metrics middleware if enabled
if os.getenv("METRICS_ENABLED", "true").lower() == "true":
    app.add_middleware(MetricsMiddleware)

# Pydantic models
class User(BaseModel):
    username: str
    email: str

class UserInDB(User):
    id: int
    created_at: str

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
async def root():
    logger.info("Root endpoint called", operation="root_access")
    return {
        "service": "user-service",
        "version": app.version,
        "status": "running",
        "metrics": "/metrics",
        "logs_test": "/test-logs"
    }

@app.get("/startup")
async def startup_probe():
    """Startup probe - checks if app finished initialization"""
    if startup_complete:
        logger.info("Startup probe - healthy", operation="startup_probe")
        return {"status": "ok"}
    logger.warning("Startup probe - not ready", operation="startup_probe")
    raise HTTPException(status_code=503, detail="Service starting up")

@app.get("/health")
async def health_check():
    """Liveness probe"""
    APP_HEALTH.set(1 if startup_complete else 0)
    logger.info("Health check called", operation="health_check", status="healthy")
    return {"status": "healthy"}

@app.get("/ready")
async def readiness_check():
    """Readiness probe - check DB connection"""
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            DB_CONNECTIONS.inc()  # Sample connection usage
            logger.info("Readiness check - ready", operation="readiness_check", database="connected")
            return {"status": "ready", "database": "connected"}
        else:
            APP_HEALTH.set(0)
            logger.warning("Readiness check - database not connected", operation="readiness_check")
            raise HTTPException(status_code=503, detail="Database not connected")
    except Exception as e:
        logger.error("Readiness check failed", 
                    error=str(e), error_type=type(e).__name__, operation="readiness_check")
        APP_HEALTH.set(0)
        DB_ERRORS.labels(error_type='readiness_check').inc()
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/users")
async def get_users():
    """Get all users from database"""
    if not pool:
        logger.warning("Database not connected for users list", operation="list_users")
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        logger.info("Retrieving all users", operation="list_users")
        
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT id, username, email, created_at FROM users ORDER BY id")
            user_count = len(users)
            
            logger.info("Users retrieved successfully", 
                       operation="list_users", user_count=user_count)
            
            USER_READ.inc(user_count)
            ACTIVE_USERS.set(user_count)
            return {"users": [dict(user) for user in users]}
            
    except Exception as e:
        logger.error("Failed to fetch users", 
                    error=str(e), error_type=type(e).__name__, operation="list_users")
        DB_ERRORS.labels(error_type='query').inc()
        raise HTTPException(status_code=500, detail="Failed to fetch users")

@app.post("/users")
async def create_user(user: User):
    """Create a new user in database"""
    if not pool:
        logger.warning("Database not connected for user creation", 
                      username=user.username, email=user.email, operation="create_user")
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        logger.info("Creating new user", 
                   username=user.username, email=user.email, operation="create_user")
        
        async with pool.acquire() as conn:
            # Insert user and return the created record
            result = await conn.fetchrow(
                "INSERT INTO users (username, email) VALUES ($1, $2) RETURNING id, username, email, created_at",
                user.username, user.email
            )
            
            user_data = dict(result)
            logger.info("User created successfully", 
                       user_id=user_data['id'], username=user_data['username'], 
                       email=user_data['email'], operation="create_user")
            
            USER_CREATED.inc()
            ACTIVE_USERS.inc()
            return {"message": "User created", "user": user_data}
            
    except asyncpg.exceptions.UniqueViolationError:
        logger.warning("User creation failed - duplicate entry", 
                      username=user.username, email=user.email, 
                      error_type="unique_violation", operation="create_user")
        DB_ERRORS.labels(error_type='unique_violation').inc()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    except Exception as e:
        logger.error("User creation failed", 
                    username=user.username, email=user.email, 
                    error=str(e), error_type=type(e).__name__, operation="create_user")
        DB_ERRORS.labels(error_type='insert').inc()
        raise HTTPException(status_code=500, detail="Failed to create user")

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    """Get a specific user by ID"""
    if not pool:
        logger.warning("Database not connected for user retrieval", 
                      user_id=user_id, operation="get_user")
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        logger.info("Retrieving user", user_id=user_id, operation="get_user")
        
        async with pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT id, username, email, created_at FROM users WHERE id = $1",
                user_id
            )
            if not user:
                logger.warning("User not found", user_id=user_id, operation="get_user")
                raise HTTPException(status_code=404, detail="User not found")
            
            user_data = dict(user)
            logger.info("User retrieved successfully", 
                       user_id=user_data['id'], username=user_data['username'], operation="get_user")
            
            USER_READ.inc()
            return user_data
            
    except Exception as e:
        logger.error("User retrieval failed", 
                    user_id=user_id, error=str(e), error_type=type(e).__name__, operation="get_user")
        DB_ERRORS.labels(error_type='query').inc()
        raise HTTPException(status_code=500, detail="Failed to fetch user")

@app.post("/test-logs")
async def test_logs():
    """Endpoint to test different log levels and structured logging"""
    logger.info("Test info log - normal operation", 
               test_data={"sample": "value", "count": 42}, 
               component="test_endpoint", operation="test_logs")
    
    logger.warning("Test warning log - attention needed", 
                  attention_needed=True, severity="medium", 
                  component="test_endpoint", operation="test_logs")
    
    logger.error("Test error log - simulated error", 
                simulated_error=True, component="test_endpoint", 
                stack_trace="simulated_stack_trace", operation="test_logs")
    
    # Test with different data types
    logger.info("Complex structured log", 
               user_data={"id": 123, "active": True, "tags": ["test", "demo"]},
               performance_metrics={"response_time": 45.2, "cpu_usage": 23.7},
               operation="test_logs")
    
    return {
        "message": "Log test completed",
        "logs_generated": ["info", "warning", "error", "complex_structure"],
        "timestamp": time.time()
    }