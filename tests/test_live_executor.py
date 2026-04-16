import pytest
from unittest.mock import patch


def test_live_executor_raises_without_flag():
    """LiveExecutor must refuse to instantiate when LIVE_TRADING=False."""
    # We patch settings at the module level where live_executor reads it
    with patch("executor.live_executor.settings") as mock_settings:
        mock_settings.live_trading = False
        # Force reimport so the patched settings is used
        import importlib
        import executor.live_executor as mod
        importlib.reload(mod)
        with pytest.raises(RuntimeError, match="LIVE_TRADING is not True"):
            mod.LiveExecutor()


def test_live_executor_not_importable_by_default():
    """Importing live_executor should not auto-instantiate or crash."""
    import executor.live_executor  # must not raise on import
    # Only raises when you try to instantiate
    with pytest.raises(RuntimeError):
        executor.live_executor.LiveExecutor()
