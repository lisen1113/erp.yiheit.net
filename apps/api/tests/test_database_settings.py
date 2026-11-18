import pytest
from yihe_api.db.session import DatabaseSettings, make_engine


def test_missing_configuration_names_required_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HOST", "PORT", "DB", "USER", "PASSWORD"):
        monkeypatch.delenv(f"POSTGRES_{key}", raising=False)

    with pytest.raises(RuntimeError) as error:
        DatabaseSettings.from_environment()

    for key in ("HOST", "DB", "USER", "PASSWORD"):
        assert f"POSTGRES_{key}" in str(error.value)


def test_connection_preserves_password_characters_without_exposing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_DB", "unit_test")
    monkeypatch.setenv("POSTGRES_USER", "unit_test")
    sample = "synthetic@:/% password"
    monkeypatch.setenv("POSTGRES_PASSWORD", sample)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)

    settings = DatabaseSettings.from_environment()
    engine = make_engine(settings)
    try:
        assert engine.url.password == sample
        assert sample not in str(engine.url)
        assert sample not in repr(settings)
    finally:
        engine.dispose()
