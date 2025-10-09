from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
import asyncpg
import os
import logging
import time
from pydantic import BaseModel
from prometheus_client import generate_latest, Counter, Gauge, REGISTRY, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simple metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint'])
USER_CREATED = Counter('user_created_total', 'Total users created')
ACTIVE_USERS = Gauge('active_users_count', 'Number of active users')
APP_HEALTH = Gauge('app_health_status', 'Application health status')

pool = None
startup_complete = False

async def connect_to_db():
    global pool, startup_complete
    try:
        db_user = os.getenv("DATABASE_USER", "postgres")
        db_password = os.getenv("DATABASE_PASSWORD", "postgres")
        db_host = os.getenv("DATABASE_HOST", "localhost")
        db_port = os.getenv("DATABASE_PORT", "5432")
        db_name = os.getenv("DATABASE_NAME", "user_service")
        
        database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        pool = await asyncpg.create_pool(database_url, min_size=5, max_size=20)
        
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        logger.info("Connected to database")
        startup_complete = True
        APP_HEALTH.set(1)
        
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        startup_complete = False
        APP_HEALTH.set(0)

async def close_db_connection():
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection closed")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_db()
    yield
    await close_db_connection()

app = FastAPI(
    title="User Service",
    description="User management microservice",
    version=os.getenv("VERSION", "1.0.0"),
    lifespan=lifespan
)

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
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {
        "service": "user-service",
        "version": app.version,
        "status": "running",
        "metrics": "/metrics"
    }

@app.get("/health")
async def health_check():
    REQUEST_COUNT.labels(method="GET", endpoint="/health").inc()
    APP_HEALTH.set(1 if startup_complete else 0)
    return {"status": "healthy"}

@app.get("/ready")
async def readiness_check():
    REQUEST_COUNT.labels(method="GET", endpoint="/ready").inc()
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"status": "ready", "database": "connected"}
        else:
            APP_HEALTH.set(0)
            raise HTTPException(status_code=503, detail="Database not connected")
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        APP_HEALTH.set(0)
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/users")
async def get_users():
    REQUEST_COUNT.labels(method="GET", endpoint="/users").inc()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT id, username, email, created_at FROM users ORDER BY id")
            user_count = len(users)
            ACTIVE_USERS.set(user_count)
            return {"users": [dict(user) for user in users]}
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch users")

@app.post("/users")
async def create_user(user: User):
    REQUEST_COUNT.labels(method="POST", endpoint="/users").inc()
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                "INSERT INTO users (username, email) VALUES ($1, $2) RETURNING id, username, email, created_at",
                user.username, user.email
            )
            USER_CREATED.inc()
            ACTIVE_USERS.inc()
            return {"message": "User created", "user": dict(result)}
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    REQUEST_COUNT.labels(method="GET", endpoint="/users/{user_id}").inc()
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
            return dict(user)
    except Exception as e:
        logger.error(f"Failed to fetch user: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user")