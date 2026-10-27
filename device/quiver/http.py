"""A small HTTP/1.0 client over socket + TLS.

HTTP/1.0 so servers answer with Content-Length (or close) and never chunked encoding, which keeps
reading simple. Bodies are read whole (frames are 30-48 KB, well within an ESP32's RAM), except
downloads, which go to a sink as they arrive (updates are written straight to flash).

TLS is always verified: against the CA certificates the app passes (Google's roots in ca.py, or
board.json's ca_file). There is no unverified mode; plain HTTP is for an API on the local network
only (board.py checks that). The same code runs on CPython for the tests.
"""

import json
import socket

try:
    import ssl
except ImportError:  # some ports call it ussl
    import ussl as ssl


class HttpError(OSError):
    """The server couldn't be reached or answered garbage (not an HTTP error status)."""


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def json(self):
        return json.loads(self.body) if self.body else None


def _split(url):
    scheme, rest = url.split("://", 1)
    host, _, path = rest.partition("/")
    port = 443 if scheme == "https" else 80
    if ":" in host:
        host, p = host.rsplit(":", 1)
        port = int(p)
    return scheme == "https", host, port, "/" + path


_context = None


def _tls(sock, host, ca):
    """ca: the CA certificates (DER or PEM bytes, or a list of them) the server's must chain to."""
    global _context
    if not ca:
        raise HttpError("no CA certificates for TLS")
    if _context is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_REQUIRED
        for cert in (ca if isinstance(ca, list) else [ca]):
            ctx.load_verify_locations(cadata=cert)
        _context = ctx
    return _context.wrap_socket(sock, server_hostname=host)


def request(method, url, body=None, headers=None, timeout=20, ca=None, sink=None):
    """Returns a Response. Raises HttpError when there's no usable answer. With `sink`, a 200's
    body isn't read into memory: sink(stream, length) reads it (Response.body is then None)."""
    secure, host, port, path = _split(url)
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
        headers = dict(headers or {})
        headers["Content-Type"] = "application/json"
    lines = ["%s %s HTTP/1.0" % (method, path), "Host: %s" % host, "User-Agent: quiver-screen", "Connection: close"]
    for k, v in (headers or {}).items():
        lines.append("%s: %s" % (k, v))
    if body is not None:
        lines.append("Content-Length: %d" % len(body))

    try:
        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
    except OSError as e:
        raise HttpError("DNS: %s" % e)
    sock = socket.socket()
    try:
        sock.settimeout(timeout)
        sock.connect(addr)
        if secure:
            sock = _tls(sock, host, ca)
        _send(sock, ("\r\n".join(lines) + "\r\n\r\n").encode())
        if body is not None:
            _send(sock, body)
        stream = sock.makefile("rb") if hasattr(sock, "makefile") else sock
        status_line = stream.readline()
        if not status_line.startswith(b"HTTP/"):
            raise HttpError("Not an HTTP response: %r" % status_line[:40])
        status = int(status_line.split(None, 2)[1])
        hdrs = {}
        while True:
            line = stream.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode().partition(":")
            hdrs[k.strip().lower()] = v.strip()
        length = int(hdrs.get("content-length", "-1"))
        if sink is not None and status == 200:
            sink(stream, length)
            return Response(status, hdrs, None)
        data = _read(stream, length)
        return Response(status, hdrs, data)
    except (OSError, ValueError) as e:  # MicroPython raises ValueError for a certificate it won't trust
        if isinstance(e, HttpError):
            raise
        raise HttpError(str(e) or type(e).__name__)
    finally:
        sock.close()


def _send(sock, data):
    """MicroPython sockets and TLS streams have write(); CPython's have sendall()."""
    if hasattr(sock, "sendall"):
        sock.sendall(data)
    else:
        view, sent = memoryview(data), 0
        while sent < len(data):
            sent += sock.write(view[sent:]) or 0


def _read(stream, length):
    if length == 0:
        return b""
    if length < 0:  # until the server closes
        parts = []
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return b"".join(parts)
            parts.append(chunk)
    buf = bytearray(length)
    view = memoryview(buf)
    got = 0
    while got < length:
        n = stream.readinto(view[got:]) if hasattr(stream, "readinto") else None
        if n is None:
            chunk = stream.read(length - got)
            n = len(chunk)
            view[got:got + n] = chunk
        if not n:
            raise HttpError("Connection closed after %d of %d bytes" % (got, length))
        got += n
    return buf
