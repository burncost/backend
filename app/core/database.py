from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from typing import AsyncGenerator
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# PostgreSQL Setup
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


# MongoDB Setup
# mongoclient: AsyncIOMotorClient = None
# mongodb = None
mongoclient = AsyncIOMotorClient(settings.MONGODB_URL)
mongodb = mongoclient[settings.MONGO_DB]

async def connect_to_mongo():
    try:
        # mongoclient = AsyncIOMotorClient(settings.MONGODB_URL)
        # mongodb = mongoclient[settings.MONGO_DB]
        # Test connection
        await mongoclient.admin.command('ping')
        logger.info("Connected to MongoDB successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise


async def close_mongo_connection():
    if mongoclient:
        mongoclient.close()
        logger.info("Closed MongoDB connection")


# Dependency to get PostgreSQL session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Dependency to get MongoDB database
# async def get_mongodb():
#     return mongodb
def get_mongo_db() -> AsyncIOMotorDatabase:
    if mongoclient is None:
        raise RuntimeError("MongoDB not connected. Ensure connect_to_mongo() ran at startup.")
    return mongodb


def get_collection(name: str) -> AsyncIOMotorCollection:
    return get_mongo_db()[name]


# Optional FastAPI dependency version
async def get_mongodb() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    yield mongodb

# Health check functions
async def test_db_connection() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
        return False


async def test_mongo_connection() -> bool:
    try:
        if mongoclient:
            await mongoclient.admin.command('ping')
            return True
        return False
    except Exception as e:
        logger.error(f"MongoDB health check failed: {e}")
        return False


async def close_db_connections():
    await engine.dispose()
    await close_mongo_connection()
    logger.info("All database connections closed")
