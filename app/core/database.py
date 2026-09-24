"""
Bridge database module for backward compatibility with older services/modules.
"""
from app.db.session import SessionLocal, engine, Base, get_db

# Alias for compatibility with code expecting AsyncSessionLocal
AsyncSessionLocal = SessionLocal
