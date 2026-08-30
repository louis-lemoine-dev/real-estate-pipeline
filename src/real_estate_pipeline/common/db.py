"""Shared SQLAlchemy engine construction for real-estate-pipeline.

Centralizes how a database engine is built so every task connecting to
Supabase does so the same way, with the same reliability settings —
instead of each script/task building its own `create_engine(...)` call.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

load_dotenv()


def get_engine() -> Engine:
    """Build a SQLAlchemy engine for the Supabase Postgres database.

    Reads DATABASE_URL from the environment (populated via .env through
    load_dotenv()). Raises KeyError if it's not set — fails loudly rather
    than silently falling back to some default connection.

    pool_pre_ping=True checks each pooled connection with a lightweight
    ping before it's handed out, so a stale or dropped connection (e.g.
    one recycled server-side by Supabase's Session pooler) is caught and
    transparently replaced before a real query runs on it, instead of
    failing mid-operation.
    """
    database_url = os.environ["DATABASE_URL"]
    return create_engine(database_url, pool_pre_ping=True)
