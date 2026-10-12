"""Real httpcore proxy handshake with owned endpoint DNS and logical authority."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from kazma_core.web_acquire import dns_worker
from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport
from kazma_core.web_acquire.result import AcquisitionError


@pytest.mark.asyncio
async def test_actual_connect_handshake_preserves_target_and_proxy_auth(monkeypatch):
    observed = []
    finished = asyncio.Event()
    async def proxy(reader, writer):
        try:
            observed.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'HTTP/1.1 502 Fixture refusal\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass  # HTTPX may close an already-completed fixture response.
            finally:
                finished.set()
    server = await asyncio.start_server(proxy, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    resolver = AsyncMock(return_value='127.0.0.1')
    monkeypatch.setattr(dns_worker, 'resolve_endpoint', resolver)
    try:
        async with httpx.AsyncClient(transport=EndpointAsyncTransport(proxy=f'http://operator:secret@proxy.example:{port}'),
                trust_env=False, timeout=2) as client:
            with pytest.raises(httpx.ProxyError):
                await client.get('https://target.example/docs?q=private')
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        server.close()
        await server.wait_closed()
    resolver.assert_awaited_once_with('proxy.example', trusted_proxy=True)
    assert observed[0].startswith(b'CONNECT target.example:443 HTTP/1.1\r\n')
    assert b'Proxy-Authorization: Basic ' in observed[0]
    assert b'private' not in observed[0] and b'127.0.0.1' not in observed[0]


@pytest.mark.asyncio
async def test_proxy_backend_refuses_unexpected_direct_destination():
    transport = EndpointAsyncTransport(proxy='http://proxy.example:1080')
    try:
        with pytest.raises(AcquisitionError, match='unexpected endpoint'):
            await transport._pool._network_backend.connect_tcp('target.example', 443)
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_factory_proxy_mount_cannot_bypass_owned_transport(monkeypatch):
    from kazma_core.proxy.client import get_scraping_client

    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy')
    async with get_scraping_client(proxy='http://proxy.example:1080') as client:
        assert isinstance(client._transport, EndpointAsyncTransport)
        assert client._transport_for_url(httpx.URL('https://target.example')) is client._transport


def test_sync_factory_proxy_mount_cannot_bypass_owned_transport(monkeypatch):
    from kazma_core.proxy.client import get_scraping_client_sync
    from kazma_core.web_acquire.endpoint_transport import EndpointTransport

    monkeypatch.setenv('KAZMA_ACQUISITION_MODE', 'local_first_with_remote_fallback')
    monkeypatch.setenv('KAZMA_ACQUISITION_REMOTE_PROVIDERS', 'proxy')
    with get_scraping_client_sync(proxy='http://proxy.example:1080') as client:
        assert isinstance(client._transport, EndpointTransport)
        assert client._transport_for_url(httpx.URL('https://target.example')) is client._transport


@pytest.mark.asyncio
async def test_sync_proxy_transport_refuses_to_block_an_event_loop(monkeypatch):
    from kazma_core.web_acquire.endpoint_transport import EndpointTransport

    resolver = AsyncMock(side_effect=AssertionError('DNS must not start on the event loop'))
    monkeypatch.setattr(dns_worker, 'resolve_endpoint', resolver)
    with EndpointTransport(proxy='http://proxy.example:1080') as transport:
        with pytest.raises(AcquisitionError, match='event loop'):
            transport._pool._network_backend.connect_tcp('proxy.example', 1080)
    resolver.assert_not_awaited()


def test_sync_transport_uses_owned_dns_literal_and_keeps_logical_http_authority(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from kazma_core.web_acquire.endpoint_transport import EndpointTransport

    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get('Host')))
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    resolver = AsyncMock(return_value='127.0.0.1')
    monkeypatch.setattr(dns_worker, 'resolve_endpoint', resolver)
    try:
        with httpx.Client(transport=EndpointTransport(), trust_env=False, timeout=2) as client:
            response = client.get(f'http://hosted.example:{server.server_port}/balance?account=fixture')
            assert response.text == 'ok'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    resolver.assert_awaited_once_with('hosted.example', trusted_proxy=False)
    assert observed == [('/balance?account=fixture', f'hosted.example:{server.server_port}')]


@pytest.mark.asyncio
@pytest.mark.parametrize('synchronous', [False, True])
async def test_literal_endpoint_preserves_verified_tls_sni_and_authority(monkeypatch, tmp_path, synchronous):
    import ssl
    from datetime import UTC, datetime, timedelta
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from kazma_core.web_acquire.endpoint_transport import EndpointTransport

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'hosted.example')])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('hosted.example')]), critical=False)
        .sign(key, hashes.SHA256()))
    certificate, private = tmp_path / 'fixture.pem', tmp_path / 'fixture.key'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(certificate, private)
    observed, names, finished = [], [], asyncio.Event()
    server_tls.set_servername_callback(lambda socket, name, context: names.append(name))
    client_tls = ssl.create_default_context(cafile=str(certificate))
    async def handler(reader, writer):
        try:
            observed.append(await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass  # Verified response completed; Windows TLS peer closed.
            finally:
                finished.set()
    server = await asyncio.start_server(handler, '127.0.0.1', 0, ssl=server_tls)
    port = server.sockets[0].getsockname()[1]
    resolver = AsyncMock(return_value='127.0.0.1')
    monkeypatch.setattr(dns_worker, 'resolve_endpoint', resolver)
    try:
        if synchronous:
            def fetch():
                with httpx.Client(transport=EndpointTransport(verify=client_tls), trust_env=False, timeout=3) as client:
                    return client.get(f'https://hosted.example:{port}/fixture').text
            assert await asyncio.to_thread(fetch) == 'ok'
        else:
            async with httpx.AsyncClient(transport=EndpointAsyncTransport(verify=client_tls), trust_env=False, timeout=3) as client:
                assert (await client.get(f'https://hosted.example:{port}/fixture')).text == 'ok'
        await asyncio.wait_for(finished.wait(), 3)
    finally:
        server.close()
        await server.wait_closed()
    assert names == ['hosted.example']
    assert f'Host: hosted.example:{port}\r\n'.encode() in observed[0]
    resolver.assert_awaited_once_with('hosted.example', trusted_proxy=False)
