"""动作注册表单一化测试(spec #8 遗留:分类/capture/apply/前端标签一处维护)。

断言注册表是全系统唯一动作事实源:classify_action 与其一致、风险与处理函数配套、
/api/actions 元数据端点供前端渲染标签(不再硬编码)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from python_backend.agents.registry import REGISTRY
from python_backend.api.app import create_app
from python_backend.core.approvals import classify_action


def test_classify_action_consistent_with_registry() -> None:
    """分类函数读注册表:注册风险与分类结果一一对应;未知动作 forbidden。"""
    for spec in REGISTRY.metadata():
        assert classify_action(spec["action"]) == spec["risk"]
    assert classify_action("product.nuke") == "forbidden"


def test_registry_handlers_match_risk() -> None:
    """风险与处理函数配套:auto 有 execute;approval 有 capture+apply。"""
    for action in REGISTRY.metadata():
        spec = REGISTRY.get(action["action"])
        assert spec is not None
        if spec.risk == "auto":
            assert spec.execute is not None, f"{spec.action} 免审动作缺 execute"
            assert spec.capture is None and spec.apply is None, f"{spec.action} 免审动作不应有 capture/apply"
        else:
            assert spec.capture is not None and spec.apply is not None, f"{spec.action} 审批动作缺 capture/apply"
            assert spec.execute is None, f"{spec.action} 审批动作不应有 execute"


def test_actions_endpoint_serves_metadata() -> None:
    """GET /api/actions 返回全部动作元数据(action/risk/label),与注册表一致。"""
    client = TestClient(create_app(auth_required=False))
    response = client.get("/api/actions")
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert actions == REGISTRY.metadata()
    assert all(set(item) == {"action", "risk", "label"} for item in actions)
    assert all(item["label"] for item in actions)  # 标签非空:前端渲染不落空
