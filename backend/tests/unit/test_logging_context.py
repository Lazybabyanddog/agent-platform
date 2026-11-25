"""日志上下文：认证结果与运行标识自动进入每条日志，退出后不残留。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import structlog

from agent_platform.settings import Settings
from agent_platform.shared.context import ActorContext, set_actor
from agent_platform.shared.logging import configure_logging, get_logger, reset_logging


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_logging()
    configure_logging(Settings(log_level="INFO", log_json=True), service="api")
    yield
    reset_logging()


def _records(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def _actor(tenant_id: uuid.UUID) -> ActorContext:
    return ActorContext(
        tenant_id=tenant_id,
        actor_type="USER",
        actor_id=uuid.uuid4(),
        display_name="tester",
    )


def test_tenant_id_is_bound_with_actor(capsys: pytest.CaptureFixture[str]) -> None:
    tenant_id = uuid.uuid4()

    set_actor(_actor(tenant_id))
    get_logger("ctx").info("invocation.start", agent_id="a")
    set_actor(None)
    get_logger("ctx").info("invocation.start", agent_id="a")

    first, second = _records(capsys.readouterr().out)
    assert first["tenant_id"] == str(tenant_id)
    assert second["tenant_id"] is None


def test_run_id_is_scoped_to_invocation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = uuid.uuid4()

    with structlog.contextvars.bound_contextvars(run_id=str(run_id)):
        get_logger("ctx").info("llm.call", model_name="m")
    get_logger("ctx").info("llm.call", model_name="m")

    inside, outside = _records(capsys.readouterr().out)
    assert inside["run_id"] == str(run_id)
    assert outside["run_id"] is None


async def test_invocation_binds_and_clears_run_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_platform.contexts.execution.application import invocation_service as module

    run_id = uuid.uuid4()
    captured: list[dict[str, Any]] = []

    async def fake_create_run(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        return run_id

    async def fake_finish(*_args: Any, **_kwargs: Any) -> None:
        return None

    class FakeModel:
        async def chat(self, _request: Any) -> Any:
            raise AssertionError("模型调用不应走到这里")

    class FakeCheckpoint:
        async def saver(self) -> None:
            return None

    async def fake_model_factory(*_args: Any) -> Any:
        return FakeModel()

    async def fake_execute_snapshot(**_kwargs: Any) -> Any:
        get_logger("ctx").info("runtime.node", node="llm")
        raise RuntimeError("boom")

    service = module.InvocationService(
        _model_factory=fake_model_factory,
        _tool_factory=lambda _actor: None,
        _recorder_factory=lambda _actor: None,
        _checkpoint_provider=FakeCheckpoint(),
    )
    monkeypatch.setattr(module.InvocationService, "_create_run", fake_create_run)
    monkeypatch.setattr(module.InvocationService, "_finish_failed", fake_finish)
    monkeypatch.setattr(module, "execute_snapshot", fake_execute_snapshot)

    with pytest.raises(RuntimeError):
        await service.execute_snapshot(
            actor=_actor(uuid.uuid4()),
            agent_id=uuid.uuid4(),
            env="DEV",  # type: ignore[arg-type]
            snapshot={
                "prompt": {"messages": [], "variables": []},
                "policy": {"timeout_s": 5, "history_token_limit": 100},
                "model": {"model_id": str(uuid.uuid4()), "model_name": "fake"},
                "tools": [],
            },
            values={},
            timeout_s=5,
            kind="SYNC",  # type: ignore[arg-type]
            skip_reference_check=True,
        )

    captured = _records(capsys.readouterr().out)
    assert [item["event"] for item in captured][:2] == ["invocation.start", "runtime.node"]
    assert all(item["run_id"] == str(run_id) for item in captured)
    assert structlog.contextvars.get_contextvars().get("run_id") is None
