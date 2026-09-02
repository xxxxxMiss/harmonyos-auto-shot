"""LLM 决策策略（P1）测试：纯逻辑，不发真实网络请求。"""
import pytest

from autoshot.config import Config
from autoshot.explorer import Action, HeuristicPolicy, Snapshot, _snapshot
from autoshot.fake import AppDriver
from autoshot.layout import parse_tree, Viewport
from autoshot.llm import LLMClient, LLMPolicy, _extract_json


def test_extract_json_from_fence_and_noise():
    assert _extract_json('```json\n{"action":"click","node_id":1}\n```') == \
        {"action": "click", "node_id": 1}
    assert _extract_json('好的，下一步：{"action":"scroll","direction":"up"}') == \
        {"action": "scroll", "direction": "up"}
    assert _extract_json("不是 JSON") == {}
    assert _extract_json("") == {}


def test_llm_policy_disabled_without_api_key():
    cfg = Config(project_root=".")
    driver = AppDriver()
    p = LLMPolicy(cfg, driver)
    assert not p.client.available
    assert p.decide(None, ["x"]) is None


def test_llm_policy_click_by_node_id():
    cfg = Config(project_root=".")
    driver = AppDriver()
    driver.add_page("home", [{"text": "设置", "clickable": True, "target": "s"},
                             {"text": "关于"}])
    p = LLMPolicy(cfg, driver)
    p.client.api_key = "sk-test"   # 模拟已配置 api_key
    p.client.decide_action = lambda prompt, shot: {"action": "click", "node_id": 0}
    driver.screenshot = lambda: b"\xff\xd8" + b"\x00" * 10

    root = parse_tree(driver.dump_layout())
    snap = _snapshot(root, Viewport(1080, 2340), "sig")
    a = p.decide(snap, ["关于本应用"])

    assert a is not None and a.kind == "click"
    assert a.node is snap.nodes[0]


def test_llm_policy_click_by_text_fallback():
    cfg = Config(project_root=".")
    driver = AppDriver()
    driver.add_page("home", [{"text": "更多设置", "clickable": True, "target": "s"}])
    p = LLMPolicy(cfg, driver)
    p.client.api_key = "sk-test"
    p.client.decide_action = lambda prompt, shot: {"action": "click", "text": "更多设置"}
    driver.screenshot = lambda: b"\xff\xd8" + b"\x00" * 10

    root = parse_tree(driver.dump_layout())
    snap = _snapshot(root, Viewport(1080, 2340), "sig")
    a = p.decide(snap, ["关于"])

    assert a is not None and a.kind == "click"
    assert a.node.text == "更多设置"


def test_llm_policy_respects_call_budget():
    cfg = Config(project_root=".")
    driver = AppDriver()
    driver.add_page("home", [{"text": "设置", "clickable": True}])
    p = LLMPolicy(cfg, driver)
    p.client.api_key = "sk-test"
    p._max_calls = 2
    p.client.decide_action = lambda prompt, shot: {"action": "scroll", "direction": "down"}
    driver.screenshot = lambda: b"\xff\xd8" + b"\x00" * 10
    root = parse_tree(driver.dump_layout())
    snap = _snapshot(root, Viewport(1080, 2340), "sig")

    assert p.decide(snap, ["x"]) is not None   # 调用 1
    assert p.decide(snap, ["x"]) is not None   # 调用 2
    assert p.decide(snap, ["x"]) is None       # 超预算


def test_llm_policy_swallows_client_error():
    cfg = Config(project_root=".")
    driver = AppDriver()
    driver.add_page("home", [{"text": "设置", "clickable": True}])
    p = LLMPolicy(cfg, driver)
    p.client.api_key = "sk-test"

    def boom(prompt, shot):
        raise RuntimeError("network down")

    p.client.decide_action = boom
    driver.screenshot = lambda: b"\xff\xd8" + b"\x00" * 10
    root = parse_tree(driver.dump_layout())
    snap = _snapshot(root, Viewport(1080, 2340), "sig")

    assert p.decide(snap, ["x"]) is None   # 异常被吞掉，返回 None 降级
