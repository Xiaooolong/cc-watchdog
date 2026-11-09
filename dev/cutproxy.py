"""Dev tool: a reverse proxy that can cut a streaming /v1/messages response mid-way,
to reproduce "API Error: Connection lost mid-response" on demand.

Usage: python dev/cutproxy.py  (listens on 127.0.0.1:8899), then start Claude Code with
ANTHROPIC_BASE_URL=http://127.0.0.1:8899. Create the file `CUT` next to this script to cut the
next streaming response right after its first completed text block.
Env: UPSTREAM (default: ANTHROPIC_BASE_URL from ~/.claude/settings.json, else api.anthropic.com),
UPSTREAM_PROXY (optional HTTP proxy for the upstream connection), PORT.
"""
import datetime
import http.client
import http.server
import json
import os
import platform
import socket
import socketserver
import struct
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CUT_FLAG = os.path.join(HERE, "CUT")
LOG = os.path.join(HERE, "proxy.log")
LISTEN = ("127.0.0.1", int(os.environ.get("PORT", "8899")))
UPSTREAM_PROXY = os.environ.get("UPSTREAM_PROXY", "")
HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "te",
       "trailer", "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}


def default_upstream():
    if os.environ.get("UPSTREAM"):
        return os.environ["UPSTREAM"]
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as f:
            return json.load(f).get("env", {}).get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
    except Exception:
        return "https://api.anthropic.com"


UPSTREAM = urllib.parse.urlsplit(default_upstream())


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='milliseconds')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def open_upstream():
    host = UPSTREAM.hostname
    port = UPSTREAM.port or (443 if UPSTREAM.scheme == "https" else 80)
    if UPSTREAM_PROXY:
        p = urllib.parse.urlsplit(UPSTREAM_PROXY)
        conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=600) if UPSTREAM.scheme == "https" \
            else http.client.HTTPConnection(p.hostname, p.port, timeout=600)
        conn.set_tunnel(host, port)
    elif UPSTREAM.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=600)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=600)
    return conn


def rst_close(sock):
    # Abortive close (RST) so the client sees a broken connection, not a clean end.
    fmt = "HH" if platform.system() == "Windows" else "ii"
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(fmt, 1, 0))
    except OSError:
        pass
    sock.close()


def auth_kind(headers):
    if headers.get("x-api-key"):
        return "x-api-key"
    a = headers.get("authorization", "")
    return ("bearer:" + a.split()[1][:7] + "...") if a.lower().startswith("bearer ") and len(a.split()) > 1 else (a[:6] or "none")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_any(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        info = {}
        if self.path.split("?")[0].endswith("/v1/messages") and body:
            try:
                j = json.loads(body)
                info = {"model": j.get("model"), "stream": j.get("stream"), "tools": len(j.get("tools") or [])}
            except Exception:
                pass
        want_cut = bool(info.get("stream")) and info.get("tools", 0) > 0 and os.path.exists(CUT_FLAG)

        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = UPSTREAM.hostname
        headers["Accept-Encoding"] = "identity"
        if body:
            headers["Content-Length"] = str(len(body))
        path = (UPSTREAM.path.rstrip("/") + self.path) if UPSTREAM.path not in ("", "/") else self.path

        try:
            up = open_upstream()
            up.request(self.command, path, body=body or None, headers=headers)
            resp = up.getresponse()
        except Exception as e:
            log(f"{self.command} {self.path} upstream error: {e!r}")
            msg = json.dumps({"type": "error", "error": {"type": "api_error", "message": f"cutproxy: {e}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
            return

        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP and k.lower() != "content-encoding":
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        is_sse = "text/event-stream" in (resp.getheader("content-type") or "")
        block_types, buf, total, cut = {}, b"", 0, False

        def send(data):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))

        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if not (want_cut and is_sse):
                    send(chunk)
                    continue
                buf = (buf + chunk).replace(b"\r\n", b"\n")
                while True:
                    i = buf.find(b"\n\n")
                    if i < 0:
                        break
                    event, buf = buf[: i + 2], buf[i + 2:]
                    send(event)
                    ev, data = None, None
                    for line in event.decode("utf-8", "replace").split("\n"):
                        if line.startswith("event:"):
                            ev = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                    try:
                        d = json.loads(data) if data else {}
                    except Exception:
                        d = {}
                    ev = ev or d.get("type")
                    if ev == "content_block_start":
                        block_types[d.get("index")] = (d.get("content_block") or {}).get("type")
                    elif ev == "content_block_stop" and block_types.get(d.get("index")) == "text":
                        cut = True
                        break
                if cut:
                    break
            if cut:
                # CUT may hold a count (cut the next N responses); empty means 1.
                try:
                    with open(CUT_FLAG) as f:
                        left = int(f.read().strip() or 1) - 1
                    if left > 0:
                        with open(CUT_FLAG, "w") as f:
                            f.write(str(left))
                    else:
                        os.remove(CUT_FLAG)
                except (OSError, ValueError):
                    try:
                        os.remove(CUT_FLAG)
                    except OSError:
                        pass
                rst_close(self.connection)
            else:
                if buf:
                    send(buf)
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            log(f"client gone: {e!r}")
        finally:
            up.close()
            log(f"{self.command} {self.path} {info or ''} auth={auth_kind(self.headers)} -> {resp.status} "
                f"bytes={total} {'CUT' if cut else ('cut-armed-no-text-block' if want_cut else '')}")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_any


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    log(f"listening on {LISTEN[0]}:{LISTEN[1]} -> {UPSTREAM.geturl()} via {UPSTREAM_PROXY or 'direct'}")
    try:
        Server(LISTEN, Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
