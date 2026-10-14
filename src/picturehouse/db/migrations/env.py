"""Alembic environment. The engine is passed in by picturehouse.db.session."""

from alembic import context

from picturehouse.db.models import Base

config = context.config
engine = config.attributes["engine"]

with engine.connect() as connection:
    # render_as_batch: SQLite can't ALTER most things; batch mode rebuilds tables.
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
