from __future__ import annotations
import json
import random
import socket
import time
import urllib.error
import urllib.request
from .errors import ApiError
BASE_URL = 'https://openrouter.ai/api/v1'
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str=BASE_URL, max_retries: int=4):
        if not api_key:
            raise ApiError('No OpenRouter API key configured.')
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.max_retries = max_retries
    def post_json(self, path: str, payload: dict, timeout: float=180.0) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = json.dumps(payload).encode('utf-8')
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json', 'X-Title': 'Bengali Subtitle Studio'}
        last_error: ApiError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(min(60.0, 2.0 ** attempt + random.uniform(0, 1)))
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method='POST')
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                err_body = _safe_read(exc)
                last_error = ApiError(f'OpenRouter returned HTTP {exc.code} for {path}: {_error_message(err_body)}', status=exc.code, body=err_body)
                if exc.code not in RETRYABLE_STATUS:
                    raise last_error from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                last_error = ApiError(f'Network error calling {path}: {exc}')
        assert last_error is not None
        raise last_error
    def chat(self, model: str, messages: list[dict], temperature: float=0.2, timeout: float=300.0, **extra) -> str:
        payload = {'model': model, 'messages': messages, 'temperature': temperature, **extra}
        data = self.post_json('chat/completions', payload, timeout=timeout)
        try:
            content = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiError(f'Unexpected chat response shape: {json.dumps(data)[:800]}') from exc
        if not isinstance(content, str) or not content.strip():
            raise ApiError('LLM returned an empty response.')
        return content
def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode('utf-8', errors='replace')[:4000]
    except Exception:
        return ''
def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
        return data.get('error', {}).get('message') or body[:500]
    except (json.JSONDecodeError, AttributeError):
        return body[:500]
