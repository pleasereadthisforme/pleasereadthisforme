"""One explicit HTTPS destination for all production provider requests."""
from prtfm.credentials.provider import get_key

API_URL = 'https://api.openai.com/v1'


def _guard(key):
    def check(request):
        if (request.url.scheme != 'https' or request.url.host != 'api.openai.com'
                or request.url.port not in (None, 443)):
            raise ValueError('provider_destination_blocked')
        # Do not let ambient SDK custom headers override routing/authentication.
        request.headers['Host'] = 'api.openai.com'
        request.headers['Authorization'] = 'Bearer ' + key
        request.headers.pop('Proxy-Authorization', None)
    return check


def create_client(*, api_key=None, timeout=60, max_retries=0, asynchronous=False, transport=None):
    import openai

    key = get_key() or api_key
    if not key:
        raise ValueError('API key not configured')
    guard = _guard(key)
    kwargs = dict(timeout=timeout, follow_redirects=False, trust_env=False, verify=True)
    if transport is not None:  # in-process offline tests only; no user/API setting
        kwargs['transport'] = transport
    if asynchronous:
        async def check(request):
            guard(request)
        http = openai.DefaultAsyncHttpxClient(event_hooks={'request': [check]}, **kwargs)
        cls = openai.AsyncOpenAI
    else:
        http = openai.DefaultHttpxClient(event_hooks={'request': [guard]}, **kwargs)
        cls = openai.OpenAI
    try:
        return cls(api_key=key, admin_api_key='', organization='', project='', webhook_secret='',
                   base_url=API_URL, timeout=timeout, max_retries=max_retries, http_client=http,
                   default_headers={'Authorization': 'Bearer ' + key})
    except Exception:
        if not asynchronous:
            http.close()
        raise
