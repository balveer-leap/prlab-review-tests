from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from typing import Any


class GitHubError(RuntimeError):
    pass


# Network failures worth retrying: the command never reached a decision on GitHub.
TRANSIENT = re.compile(
    r"connection reset|recv failure|timed out|timeout|temporary failure|could not resolve host"
    r"|unable to access|tls|eof|http 50[0234]|502 bad gateway|503 service|unexpected disconnect",
    re.I,
)


# A push or API call on a dropped connection can hang for good without this.
COMMAND_TIMEOUT = 180
# git aborts an HTTP transfer slower than 1 KB/s for 30 s instead of waiting on it.
STALL_ABORT = {"GIT_HTTP_LOW_SPEED_LIMIT": "1000", "GIT_HTTP_LOW_SPEED_TIME": "30"}


def run(args: list[str], cwd: str | None = None, attempts: int = 4) -> str:
    """Run a gh/git command; retry a transient network failure or a hang, with backoff."""
    for attempt in range(1, attempts + 1):
        # Own process group: git push hands the network to a git-remote-https child
        # that keeps the pipes open, so killing only git would still hang here.
        proc = subprocess.Popen(
            args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env={**os.environ, **STALL_ABORT},
        )
        try:
            stdout, stderr = proc.communicate(timeout=COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            if attempt == attempts:
                raise GitHubError(f"{' '.join(args)} timed out after {COMMAND_TIMEOUT}s") from None
            time.sleep(5 * attempt)
            continue
        if proc.returncode == 0:
            return stdout
        detail = (stderr or stdout or "").strip()
        if attempt == attempts or not TRANSIENT.search(detail):
            raise GitHubError(f"{' '.join(args)} failed: {detail}")
        time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def gh_json(args: list[str]) -> Any:
    return json.loads(run(["gh", *args]) or "null")


def parse_json_pages(raw: str) -> Any:
    """Parse one JSON value, or concatenated arrays from `gh api --paginate`."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        items: list[Any] = []
        idx = 0
        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break
            obj, end = decoder.raw_decode(text, idx)
            if isinstance(obj, list):
                items.extend(obj)
            else:
                items.append(obj)
            idx = end
        return items


def gh_api_list(path: str) -> list[Any]:
    raw = run(["gh", "api", "--paginate", path]) or "[]"
    data = parse_json_pages(raw)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]
