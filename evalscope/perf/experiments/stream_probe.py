"""Streaming completion probe shared by primer and warmup traffic."""

import asyncio
import json

import aiohttp


async def request_stream_completion(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    text: str,
    output_limit: int,
) -> str:
    """Drain one streaming completion and report only fully finished replies.

    Returns ``completed`` when the server sent ``[DONE]`` after a terminal
    ``finish_reason``; every other outcome names a distinct failure class so
    callers can surface it without scoring the request.
    """
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': text}],
        'stream': True,
        'max_completion_tokens': output_limit,
    }
    try:
        async with session.post(url, json=payload, allow_redirects=False) as response:
            if response.status < 200 or response.status >= 300:
                return 'http_error'
            finished = False
            async for line in response.content:
                line = line.strip()
                if not line.startswith(b'data:'):
                    continue
                value = line[5:].strip()
                if value == b'[DONE]':
                    return 'completed' if finished else 'invalid_stream'
                try:
                    chunk = json.loads(value)
                except (ValueError, UnicodeError):
                    return 'invalid_stream'
                if not isinstance(chunk, dict) or 'error' in chunk:
                    return 'invalid_stream'
                choices = chunk.get('choices')
                if choices is not None and not isinstance(choices, list):
                    return 'invalid_stream'
                for choice in choices or []:
                    if not isinstance(choice, dict):
                        return 'invalid_stream'
                    reason = choice.get('finish_reason')
                    if reason is not None and reason not in ('stop', 'length'):
                        return 'invalid_stream'
                    finished = finished or reason in ('stop', 'length')
            return 'incomplete_stream'
    except asyncio.TimeoutError:
        return 'timeout'
    except (aiohttp.ClientError, OSError, ValueError):
        return 'transport_error'
