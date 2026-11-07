"""Memory client units: no cognee install and no network, just the HTTP contract."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ocm.memory import CogneeMemory, MemoryHit, MemoryUnavailable, check_dataset


class Recorder(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def log_message(self, *args):  # keep the test output quiet
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
        Recorder.seen.append((self.path, json.loads(raw)))
        body = json.dumps({"results": [{"text": "Ada Lovelace", "search_type": "CHUNKS"}]}
                          if self.path.endswith("/recall") else {}).encode()
        self.send_response(Recorder.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def service():
    Recorder.seen, Recorder.status = [], 200
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def remember(memory, text):
    asyncio.run(memory.remember(text))


def recall(memory, query):
    return asyncio.run(memory.recall(query))


def test_remember_and_recall_stay_inside_one_dataset(service):
    m = CogneeMemory(service, "maya_project")
    remember(m, "Ada Lovelace wrote the first algorithm.")
    assert Recorder.seen[-1] == ("/api/v1/remember",
                                 {"raw_data": ["Ada Lovelace wrote the first algorithm."],
                                  "dataset_name": "maya_project"})
    assert recall(m, "who wrote it?") == [MemoryHit("Ada Lovelace", "CHUNKS")]
    assert Recorder.seen[-1] == ("/api/v1/recall",
                                 {"query": "who wrote it?", "datasets": ["maya_project"], "search_type": "CHUNKS"})


@pytest.mark.parametrize("name", ["../other", "Other", "", "a b", "x" * 65, "a/b"])
def test_a_dataset_name_cannot_escape_its_scope(name):
    with pytest.raises(ValueError):
        check_dataset(name)


def test_failures_never_carry_memory_content(service):
    Recorder.status = 500
    m = CogneeMemory(service, "maya_project")
    with pytest.raises(MemoryUnavailable) as err:
        remember(m, "my bank PIN is 1234")
    assert "1234" not in str(err.value) and "bank" not in str(err.value)
    assert "HTTP 500" in str(err.value)
    with pytest.raises(MemoryUnavailable) as err:
        recall(m, "what is my PIN")
    assert "PIN" not in str(err.value)


def test_unreachable_service_is_reported_without_the_query(service):
    m = CogneeMemory("http://127.0.0.1:1", "maya_project", timeout=2)
    with pytest.raises(MemoryUnavailable) as err:
        recall(m, "private question")
    assert "private question" not in str(err.value)


def test_empty_input_is_refused_rather_than_stored(service):
    m = CogneeMemory(service, "maya_project")
    with pytest.raises(ValueError):
        remember(m, "   ")
    with pytest.raises(ValueError):
        recall(m, "")
    assert Recorder.seen == []


def test_repr_does_not_print_what_was_remembered():
    assert "Ada" not in repr(MemoryHit("Ada Lovelace", "CHUNKS"))
