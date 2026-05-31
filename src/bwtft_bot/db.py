from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bwtft_bot.config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir() -> None:
    prefix = "sqlite+aiosqlite:///"
    if settings.database_url.startswith(prefix):
        db_path = settings.database_url.removeprefix(prefix)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir()
engine = create_async_engine(settings.database_url)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    from bwtft_bot import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
