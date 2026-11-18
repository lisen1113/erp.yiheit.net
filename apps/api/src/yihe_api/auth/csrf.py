"""Origin checks for browser requests that depend on authentication cookies."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BrowserOrigin:
    hostname: str
    port: int

    @classmethod
    def parse(cls, value: str) -> "BrowserOrigin":
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("Invalid browser origin")
        origin = urlsplit(value)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path
            or "?" in value
            or "#" in value
        ):
            raise ValueError("Invalid browser origin")
        port = origin.port if origin.port is not None else 443
        if not 1 <= port <= 65535:
            raise ValueError("Invalid browser origin")
        return cls(origin.hostname, port)


def allows_cookie_request(*, trusted: BrowserOrigin, origins: list[str], hosts: list[str]) -> bool:
    """Require one valid Origin and Host matching the configured public HTTPS origin."""
    if len(origins) != 1 or len(hosts) != 1:
        return False
    try:
        return (
            BrowserOrigin.parse(origins[0]) == BrowserOrigin.parse(f"https://{hosts[0]}") == trusted
        )
    except ValueError:
        return False
