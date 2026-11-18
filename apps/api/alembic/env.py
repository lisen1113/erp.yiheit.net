from alembic import context
from sqlalchemy.pool import NullPool
from yihe_api.db.models import Base
from yihe_api.db.session import DatabaseSettings, make_engine

settings = DatabaseSettings.from_environment()

if context.is_offline_mode():
    context.configure(
        url=settings.url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
