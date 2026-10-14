from __future__ import annotations

import asyncio
import json
import logging

from picturehouse.log import LOG_FILE_NAME, current_context, log_context, new_job_id, setup_logging


def _lines(log_dir):
    for h in logging.getLogger().handlers:
        h.flush()
    text = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_json_file_has_context_and_extras(tmp_path):
    setup_logging(tmp_path, console=False)
    log = logging.getLogger("picturehouse.test")
    with log_context(job_id="scan-1234", library="Films"):
        log.info("probing %s", "東京物語.mkv", extra={"file": "a/b.mkv"})
    log.warning("outside")

    first, second = _lines(tmp_path)
    assert first["msg"] == "probing 東京物語.mkv"
    assert first["job_id"] == "scan-1234"
    assert first["library"] == "Films"
    assert first["file"] == "a/b.mkv"
    assert first["level"] == "INFO" and first["logger"] == "picturehouse.test"
    assert "job_id" not in second


def test_exceptions_are_captured(tmp_path):
    setup_logging(tmp_path, console=False)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("picturehouse.test").exception("failed")
    (entry,) = _lines(tmp_path)
    assert "RuntimeError: boom" in entry["exc"]


def test_file_level_filters(tmp_path):
    setup_logging(tmp_path, level="WARNING", console=False)
    logging.getLogger("picturehouse.test").info("hidden")
    logging.getLogger("picturehouse.test").warning("shown")
    assert [e["msg"] for e in _lines(tmp_path)] == ["shown"]


def test_setup_is_idempotent(tmp_path):
    setup_logging(tmp_path, console=False)
    setup_logging(tmp_path, console=False)
    logging.getLogger("picturehouse.test").info("once")
    assert len(_lines(tmp_path)) == 1


def test_rotation(tmp_path):
    setup_logging(tmp_path, console=False, max_bytes=10_000, backup_count=2)
    log = logging.getLogger("picturehouse.test")
    for i in range(400):
        log.info("line %d %s", i, "x" * 80)
    assert (tmp_path / f"{LOG_FILE_NAME}.1").exists()
    assert not (tmp_path / f"{LOG_FILE_NAME}.3").exists()


def test_context_is_per_task():
    async def worker(name):
        with log_context(job_id=name):
            await asyncio.sleep(0.01)
            return current_context()["job_id"]

    async def main():
        return await asyncio.gather(worker("a"), worker("b"))

    assert asyncio.run(main()) == ["a", "b"]
    assert current_context() == {}


def test_job_ids_are_unique():
    ids = {new_job_id("scan") for _ in range(100)}
    assert len(ids) == 100 and all(i.startswith("scan-") for i in ids)


def test_api_keys_never_reach_the_log(tmp_path):
    """Regression: httpx logged TMDb request URLs (with ?api_key=) at INFO."""
    import httpx

    setup_logging(tmp_path, console=False)
    secret = "abcdef0123456789secret"

    def handler(request):
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get(f"https://api.themoviedb.org/3/search/movie?api_key={secret}&query=x")
    log = logging.getLogger("picturehouse.test")
    log.warning("calling https://www.omdbapi.com/?apikey=%s&s=x", secret)
    log.info("header Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.%s", secret)
    text = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert secret not in text
    assert "apikey=REDACTED" in text and "Bearer REDACTED" in text
