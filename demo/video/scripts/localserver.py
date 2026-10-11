"""Chromium blocks fetch() on file:// URLs, so scenes are served over loopback."""
import contextlib, functools, threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@contextlib.contextmanager
def serve(root):
    handler = functools.partial(_Quiet, directory=str(root))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
