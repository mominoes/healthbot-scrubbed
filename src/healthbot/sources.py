"""Trusted-source allowlist for general health guidance."""

from __future__ import annotations

from urllib.parse import urlparse

TRUSTED_DOMAINS: tuple[str, ...] = (
    "mayoclinic.org",
    "medlineplus.gov",
    "cdc.gov",
    "nih.gov",  # includes ncbi.nlm.nih.gov (subdomain)
    "ncbi.nlm.nih.gov",
    "canada.ca",
    "uspreventiveservicestaskforce.org",
    "heart.org",
    "diabetes.org",
)


def is_trusted(url: str) -> bool:
    """True if the URL's host is an allowlisted domain or a subdomain of one."""
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)
