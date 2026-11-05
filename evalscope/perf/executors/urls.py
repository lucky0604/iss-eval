"""Endpoint rules shared by the AIPerf profiler and its primer."""

from urllib.parse import urlsplit, urlunsplit

from evalscope.utils.uri_utils import validate_http_endpoint


def normalize_chat_url(url: str) -> tuple[str, str]:
    """Return the origin and exact supported OpenAI chat endpoint path."""
    validate_http_endpoint(url)
    parsed = urlsplit(url.strip())
    if parsed.query or parsed.fragment:
        raise ValueError('Chat endpoint URLs with query or fragment are unsupported')
    path = parsed.path.rstrip('/')
    if not path:
        path = '/v1/chat/completions'
    elif path.endswith('/v1'):
        path += '/chat/completions'
    elif not path.endswith('/v1/chat/completions'):
        raise ValueError('Expected a base /v1 URL or full chat completions endpoint')
    return urlunsplit((parsed.scheme, parsed.netloc, '', '', '')), path


def normalize_metrics_url(url: str) -> str:
    """Normalize a credential-free Metrics URL for attribution and deduplication."""
    validate_http_endpoint(url)
    parsed = urlsplit(url.strip())
    if parsed.query or parsed.fragment:
        raise ValueError('Metrics URLs with query or fragment are unsupported')
    path = parsed.path.rstrip('/') or '/metrics'
    return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}'
