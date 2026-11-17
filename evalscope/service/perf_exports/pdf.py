"""PDF rendering with an explicit, lazily imported runtime.

Playwright's Chromium is preferred because it can add real page numbers and a
localized footer. A locally installed Chrome/Chromium is the fallback when
Playwright is absent; it prints through the DevTools protocol with the same
footer so both runtimes paginate identically. Nothing here downloads a browser
at export time: an unavailable runtime fails the PDF export with a concrete
reason while HTML and CSV keep working.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from evalscope.utils.logger import get_logger

from .text import fill, text

logger = get_logger()

PDF_TIMEOUT_SECONDS = 180
RENDERER_ENV = 'EVALSCOPE_PDF_RENDERER'
CHROME_ENV = 'EVALSCOPE_CHROME'

_CHROME_CANDIDATES = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
)


class PdfUnavailable(RuntimeError):
    """The PDF renderer cannot run here; the message is shown to the user."""


def _is_executable(path: str) -> bool:
    """A renderer is usable only when the file exists and may be executed."""
    return Path(path).is_file() and os.access(path, os.X_OK)


def _chrome_path() -> Optional[str]:
    configured = os.environ.get(CHROME_ENV)
    if configured and _is_executable(configured):
        return configured
    for candidate in _CHROME_CANDIDATES:
        if _is_executable(candidate):
            return candidate
    for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome'):
        found = shutil.which(name)
        if found and _is_executable(found):
            return found
    return None


@lru_cache(maxsize=1)
def _playwright_probe() -> Tuple[bool, str]:
    """Launch Playwright's Chromium once and report what actually happened.

    An installed Python package without a downloaded browser cannot print, so
    the probe must exercise the browser rather than only the import. The result
    is cached: installing a browser is followed by a service restart anyway.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, 'the playwright package is not installed'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as error:  # noqa: BLE001 - any launch failure means "not available"
        detail = str(error).strip().splitlines()[0][:300] if str(error).strip() else type(error).__name__
        return False, f'{type(error).__name__}: {detail}'
    return True, ''


def pdf_runtime() -> Tuple[bool, Optional[str], str]:
    """Return ``(available, renderer, reason)`` without launching a browser."""
    requested = os.environ.get(RENDERER_ENV, 'auto').lower()
    playwright_ok, playwright_reason = _playwright_probe()
    chrome = _chrome_path()
    if requested == 'playwright' and not playwright_ok:
        return (
            False,
            None,
            f'EVALSCOPE_PDF_RENDERER=playwright but Playwright cannot launch Chromium ({playwright_reason}). '
            'Run "playwright install chromium" once on this host.',
        )
    if requested == 'chrome' and chrome is None:
        return False, None, 'EVALSCOPE_PDF_RENDERER=chrome but no executable Chrome/Chromium binary was found'
    if playwright_ok and requested in ('auto', 'playwright'):
        return True, 'playwright', ''
    if chrome is not None and requested in ('auto', 'chrome'):
        return True, 'chrome', ''
    playwright_note = (
        f'Playwright is installed but cannot launch Chromium ({playwright_reason}). '
        if playwright_reason != 'the playwright package is not installed'
        else 'Playwright is not installed. '
    )
    return (
        False,
        None,
        f'No PDF runtime: {playwright_note}Install it with pip install "evalscope[pdf]" && playwright install '
        'chromium, or install Chrome/Chromium locally and set EVALSCOPE_CHROME.',
    )


def _footer_template(locale: str) -> str:
    t = text(locale)
    page = fill(t['page'], page='<span class="pageNumber"></span>', total='<span class="totalPages"></span>')
    return (
        '<div style="width:100%;font-size:7px;color:#666;padding:0 8mm;'
        "font-family:'PingFang SC','Noto Sans CJK SC',sans-serif;display:flex;justify-content:space-between;\">"
        f'<span>{t["title"]}</span><span>{page}</span></div>'
    )


def _render_with_playwright(html_path: Path, pdf_path: Path, locale: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001 - the caller turns this into an export error
            raise PdfUnavailable(
                'The Playwright Chromium browser is not installed. Run "playwright install chromium" once on the '
                f'host that renders exports ({type(error).__name__}).'
            ) from error
        try:
            page = browser.new_page()
            page.set_default_timeout(PDF_TIMEOUT_SECONDS * 1000)

            def block_remote(route) -> None:  # noqa: ANN001 - Playwright route object
                url = route.request.url
                if url.startswith('file:') or url.startswith('data:') or url.startswith('about:'):
                    route.continue_()
                else:
                    route.abort()

            page.route('**/*', block_remote)
            page.goto(html_path.as_uri(), wait_until='load', timeout=PDF_TIMEOUT_SECONDS * 1000)
            page.evaluate('() => document.fonts.ready.then(() => true)')
            page.pdf(
                path=str(pdf_path),
                format='A3',
                landscape=True,
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template='<div></div>',
                footer_template=_footer_template(locale),
                margin={'top': '8mm', 'bottom': '14mm', 'left': '8mm', 'right': '8mm'},
            )
        finally:
            browser.close()


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    """Build a masked client-to-server WebSocket frame (RFC 6455)."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header.extend(struct.pack('!H', length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack('!Q', length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + masked


def _recv_exact(connection: socket.socket, count: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < count:
        chunk = connection.recv(count - len(buffer))
        if not chunk:
            raise PdfUnavailable('The PDF renderer closed the DevTools connection')
        buffer.extend(chunk)
    return bytes(buffer)


def _read_frame(connection: socket.socket) -> Tuple[bool, int, bytes]:
    """Read one WebSocket frame as ``(fin, opcode, payload)``."""
    first, second = _recv_exact(connection, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack('!H', _recv_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', _recv_exact(connection, 8))[0]
    if length > 64 * 1024 * 1024:
        raise PdfUnavailable('The PDF renderer sent an oversized DevTools frame')
    mask = _recv_exact(connection, 4) if masked else b''
    payload = _recv_exact(connection, length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return fin, opcode, payload


class _WebSocket:
    """A minimal text-frame WebSocket client for the local DevTools endpoint.

    Only Chrome's loopback DevTools socket is a supported peer: no proxies, no
    TLS, no extensions. Keeping this local avoids adding a runtime dependency
    just to print a PDF.
    """

    _GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

    def __init__(self, url: str, timeout: float = PDF_TIMEOUT_SECONDS) -> None:
        parsed = urlparse(url)
        connection = socket.create_connection((parsed.hostname or '127.0.0.1', parsed.port or 80), timeout=timeout)
        connection.settimeout(timeout)
        self._socket = connection
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path or '/'
        if parsed.query:
            path = f'{path}?{parsed.query}'
        request = (
            f'GET {path} HTTP/1.1\r\n'
            f'Host: {parsed.hostname}:{parsed.port}\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key}\r\n'
            'Sec-WebSocket-Version: 13\r\n'
            '\r\n'
        )
        connection.sendall(request.encode())
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            header.extend(_recv_exact(connection, 1))
            if len(header) > 64 * 1024:
                raise PdfUnavailable('The PDF renderer returned an invalid DevTools handshake')
        lines = bytes(header).decode('latin-1').split('\r\n')
        if ' 101 ' not in lines[0]:
            raise PdfUnavailable(f'Chrome refused the DevTools connection: {lines[0]}')
        expected = base64.b64encode(hashlib.sha1((key + self._GUID).encode()).digest()).decode()
        if not any(
            line.lower().startswith('sec-websocket-accept:') and line.split(':', 1)[1].strip() == expected
            for line in lines[1:]
        ):
            raise PdfUnavailable('The PDF renderer returned an invalid DevTools handshake')

    def send_text(self, payload: str) -> None:
        self._socket.sendall(_encode_frame(0x1, payload.encode('utf-8')))

    def recv_text(self) -> str:
        buffer = bytearray()
        while True:
            fin, opcode, payload = _read_frame(self._socket)
            if opcode == 0x9:  # ping
                self._socket.sendall(_encode_frame(0xA, payload))
                continue
            if opcode == 0x8:
                raise PdfUnavailable('The PDF renderer closed the DevTools connection')
            if opcode in (0x1, 0x0):
                buffer.extend(payload)
                if fin:
                    return buffer.decode('utf-8')
                continue
            if opcode == 0xA:  # unsolicited pong
                continue
            raise PdfUnavailable(f'The PDF renderer sent an unsupported WebSocket opcode {opcode}')

    def close(self) -> None:
        try:
            self._socket.sendall(_encode_frame(0x8, b''))
        except OSError:
            pass
        self._socket.close()


class _DevTools:
    """One-request-at-a-time CDP client over a flat session."""

    def __init__(self, websocket: _WebSocket) -> None:
        self._websocket = websocket
        self._next_id = 0

    def call(self, method: str, params: Optional[dict] = None, session: Optional[str] = None) -> dict:
        self._next_id += 1
        message = {'id': self._next_id, 'method': method, 'params': params or {}}
        if session is not None:
            message['sessionId'] = session
        self._websocket.send_text(json.dumps(message))
        while True:
            reply = json.loads(self._websocket.recv_text())
            if reply.get('id') != self._next_id:
                continue  # progress events are irrelevant: only the reply matters
            if 'error' in reply:
                detail = reply['error'].get('message', 'unknown error')
                raise PdfUnavailable(f'Chrome refused {method}: {detail}')
            return reply.get('result', {})


def _wait_for_devtools(profile: Path, process: subprocess.Popen, timeout: float) -> str:
    """Wait for Chrome's DevToolsActivePort file and return the browser socket URL."""
    port_file = profile / 'DevToolsActivePort'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PdfUnavailable(f'Chrome exited before the report could be printed (exit {process.returncode})')
        if port_file.is_file():
            lines = port_file.read_text(encoding='utf-8').splitlines()
            if len(lines) >= 2 and lines[0].strip().isdigit():
                return f'ws://127.0.0.1:{lines[0].strip()}/{lines[1].strip().lstrip("/")}'
        time.sleep(0.05)
    raise PdfUnavailable('Chrome did not expose its DevTools endpoint in time')


def _wait_for_document(devtools: _DevTools, session: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = devtools.call(
            'Runtime.evaluate', {'expression': 'document.readyState', 'returnByValue': True}, session=session
        )
        if state.get('result', {}).get('value') == 'complete':
            return
        time.sleep(0.1)
    raise PdfUnavailable('The report page did not finish loading')


def _print_over_devtools(devtools: _DevTools, html_path: Path, pdf_path: Path, locale: str, timeout: float) -> None:
    """Create a page target, wait for it, and print it with a localized footer."""
    target = devtools.call('Target.createTarget', {'url': html_path.as_uri()})
    attached = devtools.call('Target.attachToTarget', {'targetId': target['targetId'], 'flatten': True})
    session = attached['sessionId']
    try:
        devtools.call('Page.enable', session=session)
        _wait_for_document(devtools, session, timeout)
        devtools.call(
            'Runtime.evaluate',
            {'expression': 'document.fonts.ready.then(() => true)', 'awaitPromise': True, 'returnByValue': True},
            session=session,
        )
        printed = devtools.call(
            'Page.printToPDF',
            {
                'displayHeaderFooter': True,
                'headerTemplate': '<div></div>',
                'footerTemplate': _footer_template(locale),
                'printBackground': True,
                'preferCSSPageSize': True,
                'paperWidth': 16.54,
                'paperHeight': 11.69,
                'landscape': True,
                'marginTop': 0.31,
                'marginBottom': 0.55,
                'marginLeft': 0.31,
                'marginRight': 0.31,
            },
            session=session,
        )
        pdf_path.write_bytes(base64.b64decode(printed['data']))
    finally:
        try:
            devtools.call('Target.closeTarget', {'targetId': target['targetId']})
        except (PdfUnavailable, OSError, ValueError):
            pass


def _render_with_chrome(html_path: Path, pdf_path: Path, locale: str) -> None:
    chrome = _chrome_path()
    if chrome is None:
        raise PdfUnavailable('No Chrome/Chromium binary was found')
    with tempfile.TemporaryDirectory(prefix='evalscope-pdf-profile-') as profile:
        command = [
            chrome,
            '--headless=new',
            '--disable-gpu',
            '--disable-extensions',
            '--no-first-run',
            '--no-default-browser-check',
            '--user-data-dir=' + profile,
            # Force every hostname to fail resolution: an export never reaches the network.
            '--host-resolver-rules=MAP * ~NOTFOUND',
            # A random loopback port; Chrome writes it to DevToolsActivePort.
            '--remote-debugging-port=0',
            'about:blank',
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            websocket = _WebSocket(_wait_for_devtools(Path(profile), process, PDF_TIMEOUT_SECONDS))
            try:
                _print_over_devtools(_DevTools(websocket), html_path, pdf_path, locale, PDF_TIMEOUT_SECONDS)
            finally:
                websocket.close()
        except subprocess.TimeoutExpired as error:
            raise PdfUnavailable(f'PDF rendering timed out after {PDF_TIMEOUT_SECONDS}s') from error
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def render_pdf(html_path: Path, pdf_path: Path, *, locale: str = 'en') -> str:
    """Render *html_path* to *pdf_path*; returns the renderer that succeeded."""
    available, renderer, reason = pdf_runtime()
    if not available or renderer is None:
        raise PdfUnavailable(reason)
    temporary = pdf_path.with_name(f'.{pdf_path.name}.tmp')
    try:
        if renderer == 'playwright':
            try:
                _render_with_playwright(html_path, temporary, locale)
            except PdfUnavailable:
                if os.environ.get(RENDERER_ENV, 'auto').lower() == 'playwright' or _chrome_path() is None:
                    raise
                logger.warning('Falling back to the local Chrome renderer after Playwright failed')
                renderer = 'chrome'
                _render_with_chrome(html_path, temporary, locale)
        else:
            _render_with_chrome(html_path, temporary, locale)
        if not temporary.is_file() or temporary.stat().st_size < 1024:
            raise PdfUnavailable('The PDF renderer produced an empty document')
        os.replace(temporary, pdf_path)
        return renderer
    finally:
        temporary.unlink(missing_ok=True)
