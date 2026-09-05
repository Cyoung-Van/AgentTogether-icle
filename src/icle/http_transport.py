"""Shared URL policy for authenticated provider requests."""
from ipaddress import ip_address
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.error import HTTPError


def validate_endpoint(url: str) -> None:
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or '\\' in url:
        raise ValueError('base_url contains whitespace or invalid characters')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('base_url is malformed') from exc
    if not host or parsed.scheme not in ('https', 'http') or (port is not None and port < 1):
        raise ValueError('base_url must be an absolute HTTP(S) URL with a valid host and port')
    if parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
        raise ValueError('base_url must not contain credentials, query, or fragment')
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == 'localhost'
    if parsed.scheme != 'https' and not loopback:
        raise ValueError('base_url must be https (or loopback for testing)')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even HTTPS cross-origin redirects must not receive provider credentials.
        raise HTTPError(req.full_url, code, 'provider redirects are disabled; configure the final endpoint', headers, fp)


def open_authenticated(request, *, timeout):
    validate_endpoint(request.full_url)
    return build_opener(_NoRedirect()).open(request, timeout=timeout)
