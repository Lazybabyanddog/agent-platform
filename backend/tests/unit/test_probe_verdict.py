"""探测结论六态：取值稳定、与既有字符串常量一一对应。"""

from __future__ import annotations

import json

from agent_platform.contexts.catalog.application.model_service import (
    PROBE_AUTH_FAILED,
    PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED,
    PROBE_HTTP_ERROR,
    PROBE_MODEL_NOT_FOUND,
    PROBE_OK,
    PROBE_TIMEOUT,
    PROBE_UNREACHABLE,
)
from agent_platform.contexts.catalog.domain.enums import ProbeVerdict

EXPECTED = {
    "OK",
    "MODEL_NOT_FOUND",
    "CREDENTIAL_OK_MODEL_UNVERIFIED",
    "MODEL_PROBE_AUTH_FAILED",
    "MODEL_PROBE_TIMEOUT",
    "MODEL_PROBE_UNREACHABLE",
}


def test_six_verdicts_are_stable_strings() -> None:
    assert {member.value for member in ProbeVerdict} == EXPECTED
    assert len(EXPECTED) == 6


def test_legacy_constants_map_to_verdicts() -> None:
    assert PROBE_OK == ProbeVerdict.OK.value
    assert PROBE_MODEL_NOT_FOUND == ProbeVerdict.MODEL_NOT_FOUND.value
    assert (
        PROBE_CREDENTIAL_OK_MODEL_UNVERIFIED
        == ProbeVerdict.CREDENTIAL_OK_MODEL_UNVERIFIED.value
    )
    assert PROBE_AUTH_FAILED == ProbeVerdict.MODEL_PROBE_AUTH_FAILED.value
    assert PROBE_TIMEOUT == ProbeVerdict.MODEL_PROBE_TIMEOUT.value
    assert PROBE_UNREACHABLE == ProbeVerdict.MODEL_PROBE_UNREACHABLE.value
    # 其它 HTTP 错误归入"无法连通"，不再单独占一态
    assert PROBE_HTTP_ERROR == ProbeVerdict.MODEL_PROBE_UNREACHABLE.value


def test_verdict_serializes_to_plain_string() -> None:
    payload = {"verdict": ProbeVerdict.MODEL_NOT_FOUND}
    assert json.loads(json.dumps(payload)) == {"verdict": "MODEL_NOT_FOUND"}
    assert str(ProbeVerdict.MODEL_NOT_FOUND) == "MODEL_NOT_FOUND"
