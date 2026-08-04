import builtins
import importlib
import sys


def test_import_does_not_warn_when_anthropic_is_unavailable(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    sys.modules.pop("engine.ai_fallback", None)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("cannot import name 'omit' from anthropic._types")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    module = importlib.import_module("engine.ai_fallback")

    assert getattr(module, "_CLAUDE_AVAILABLE", False) is False
    assert "Anthropic SDK init failed" not in caplog.text
