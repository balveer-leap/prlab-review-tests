"""Local live canvas server: folder watching, payload route, event stream."""

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from prlab_eval import live


def write_report(folder: Path, tool: str, stamp: str, passed: int = 1) -> Path:
    path = folder / tool / f"review-eval-{tool}-org-x-groq-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": tool, "owner": "org-x", "generated_at": stamp, "passed": passed, "failed": 0,
        "precision": 1.0, "recall": 1.0, "f1": 1.0, "true_positives": 1, "false_negatives": 0,
        "false_positives": 0, "by_capability": [], "results": [],
    }
    path.write_text(json.dumps(payload))
    return path


def test_snapshot_ignores_generator_output_and_non_reports(tmp_path: Path) -> None:
    write_report(tmp_path, "qodo", "20260925T000000Z")
    (tmp_path / "comparison.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    assert list(live.snapshot(tmp_path)) == ["qodo/review-eval-qodo-org-x-groq-20260925T000000Z.json"]


def test_changed_reports_added_modified_and_removed(tmp_path: Path) -> None:
    first = write_report(tmp_path, "qodo", "20260925T000000Z")
    before = live.snapshot(tmp_path)
    second = write_report(tmp_path, "coderabbit", "20260925T000001Z")
    first.unlink()
    diff = live.changed(before, live.snapshot(tmp_path))
    assert diff == sorted([str(first.relative_to(tmp_path)), str(second.relative_to(tmp_path))])


def test_watcher_bumps_version_only_on_change(tmp_path: Path) -> None:
    write_report(tmp_path, "qodo", "20260925T000000Z")
    watcher = live.ReportWatcher(tmp_path, interval=0.01)
    assert watcher.poll_once() is False
    write_report(tmp_path, "qodo", "20260925T000009Z")
    assert watcher.poll_once() is True
    assert watcher.version == 1
    assert watcher.poll_once() is False


def test_payload_json_reports_missing_reports_as_message(tmp_path: Path) -> None:
    status, body = live.payload_json(tmp_path)
    data = json.loads(body)
    assert status == 200 and data["ok"] is False and "no reports" in data["error"]


@pytest.fixture
def server(tmp_path: Path):
    page = tmp_path / "index.html"
    page.write_text("<!doctype html><title>t</title>")
    reports = tmp_path / "reports"
    reports.mkdir()
    watcher = live.ReportWatcher(reports, interval=0.02)
    threading.Thread(target=watcher.run, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), live.make_handler(reports, watcher, page=page, heartbeat=0.2))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", reports
    watcher.stop()
    httpd.shutdown()
    httpd.server_close()


def test_routes_serve_page_payload_and_404(server) -> None:
    base, reports = server
    assert b"<title>t</title>" in urllib.request.urlopen(base + "/").read()
    data = json.loads(urllib.request.urlopen(base + "/api/payload").read())
    assert data["ok"] is False  # empty reports folder
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/nope")
    assert err.value.code == 404


def test_event_stream_announces_a_new_report(server) -> None:
    base, reports = server
    stream = urllib.request.urlopen(base + "/api/events", timeout=5)
    assert stream.readline().startswith(b"event: hello")
    stream.readline(); stream.readline()  # data line + blank line
    write_report(reports, "qodo", "20260925T000000Z")
    deadline = time.time() + 5
    while time.time() < deadline:
        line = stream.readline()
        if line.startswith(b"event: reports"):
            data = json.loads(stream.readline().split(b"data: ", 1)[1])
            assert data["version"] == 1
            assert data["changed"] == ["qodo/review-eval-qodo-org-x-groq-20260925T000000Z.json"]
            return
    pytest.fail("no reports event after a report was written")
