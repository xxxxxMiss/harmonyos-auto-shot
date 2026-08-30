"""同路由不同参数场景的测试：TestApp 长列表项都跳 DetailPage，按 index 渲染不同文案。

验证当前架构的两个事实（真机已实测确认）：
1. 静态分析能正确分类 detail_0~4 为 conditional（依赖 this.index），但导航边
   ListPage→DetailPage 的触发标签为 null（列表项文本是三元表达式），
   且条件依赖路由参数 index（非页面内 toggle 开关）→ 自动场景无法推导正确步骤。
2. 因此这类 key 必须靠手写 scenes.yaml 兜底（点对应列表项跳转携带参数）。

本文件只测静态分析层（离线、确定性），真机结论见 README「已知边界」。
"""
import os

import pytest

from autoshot.auto_scene import (generate, navigation_steps_to_page,
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


# ---- 静态分类：detail_0~4 依赖路由参数 index ----

def test_detail_conditionals_classified(scan):
    for i in range(5):
        usages = [u for u in scan.usages if u.key == f"detail_{i}"]
        assert usages, f"detail_{i} 应有用法"
        u = usages[0]
        assert u.trigger_hint == "conditional"
        assert f"this.index === {i}" in u.condition_text


def test_detail_default_conditional(scan):
    usages = [u for u in scan.usages if u.key == "detail_default"]
    assert usages and usages[0].trigger_hint == "conditional"


# ---- 关键盲区：导航边无触发标签 + 无 toggle 开关 ----

def test_detail_nav_edge_has_no_via(scan):
    """ListPage→DetailPage 的边：列表项文本是三元表达式，提取不到触发标签。"""
    edge = next(e for e in scan.edges if e["to"] == "pages/DetailPage")
    assert edge["api"] == "pushUrl"
    assert edge["viaKey"] is None and edge["viaText"] is None


def test_detail_no_toggle_trigger(scan):
    """条件依赖 this.index（路由参数），非页面内 toggle 开关 → 无法生成触发步骤。"""
    u = next(u for u in scan.usages if u.key == "detail_2")
    trigger, reset = trigger_and_reset(u, index)
    assert trigger is None, "路由参数条件不应生成 toggle 点击步骤"
    assert reset is None


# ---- 自动场景推导对这类 key 无法给出可用的带参跳转 ----

def test_auto_scene_cannot_navigate_detail(scan, index):
    """自动推导的 DetailPage 场景步骤只到 ListPage，缺"点列表项跳转"步骤。"""
    data = generate(scan, index, TESTAPP)
    scene = next(s for s in data["scenes"] if s["name"] == "DetailPage")
    # 步骤应止于"查看长列表"，没有"点击列表项"也没有参数注入
    steps_text = [str(s) for s in scene["steps"]]
    assert any("查看长列表" in t for t in steps_text)
    assert not any("列表第" in t for t in steps_text), \
        "自动推导不应能点列表项（缺触发标签）"


def test_detail_page_depth(scan):
    """DetailPage 深度为 2（Index→ListPage→DetailPage），导航图本身仍正确。"""
    page = next(p for p in scan.pages if p["name"] == "pages/DetailPage")
    assert page["depth"] == 2


# ---- 手写场景兜底：scenes.yaml 里应含 detail_* 场景（保证真机可跑）----

def test_handwritten_scenes_cover_detail(scan, index):
    """手写 scenes.yaml 兜底了 detail_0/2/default 的带参跳转。"""
    from autoshot.scene import SceneRegistry
    scenes_path = os.path.join(TESTAPP, "scenes.yaml")
    reg = SceneRegistry.load(scenes_path)
    for key in ["detail_0", "detail_2", "detail_default"]:
        scene = reg.find_for_key(key)
        assert scene is not None, f"scenes.yaml 应覆盖 {key}"
        steps_text = [str(s) for s in scene.steps]
        # 场景应包含点击列表项（带参跳转的关键步骤）
        assert any("列表第" in t for t in steps_text), f"{key} 场景缺点击列表项步骤"
