"""Explicit database configuration and synchronous session factories."""

import os
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, ValidationError
from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    host: RequiredText
    port: Annotated[int, Field(ge=1, le=65535)] = 5432
    database: RequiredText
    user: RequiredText
    password: Annotated[SecretStr, Field(min_length=1)]

    @classmethod
    def from_environment(cls) -> Self:
        variables = {
            "host": "POSTGRES_HOST",
            "port": "POSTGRES_PORT",
            "database": "POSTGRES_DB",
            "user": "POSTGRES_USER",
            "password": "POSTGRES_PASSWORD",
        }
        values = {field: os.environ[key] for field, key in variables.items() if key in os.environ}
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            names = ", ".join(variables[str(error["loc"][0])] for error in exc.errors())
            raise RuntimeError(f"Missing or invalid PostgreSQL configuration: {names}") from None

    def url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.database,
        )


def make_engine(settings: DatabaseSettings, *, poolclass: type[NullPool] | None = None) -> Engine:
    connect_args = {"options": "-c timezone=UTC", "connect_timeout": 5}
    if poolclass is not None:
        return create_engine(settings.url(), connect_args=connect_args, poolclass=poolclass)
    return create_engine(
        settings.url(),
        connect_args=connect_args,
        pool_size=5,
        max_overflow=2,
        pool_timeout=10,
        pool_pre_ping=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
