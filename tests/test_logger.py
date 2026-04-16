import json
import logging
from observability.logger import get_logger, JSONFormatter

def test_json_formatter_produces_valid_json():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello world", args=(), exc_info=None
    )
    output = formatter.format(record)
    parsed = json.loads(output)  # must not raise
    assert parsed["level"] == "INFO"
    assert parsed["msg"] == "hello world"
    assert parsed["logger"] == "test"
    assert "ts" in parsed

def test_get_logger_returns_logger():
    logger = get_logger("test_module")
    assert isinstance(logger, logging.Logger)
    assert logger.level == logging.INFO

def test_get_logger_no_duplicate_handlers():
    # Calling get_logger twice must not add duplicate handlers
    logger1 = get_logger("same_module")
    handler_count_1 = len(logger1.handlers)
    logger2 = get_logger("same_module")
    assert len(logger2.handlers) == handler_count_1
