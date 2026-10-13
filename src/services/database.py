"""Database Service"""
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
import structlog

from src.utils.config import settings
from src.models.database import Base

logger = structlog.get_logger()
engine = None
async_session_maker = None

async def init_db():
    global engine, async_session_maker
    database_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    # M7 — SQLAlchemy echo logs the FULL parameterised query, including
    # bound API keys / passwords / customer data. Even if a sleepy operator
    # flips DEBUG=true on prod, refuse to enable echo there. Only on
    # explicitly non-prod environments do we honor DEBUG → echo.
    echo_sql = settings.DEBUG and settings.ENVIRONMENT.lower() not in (
        "production", "prod",
    )
    # NullPool — every checkout opens a fresh asyncpg connection. Avoids the
    # "another operation in progress" failures we hit on multi-query endpoints
    # when a pooled connection got stuck mid-statement after an earlier crash.
    engine = create_async_engine(
        database_url,
        echo=echo_sql,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
    if settings.ENVIRONMENT != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("Database initialized")

async def close_db():
    if engine:
        await engine.dispose()

@asynccontextmanager
async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
