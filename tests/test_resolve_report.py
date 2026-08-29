"""capture 命令的输入解析（key/文案/OCR 行）与总结报告渲染测试。"""
import os

import pytest

from autoshot.report import (CaptureResult, Report, ResolvedInput,
                             UnresolvedInput)
from autoshot.resolve import resolve_inputs, resolve_one
from autoshot.resource_index import ResourceIndex

TESTAPP = os.path.join(os.path.dirname(__file__), "..", "TestApp")


@pytest.fixture
def index():
    return ResourceIndex.from_project(TESTAPP)


# ---------- 输入解析 ----------

def test_resolve_direct_key(index):
    r, u = resolve_one(index, "main_title")
    assert r is not None and r.key == "main_title" and r.source == "key"
    assert u is None


def test_resolve_text_exact(index):
    r, u = resolve_one(index, "设置")
    assert r is not None and r.key == "settings_title" and r.source == "text"
    assert u is None


def test_resolve_text_no_match(index):
    r, u = resolve_one(index, "22:46")
    assert r is None
    assert u is not None and u.reason == "无匹配资源"


def test_resolve_text_fuzzy_ambiguous(index):
    # "网络连接不可用" 是 network_error 的子串 → 模糊命中 → 歧义
    r, u = resolve_one(index, "网络连接不可用")
    assert r is None
    assert u is not None and "network_error" in u.candidates


def test_resolve_multiple_names_dedup(index):
    resolved, unresolved = resolve_inputs(index, names=["main_title", "main_title", "设置"])
    keys = [r.key for r in resolved]
    assert keys == ["main_title", "settings_title"]   # 去重
    assert unresolved == []


def test_resolve_inputs_mixed(index):
    resolved, unresolved = resolve_inputs(index, names=["main_title", "不存在的文案"])
    assert [r.key for r in resolved] == ["main_title"]
    assert len(unresolved) == 1 and unresolved[0].input == "不存在的文案"


# ---------- 报告渲染 ----------

def _report():
    rep = Report(project_root="/p", output_dir="shots", locale="zh_CN")
    rep.resolved = [ResolvedInput(input="main_title", key="main_title", source="key",
                                  candidates=["首页"]),
                    ResolvedInput(input="设置", key="settings_title", source="text",
                                  candidates=["设置"])]
    rep.unresolved = [UnresolvedInput(input="22:46", reason="无匹配资源")]
    rep.results = [
        CaptureResult(input="main_title", key="main_title", ok=True, path="shots/main_title.png", duration=3.1),
        CaptureResult(input="设置", key="settings_title", ok=False, error="无可用场景", duration=0.0),
    ]
    return rep


def test_report_summary():
    s = _report().summary()
    assert s == {"total": 2, "succeeded": 1, "failed": 1, "unresolved": 1, "input_count": 3}


def test_report_markdown_contains_sections():
    md = _report().to_markdown()
    assert "## 一、待处理项" in md
    assert "## 二、处理结果" in md
    assert "## 三、汇总" in md
    assert "✅ 成功" in md and "❌ 失败" in md
    assert "main_title.png" in md and "无可用场景" in md
    assert "22:46" in md


def test_report_json_roundtrip(tmp_path):
    rep = _report()
    p = rep.write_json(str(tmp_path / "r.json"))
    import json
    d = json.load(open(p, encoding="utf-8"))
    assert d["summary"]["succeeded"] == 1
    assert d["results"][0]["path"].endswith("main_title.png")
    assert d["unresolved"][0]["reason"] == "无匹配资源"
