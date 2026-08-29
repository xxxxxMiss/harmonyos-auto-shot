import os

import pytest

from autoshot.resource_index import ResourceIndex
from autoshot.scanner import build_usage_index, scan_project, scan_report

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_app")


@pytest.fixture
def scan():
    # 显式 regex 后端：本文件固化 MVP 行为；ast 后端见 test_scanner_ast.py
    return scan_project(FIXTURE, backend="regex")


@pytest.fixture
def index():
    return ResourceIndex.from_project(FIXTURE)


def test_r1_dollar_r(scan):
    keys = {u.key for u in scan.usages if u.rule == "R1_$r"}
    assert {"main_title", "login_button", "network_error", "settings_about", "settings_title"} <= keys


def test_r2_id_form(scan):
    hits = [u for u in scan.usages if u.rule == "R2_id"]
    assert any(u.key == "remaining_count" for u in hits)


def test_r3_byname_literal(scan):
    # fixtures 中 I18n.t 内部是变量传参，不应有 R3 字面量命中
    assert not [u for u in scan.usages if u.rule == "R3_byname"]


def test_dynamic_ref_recorded(scan):
    assert any(d.expr == "name" for d in scan.dynamic_refs)


def test_dialog_trigger_hint(scan):
    login = [u for u in scan.usages if u.key == "login_button"]
    assert any(u.trigger_hint == "click" for u in login)


def test_report_reconciliation(index, scan):
    report = scan_report(index, scan)
    assert report["total_resource_keys"] == 8
    # app_name 只在 module.json5 引用（不扫描），unused_string 只有动态引用 → 都算零引用
    assert set(report["unreferenced_key_list"]) == {"app_name", "unused_string"}
    assert report["keys_not_in_resources"] == []
    assert report["dynamic_refs"] == 1


def test_usage_index_payload(index, scan):
    payload = build_usage_index(index, scan, FIXTURE)
    entry = payload["keys"]["remaining_count"]
    assert entry["values"]["zh_CN"] == "还剩%d个"
    assert entry["modules"] == ["entry"]
    assert entry["usages"]
