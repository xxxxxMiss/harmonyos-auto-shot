"""阶段三：auto_scene 自动场景生成与 batch 批量执行器测试。"""
import os

import pytest

from autoshot.auto_scene import (bfs_path, bundle_name, entry_page, generate,
                                 navigation_steps_to_page, page_groups_for_batch,
                                 trigger_and_reset)
from autoshot.resource_index import ResourceIndex
from autoshot.scanner import ast_backend_available, scan_project

pytestmark = pytest.mark.skipif(
    not ast_backend_available(), reason="需要 node + npm install typescript")

TESTAPP = os.path.join(os.path.dirname(__file__), "..", "TestApp")


@pytest.fixture(scope="module")
def scan():
    return scan_project(TESTAPP, backend="ast")


@pytest.fixture(scope="module")
def index():
    return ResourceIndex.from_project(TESTAPP)


def test_bundle_name_detected():
    assert bundle_name(TESTAPP) == "com.example.hualitd"


def test_entry_page(scan):
    assert entry_page(scan) == "pages/Index"


def test_bfs_direct_and_transitive(scan):
    # Index -> SettingsPage 一步
    assert [e["to"] for e in bfs_path(scan.page_adj, "pages/Index", "pages/SettingsPage")] == ["pages/SettingsPage"]
    # 入口到自身为空
    assert bfs_path(scan.page_adj, "pages/Index", "pages/Index") == []


def test_generate_navigation_and_triggers(scan, index):
    data = generate(scan, index, TESTAPP)
    scenes = {s["name"]: s for s in data["scenes"]}
    # 导航路径推导
    assert scenes["SettingsPage"]["steps"] == [
        {"launch": "com.example.hualitd"}, {"click": "text=打开设置页"}]
    # 弹窗触发
    dialog = scenes["SettingsPage__trigger:text=显示弹窗"]
    assert dialog["steps"][-1] == {"click": "text=显示弹窗"}
    assert dialog["keys"] == ["dialog_message"]
    # 条件渲染：断网开关
    net = scenes["StatePage__trigger:text=模拟断网"]
    assert net["steps"][-1] == {"click": "text=模拟断网"}
    assert net["keys"] == ["network_error"]
    # settings_about 应回到静态组（visible 优先）
    assert "settings_about" in scenes["SettingsPage"]["keys"]


def test_trigger_and_reset_click(scan, index):
    usage = next(u for u in scan.usages if u.key == "dialog_message")
    trigger, reset = trigger_and_reset(usage, index)
    assert trigger == {"click": "text=显示弹窗"}
    assert reset == {"back": None}


def test_trigger_and_reset_conditional_toggle(scan, index):
    usage = next(u for u in scan.usages if u.key == "network_error")
    trigger, reset = trigger_and_reset(usage, index)
    assert trigger == {"click": "text=模拟断网"}
    assert reset == {"click": "text=模拟断网"}


def test_trigger_none_for_list_render(scan, index):
    # list_target 的条件是 ForEach 索引（idx===20），非状态开关 → 无触发，靠滚动
    usage = next(u for u in scan.usages if u.key == "list_target")
    trigger, reset = trigger_and_reset(usage, index)
    assert trigger is None and reset is None


def test_page_groups(scan, index):
    groups = page_groups_for_batch(scan, index)
    keys_in = {p: {i["key"] for i in items} for p, items in groups.items()}
    assert "main_title" in keys_in["pages/Index"]
    assert "dialog_message" in keys_in["pages/SettingsPage"]
    assert "network_error" in keys_in["pages/StatePage"]
    # 弹窗 key 应带触发步骤
    dmsg = next(i for i in groups["pages/SettingsPage"] if i["key"] == "dialog_message")
    assert dmsg["trigger"] == {"click": "text=显示弹窗"}


def test_navigation_steps(scan, index):
    steps = navigation_steps_to_page(scan, index, "pages/StatePage", "com.example.hualitd")
    assert steps == [{"launch": "com.example.hualitd"}, {"click": "text=状态演示页"}]
