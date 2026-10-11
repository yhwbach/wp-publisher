from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession


def create_async_database_engine(database_url: str, *, use_null_pool: bool = False) -> AsyncEngine:
    if use_null_pool:
        return create_async_engine(database_url, poolclass=pool.NullPool)
    return create_async_engine(database_url, pool_pre_ping=True, pool_recycle=3600)


def create_async_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, autoflush=False, expire_on_commit=False)


@asynccontextmanager
async def get_db(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_database_engine(database_url, use_null_pool=True)
    try:
        async with create_async_session(engine)() as session:
            yield session
    finally:
        await engine.dispose()
