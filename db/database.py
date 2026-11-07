"""
Database engine and session management.
Supports PostgreSQL (production) with automatic SQLite fallback (local/dev/testing).
"""
import os
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from db.models import Base

# Determine Database URL
DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_SQLITE_PATH = DEFAULT_DB_DIR / "trading_agent.db"

_db_url = os.environ.get("DATABASE_URL")
if not _db_url:
    # Ensure data directory exists for SQLite
    DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
    _db_url = f"sqlite:///{DEFAULT_SQLITE_PATH}"

# Connection arguments for SQLite vs PostgreSQL
_connect_args = {}
if _db_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

# Engine initialization
engine = create_engine(
    _db_url,
    connect_args=_connect_args,
    pool_pre_ping=True
)

SessionFactory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
ScopedSession = scoped_session(SessionFactory)


_initialized = False

def init_db(custom_engine=None):
    """Create all database tables."""
    global _initialized
    target_engine = custom_engine or engine
    Base.metadata.create_all(bind=target_engine)
    _initialized = True

# Auto-initialize default database on import
try:
    init_db()
except Exception:
    pass


@contextmanager
def get_db_session():
    """Context manager for database transactional sessions."""
    global _initialized
    if not _initialized:
        try:
            init_db()
        except Exception:
            pass
    session = ScopedSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
