from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
import asyncpg
import os
import logging
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database pool + startup flag
pool = None
startup_complete = False

async def connect_to_db():
    global pool, startup_complete
    try:
        pool = await asyncpg.create_pool(
            os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/users"),
            min_size=5,
            max_size=20
        )
        logger.info(" Connected to database")
        startup_complete = True
    except Exception as e:
        logger.error(f" Database connection failed: {e}")
        startup_complete = False

async def close_db_connection():
    global pool
    if pool:
        await pool.close()
        logger.info(" Database connection closed")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_db()
    yield
    # Shutdown
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

# In-memory storage (temporary)
users_db = []

@app.get("/")
async def root():
    return {
        "service": "user-service",
        "version": app.version,
        "status": "running"
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
    return {"status": "healthy"}

@app.get("/ready")
async def readiness_check():
    """Readiness probe - check DB + Kafka"""
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
        # Future: add Kafka connection check
        return {"status": "ready", "database": "connected", "kafka": "connected"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/users")
async def get_users():
    return {"users": users_db}

@app.post("/users")
async def create_user(user: User):
    users_db.append(user.dict())
    # Future: Publish event to Kafka topic
    # await kafka_producer.send("user-created", user.dict())
    return {"message": "User created", "user": user.dict()}

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    if user_id < 1 or user_id > len(users_db):
        raise HTTPException(status_code=404, detail="User not found")
    return users_db[user_id - 1]
