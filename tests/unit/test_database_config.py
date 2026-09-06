from pydantic import SecretStr

from app.config import Settings
from app.db.session import database_url


def test_database_url_handles_special_characters_and_redacts_password() -> None:
    password = "test:@/%value"
    url = database_url(Settings(_env_file=None, postgres_password=SecretStr(password)))
    assert url.drivername == "postgresql+psycopg"
    assert url.password == password
    assert password not in str(url)
    assert password not in repr(url)
