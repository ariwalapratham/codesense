from api.routes.health import _qdrant_check


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
