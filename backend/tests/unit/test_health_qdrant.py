import pytest

from api.routes import health as health_mod
from api.routes.health import _qdrant_check
from core.errors import AppError


class _Ok:
    async def ping(self) -> None:
        return None


class _Boom:
    async def ping(self) -> None:
        raise RuntimeError("down")


async def test_qdrant_check_ok():
    result = await _qdrant_check(_Ok())
    assert result["status"] == "ok"
    assert "latency_ms" in result


async def test_qdrant_check_error():
    result = await _qdrant_check(_Boom())
    assert result["status"] == "error"
    assert "latency_ms" in result


async def test_configured_qdrant_down_is_503(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "qdrant_url", "http://localhost:6333")

    async def _ok_ping():
        return None

    monkeypatch.setattr(health_mod.mongodb, "ping", _ok_ping)
    with pytest.raises(AppError) as exc:
        await health_mod.health_deep(vector_store=_Boom())
    assert exc.value.status_code == 503
    assert exc.value.code == "upstream_error"
    assert exc.value.details["checks"]["qdrant"]["status"] == "error"
