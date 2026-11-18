import pytest

from healthbot.sources import is_trusted


@pytest.mark.parametrize(
    "url",
    [
        "https://www.cdc.gov/vaccines/tdap",
        "https://cdc.gov/",
        "https://ncbi.nlm.nih.gov/books/x",
        "https://medlineplus.gov/cholesterol.html",
        "https://www.canada.ca/en/public-health.html",
        "HTTPS://WWW.HEART.ORG/x",
    ],
)
def test_trusted(url):
    assert is_trusted(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evilcdc.gov/",
        "https://cdc.gov.evil.com/",
        "https://example.com/?u=cdc.gov",
        "https://user@evil.com/cdc.gov",
        "",
    ],
)
def test_untrusted(url):
    assert not is_trusted(url)
