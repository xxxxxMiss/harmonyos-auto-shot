"""阶段二 AST 后端金标测试（需 node + typescript，缺失时跳过）。"""
import os

import pytest

from autoshot.resource_index import ResourceIndex
from autoshot.scanner import (ast_backend_available, build_usage_index,
                              scan_project, scan_report)

pytestmark = pytest.mark.skipif(
    not ast_backend_available(), reason="需要 node + npm install typescript")

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_app")
TESTAPP = os.path.join(os.path.dirname(__file__), "..", "TestApp")


@pytest.fixture
def scan():
    return scan_project(FIXTURE, backend="ast")


@pytest.fixture
def index():
    return ResourceIndex.from_project(FIXTURE)


# ---------- R5：i18n 封装调用点（regex MVP 覆盖不了的核心增量） ----------

def test_r5_wrapper_call_site(scan):
    hits = [u for u in scan.usages if u.key == "settings_title" and u.rule == "R5_wrapper"]
    assert hits, "应通过函数摘要解析 I18n.t('settings_title') 调用点"
    assert any(u.file.endswith("Index.ets") for u in hits)


def test_r5_with_const_propagation(scan):
    """I18n.t(DYNAMIC_KEY)：封装 + 变量 → 常量传播解析出 unused_string。"""
    hits = [u for u in scan.usages if u.key == "unused_string"]
    assert hits and hits[0].rule == "R5_wrapper"


def test_accessor_detected(scan):
    assert any(a["name"] == "t" and a["className"] == "I18n" for a in scan.accessors)


def test_no_dynamic_noise_from_wrapper_body(scan):
    """访问器内部的参数转发不应计入动态引用（调用点已由 R5 解析）。"""
    assert scan.dynamic_refs == []


# ---------- 上下文分类 ----------

def test_conditional_with_condition_source(scan):
    u = next(u for u in scan.usages if u.key == "network_error")
    assert u.trigger_hint == "conditional"
    assert "networkOk" in u.condition_text


def test_click_for_dialog(scan):
    u = next(u for u in scan.usages if u.key == "dialog_marker" or u.key == "login_button")
    # login_button 的 toast 调用点应为 click
    assert any(uu.trigger_hint == "click" for uu in scan.usages if uu.key == "login_button")


def test_struct_preprocess_keeps_names(scan):
    u = next(u for u in scan.usages if u.key == "network_error")
    assert u.struct_name == "SettingsPage", "struct→class 预处理不得破坏类名"


# ---------- 对账（相对 regex 的增量） ----------

def test_report_ast_reconciliation(index, scan):
    report = scan_report(index, scan)
    assert report["backend"] == "ast"
    # AST 下 unused_string 已由 R5 解析，零引用仅剩 app_name（module.json5 引用不扫描）
    assert report["unreferenced_key_list"] == ["app_name"]
    assert report["keys_not_in_resources"] == []
    assert report["dynamic_refs"] == 0
    assert report["i18n_accessors"] == 1


def test_usage_index_payload_has_pages_and_triggers(index, scan):
    payload = build_usage_index(index, scan, FIXTURE)
    entry = payload["keys"]["network_error"]
    assert entry["usages"][0]["condition"] is not None
    assert entry["usages"][0]["triggers"]


# ---------- 导航图（TestApp 是完整工程） ----------

def _testapp_scan():
    scan = scan_project(TESTAPP, backend="ast")
    pages = {p["name"]: p["depth"] for p in scan.pages}
    assert pages == {"pages/Index": 0, "pages/SettingsPage": 1, "pages/ListPage": 1,
                     "pages/StatePage": 1, "pages/DetailPage": 2, "pages/NavEntryPage": 1,
                     "NavDetailPage": 2, "NavRouteAPage": 2, "NavRouteBPage": 2}
    return scan


def test_testapp_key_pages():
    scan = _testapp_scan()
    assert scan.key_pages["main_title"] == [{"page": "pages/Index", "depth": 0}]
    assert scan.key_pages["list_target"][0]["page"] == "pages/ListPage"
    assert scan.key_pages["network_error"][0]["page"] == "pages/StatePage"


def test_testapp_push_url_edges():
    scan = _testapp_scan()
    targets = {e["to"] for e in scan.edges if e["api"] == "pushUrl"}
    assert targets == {"pages/SettingsPage", "pages/ListPage", "pages/StatePage",
                       "pages/DetailPage", "pages/NavEntryPage"}


def test_testapp_conditional_and_click():
    scan = scan_project(TESTAPP, backend="ast")
    net = next(u for u in scan.usages if u.key == "network_error")
    assert net.trigger_hint == "conditional" and net.condition_text == "this.offline"
    toast = next(u for u in scan.usages if u.key == "toast_message")
    assert toast.trigger_hint == "click" and "runtime" in toast.triggers
