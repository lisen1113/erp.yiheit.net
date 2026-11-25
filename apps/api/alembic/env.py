from alembic import context
from sqlalchemy.pool import NullPool
from yihe_api.audit import models as audit_models
from yihe_api.auth import models as auth_models
from yihe_api.db.models import Base
from yihe_api.db.session import DatabaseSettings, make_engine
from yihe_api.files import models as file_models
from yihe_api.idempotency import models as idempotency_models
from yihe_api.master_data.customer_models import CustomerProfile
from yihe_api.master_data.models import BusinessPartner
from yihe_api.master_data.supplier_models import SupplierProfile
from yihe_api.product_categories.models import ProductCategory
from yihe_api.rbac import models as rbac_models

assert (
    audit_models.AuditLog.metadata is idempotency_models.IdempotencyRecord.metadata is Base.metadata
)
assert auth_models.Employee.metadata is rbac_models.Role.metadata is Base.metadata
assert BusinessPartner.metadata is CustomerProfile.metadata is SupplierProfile.metadata
assert SupplierProfile.metadata is ProductCategory.metadata is Base.metadata
assert file_models.FileAsset.metadata is Base.metadata
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
