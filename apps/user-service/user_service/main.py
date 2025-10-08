from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
import asyncpg
import os
import logging
import time
from pydantic import BaseModel
from prometheus_client import generate_latest, REGISTRY, CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from user_service.metrics import (
    record_request_metrics, USER_CREATED, USER_READ, ACTIVE_USERS, 
    DB_CONNECTIONS, DB_ERRORS, APP_HEALTH, MetricsMiddleware
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pool = None
startup_complete = False

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

async def connect_to_db():
    global pool, startup_complete
    try:
        # Get database connection details from environment
        db_user = os.getenv("DATABASE_USER", "postgres")
        db_password = os.getenv("DATABASE_PASSWORD", "postgres")
        db_host = os.getenv("DATABASE_HOST", "localhost")
        db_port = os.getenv("DATABASE_PORT", "5432")
        db_name = os.getenv("DATABASE_NAME", "user_service")
        
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
        
        logger.info("Connected to database and ensured table exists")
        startup_complete = True
        APP_HEALTH.set(1)
        
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        startup_complete = False
        APP_HEALTH.set(0)
        DB_ERRORS.labels(error_type='connection').inc()

async def close_db_connection():
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection closed")
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
    description="User management microservice with metrics",
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
    return {
        "service": "user-service",
        "version": app.version,
        "status": "running",
        "metrics": "/metrics"
    }

@app.get("/startup")
async def startup_probe():
    """Startup probe - checks if app finished initialization"""
    if startup_complete:
        return {"status": "ok"}
    raise HTTPException(status_code=503, detail="Service starting up")

@app.get("/health")
async def health_check():
    """Liveness probe"""
    APP_HEALTH.set(1 if startup_complete else 0)
    return {"status": "healthy"}

@app.get("/ready")
async def readiness_check():
    """Readiness probe - check DB connection"""
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            DB_CONNECTIONS.inc()  # Sample connection usage
            return {"status": "ready", "database": "connected"}
        else:
            APP_HEALTH.set(0)
            raise HTTPException(status_code=503, detail="Database not connected")
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        APP_HEALTH.set(0)
        DB_ERRORS.labels(error_type='readiness_check').inc()
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/users")
async def get_users():
    """Get all users from database"""
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT id, username, email, created_at FROM users ORDER BY id")
            user_count = len(users)
            USER_READ.inc(user_count)
            ACTIVE_USERS.set(user_count)
            return {"users": [dict(user) for user in users]}
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        DB_ERRORS.labels(error_type='query').inc()
        raise HTTPException(status_code=500, detail="Failed to fetch users")

@app.post("/users")
async def create_user(user: User):
    """Create a new user in database"""
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            # Insert user and return the created record
            result = await conn.fetchrow(
                "INSERT INTO users (username, email) VALUES ($1, $2) RETURNING id, username, email, created_at",
                user.username, user.email
            )
            USER_CREATED.inc()
            ACTIVE_USERS.inc()
            return {"message": "User created", "user": dict(result)}
    except asyncpg.exceptions.UniqueViolationError:
        DB_ERRORS.labels(error_type='unique_violation').inc()
        raise HTTPException(status_code=400, detail="Username or email already exists")
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        DB_ERRORS.labels(error_type='insert').inc()
        raise HTTPException(status_code=500, detail="Failed to create user")

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    """Get a specific user by ID"""
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT id, username, email, created_at FROM users WHERE id = $1",
                user_id
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            USER_READ.inc()
            return dict(user)
    except Exception as e:
        logger.error(f"Failed to fetch user: {e}")
        DB_ERRORS.labels(error_type='query').inc()
        raise HTTPException(status_code=500, detail="Failed to fetch user")