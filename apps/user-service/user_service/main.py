from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
import asyncpg
import os
import logging
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        
        # Construct DATABASE_URL
        database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        pool = await asyncpg.create_pool(
            database_url,
            min_size=5,
            max_size=20
        )
        
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
        
        logger.info("Connected to database and ensured table exists!")
        startup_complete = True
    except Exception as e:
        logger.error(f" Database connection failed: {e}")
        startup_complete = False

async def close_db_connection():
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection closed")

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

class UserInDB(User):
    id: int
    created_at: str

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
    """Readiness probe - check DB connection"""
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"status": "ready", "database": "connected"}
        else:
            raise HTTPException(status_code=503, detail="Database not connected")
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/users")
async def get_users():
    """Get all users from database"""
    if not pool:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT id, username, email, created_at FROM users ORDER BY id")
            return {"users": [dict(user) for user in users]}
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
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
            return {"message": "User created", "user": dict(result)}
    except asyncpg.exceptions.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
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
            return dict(user)
    except Exception as e:
        logger.error(f"Failed to fetch user: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user")