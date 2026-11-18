import pytest
from yihe_api.auth.csrf import BrowserOrigin, allows_cookie_request


@pytest.mark.parametrize(
    ("origins", "hosts", "allowed"),
    [
        (["https://erp.example.test"], ["erp.example.test:443"], True),
        (["https://erp.example.test:444"], ["erp.example.test"], False),
        (["http://erp.example.test"], ["erp.example.test"], False),
        (["https://attacker.example.test"], ["erp.example.test"], False),
        (["https://erp.example.test"], ["attacker.example.test"], False),
        (["https://attacker.example.test"], ["attacker.example.test"], False),
        (["https://erp.example.test", "https://erp.example.test"], ["erp.example.test"], False),
        (["https://erp.example.test"], ["erp.example.test", "erp.example.test"], False),
        ([], ["erp.example.test"], False),
        (["null"], ["erp.example.test"], False),
        (["https://erp.example.test/"], ["erp.example.test"], False),
    ],
)
def test_cookie_request_requires_configured_origin_and_host(origins, hosts, allowed) -> None:
    trusted = BrowserOrigin.parse("https://erp.example.test")
    assert allows_cookie_request(trusted=trusted, origins=origins, hosts=hosts) is allowed
