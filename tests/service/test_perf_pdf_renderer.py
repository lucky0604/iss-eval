"""Offline checks for the Chrome PDF renderer's DevTools transport.

The browser itself is exercised by the Phase 08/09 real-PDF evidence; these
tests pin the protocol pieces that must not silently regress: client frames are
masked, extended lengths round-trip, pings are answered, and the print call
carries the localized page-number footer.
"""

from __future__ import annotations

import base64
import socket
import threading
from pathlib import Path

import pytest

from evalscope.service.perf_exports import pdf


def _pair():
    left, right = socket.socketpair()
    return left, right


def test_client_frames_are_masked_and_roundtrip_all_lengths():
    left, right = _pair()
    try:
        for payload in (b'{"id":1}', b'x' * 200, b'y' * 70000):
            frame = pdf._encode_frame(0x1, payload)
            assert frame[0] == 0x81 and frame[1] & 0x80  # FIN + text, client mask set
            # A large frame fills the kernel buffer, so the sender must not run
            # on the same thread that drains it.
            sender = threading.Thread(target=left.sendall, args=(frame,))
            sender.start()
            fin, opcode, received = pdf._read_frame(right)
            sender.join(timeout=5)
            assert (fin, opcode, received) == (True, 0x1, payload)
    finally:
        left.close()
        right.close()


def test_a_ping_is_answered_while_waiting_for_the_reply():
    left, right = _pair()
    client = pdf._WebSocket.__new__(pdf._WebSocket)
    client._socket = left
    try:
        right.sendall(pdf._encode_frame(0x9, b'ping'))
        right.sendall(pdf._encode_frame(0x1, '{"id": 1}'.encode()))
        # _encode_frame masks client frames, so the pong is written masked and
        # this server-side reader decodes it back.
        result = {}

        def answer():
            result['value'] = client.recv_text()

        worker = threading.Thread(target=answer)
        worker.start()
        fin, opcode, payload = pdf._read_frame(right)
        worker.join(timeout=5)
    finally:
        left.close()
        right.close()
    assert (fin, opcode, payload) == (True, 0xA, b'ping')
    assert result['value'] == '{"id": 1}'


class _RecordingDevTools:
    def __init__(self) -> None:
        self.calls = []

    def call(self, method, params=None, session=None):
        self.calls.append((method, params or {}, session))
        if method == 'Target.createTarget':
            return {'targetId': 'target-1'}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'session-1'}
        if method == 'Runtime.evaluate':
            if (params or {}).get('expression') == 'document.readyState':
                return {'result': {'value': 'complete'}}
            return {'result': {'value': True}}
        if method == 'Page.printToPDF':
            return {'data': base64.b64encode(b'%PDF-1.7 rendered').decode()}
        return {}


def test_print_uses_a_localized_footer_and_writes_the_decoded_pdf(tmp_path):
    html = tmp_path / 'report.html'
    html.write_text('<html lang="zh"></html>', encoding='utf-8')
    output = tmp_path / 'report.pdf'
    devtools = _RecordingDevTools()

    pdf._print_over_devtools(devtools, html, output, 'zh', timeout=5)

    assert output.read_bytes() == b'%PDF-1.7 rendered'
    printed = next(params for method, params, _session in devtools.calls if method == 'Page.printToPDF')
    assert printed['displayHeaderFooter'] is True
    assert printed['preferCSSPageSize'] is True
    assert 'pageNumber' in printed['footerTemplate'] and 'totalPages' in printed['footerTemplate']
    assert '性能压测矩阵报告' in printed['footerTemplate']
    assert all(session == 'session-1' for method, _params, session in devtools.calls if method.startswith('Page.'))
    assert devtools.calls[-1][0] == 'Target.closeTarget'

def test_a_chrome_candidate_must_be_an_executable_file(tmp_path, monkeypatch):
    script = tmp_path / 'chrome'
    script.write_text('#!/bin/sh\n', encoding='utf-8')
    monkeypatch.setenv(pdf.CHROME_ENV, str(script))
    monkeypatch.setattr(pdf, '_CHROME_CANDIDATES', ())
    monkeypatch.setattr(pdf.shutil, 'which', lambda _name: None)
    # A plain file is not a browser: modes matter, not existence.
    assert pdf._chrome_path() is None
    script.chmod(0o755)
    assert pdf._chrome_path() == str(script)


def test_the_capability_probe_needs_a_launchable_browser(monkeypatch):
    monkeypatch.setattr(pdf, '_playwright_probe', lambda: (False, "Executable doesn't exist at chromium_headless_shell"))
    monkeypatch.setattr(pdf, '_chrome_path', lambda: '/usr/bin/google-chrome')
    assert pdf.pdf_runtime() == (True, 'chrome', '')

    monkeypatch.setattr(pdf, '_chrome_path', lambda: None)
    available, renderer, reason = pdf.pdf_runtime()
    assert available is False and renderer is None
    assert 'cannot launch Chromium' in reason and 'playwright install chromium' in reason


def _fake_pdf(path: Path) -> None:
    path.write_bytes(b'%PDF-1.7 fake ' + b'x' * 2048)


def test_auto_render_falls_back_to_the_local_chrome(tmp_path, monkeypatch):
    html = tmp_path / 'report.html'
    html.write_text('<html lang="zh"></html>', encoding='utf-8')
    output = tmp_path / 'report.pdf'
    monkeypatch.setenv(pdf.RENDERER_ENV, 'auto')
    monkeypatch.setattr(pdf, '_playwright_probe', lambda: (True, ''))
    monkeypatch.setattr(pdf, '_chrome_path', lambda: '/usr/bin/google-chrome')

    def crashed_playwright(*_args, **_kwargs):
        raise pdf.PdfUnavailable('playwright crashed')

    monkeypatch.setattr(pdf, '_render_with_playwright', crashed_playwright)
    monkeypatch.setattr(pdf, '_render_with_chrome', lambda _html, target, _locale: _fake_pdf(Path(target)))

    assert pdf.render_pdf(html, output, locale='zh') == 'chrome'
    assert output.read_bytes().startswith(b'%PDF-1.7')


def test_an_explicit_playwright_request_never_switches_renderer(tmp_path, monkeypatch):
    html = tmp_path / 'report.html'
    html.write_text('<html></html>', encoding='utf-8')
    output = tmp_path / 'report.pdf'
    monkeypatch.setenv(pdf.RENDERER_ENV, 'playwright')
    monkeypatch.setattr(pdf, '_playwright_probe', lambda: (True, ''))
    monkeypatch.setattr(pdf, '_chrome_path', lambda: '/usr/bin/google-chrome')
    monkeypatch.setattr(
        pdf,
        '_render_with_playwright',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(pdf.PdfUnavailable('playwright crashed')),
    )
    monkeypatch.setattr(pdf, '_render_with_chrome', lambda _html, target, _locale: _fake_pdf(Path(target)))

    with pytest.raises(pdf.PdfUnavailable):
        pdf.render_pdf(html, output, locale='en')
