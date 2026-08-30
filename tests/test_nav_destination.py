"""NavDestination 方案（route_map.json）的路由分析测试。

覆盖用户要求的两种场景：
1. 不同列表项 → 同一个路由（NavDetailPage），根据路由参数 index 渲染不同内容
   —— 验证能自动推导"点哪个列表项带正确参数"（param_nav_step）。
2. 不同列表项 → 按指定条件（type==='A'/'B'）进入不同路由（NavRouteAPage/BPage）
   —— 验证条件路由枚举（routeCond + ForEach 数据源 + viaItems）。

两种场景均已被真机实测确认可全自动推导，无需手写 scenes.yaml。
"""
import os

import pytest

from autoshot.auto_scene import (bfs_path, generate, navigation_steps_to_page,
                                 param_nav_step)
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


# ---- discoverPages：route_map.json 发现 ----

def test_router_map_pages_discovered(scan):
    pages = {p["name"]: p.get("via") for p in scan.pages}
    assert pages["NavDetailPage"] == "routerMap"
    assert pages["NavRouteAPage"] == "routerMap"
    assert pages["NavRouteBPage"] == "routerMap"
    # 传统 pages 方案仍保留
    assert pages["pages/Index"] == "pages"


def test_router_map_page_depth(scan):
    page = next(p for p in scan.pages if p["name"] == "NavDetailPage")
    assert page["depth"] == 2   # Index -> NavEntryPage -> NavDetailPage


# ---- 导航边：pushPathByName 提取 ----

def test_push_path_by_name_param_literal(scan):
    edge = next(e for e in scan.edges if e["to"] == "NavDetailPage")
    assert edge["api"] == "pushPathByName"
    # as 断言被剥离，拿到对象字面量 {index: idx}
    assert edge["param"]["literal"]["index"] == "idx"
    # 列表项文案模板串 `详情项${idx}`
    assert edge["viaTemplate"] == "详情项"
    assert edge["viaVar"] == "idx"


def test_push_path_by_name_empty_param(scan):
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage")
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage")
    assert edge_a["param"]["literal"] == {}
    assert edge_b["param"]["literal"] == {}


# ---- 场景1：同路由不同参数 → 自动推导带参点击 ----

def test_usage_param_condition_extracted(scan):
    u = next(u for u in scan.usages if u.key == "nav_detail_2")
    assert u.trigger_hint == "conditional"
    assert u.param_var == "index"
    assert u.param_value == "2"


def test_param_nav_step_derives_click(scan, index):
    u = next(u for u in scan.usages if u.key == "nav_detail_2")
    entry = "pages/Index"
    path = bfs_path(scan.page_adj, entry, "NavDetailPage")
    step = param_nav_step(u, path, index)
    assert step == {"click": "text=详情项2"}


def test_auto_scene_generates_param_clicks(scan, index):
    data = generate(scan, index, TESTAPP)
    by_name = {s["name"]: s for s in data["scenes"]}
    # 每个参数值一个独立场景
    assert "NavDetailPage__param:text=详情项0" in by_name
    assert "NavDetailPage__param:text=详情项2" in by_name
    steps = by_name["NavDetailPage__param:text=详情项2"]["steps"]
    assert {"click": "text=详情项2"} in steps


# ---- 场景2：不同条件进不同路由 → 自动推导 ----

def test_different_route_via_items_enumeration(scan):
    """条件路由：if (type==='A') push A / else push B，结合 ForEach 数据源枚举出点击文案。"""
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage")
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage")
    # routeCond 保留文本摘要：A 是 then 分支，B 是 else 分支
    assert edge_a["routeCond"]["conditionText"] == "type === 'A'"
    assert edge_a["routeCond"]["inElse"] is False
    assert edge_b["routeCond"]["inElse"] is True
    # 枚举：A 路由点"路由项A0"，B 路由点"路由项B1"
    assert "路由项A0" in edge_a["viaItems"]
    assert "路由项B1" in edge_b["viaItems"]


def test_different_route_auto_scene_generates_click(scan, index):
    data = generate(scan, index, TESTAPP)
    by_name = {s["name"]: s for s in data["scenes"]}
    a_steps = by_name["NavRouteAPage"]["steps"]
    b_steps = by_name["NavRouteBPage"]["steps"]
    assert {"click": "text=路由项A0"} in a_steps
    assert {"click": "text=路由项B1"} in b_steps


def test_no_handwritten_needed_for_different_route(scan, index):
    """不同条件进不同路由已能自动推导，scenes.yaml 无需手写兜底。"""
    from autoshot.scene import SceneRegistry
    reg = SceneRegistry.load(os.path.join(TESTAPP, "scenes.yaml"))
    assert reg.find_for_key("nav_route_a") is None
    assert reg.find_for_key("nav_route_b") is None


# ---- 导航路径：NavDestination 路由可达性 ----

def test_navigation_to_nav_detail(scan, index):
    steps = navigation_steps_to_page(scan, index, "NavDetailPage", "com.example.hualitd")
    assert {"click": "text=导航入口"} in steps


# ---- 场景4：枚举常量路由判断（P2 增量，仅 fork 模式可推导）----

def test_enum_constant_route_condition(scan):
    """isVipLevel(item) 内部用 ItemLevel.Premium（=3）枚举，checker 符号路径求值。

    仅 fork 模式（含 checker）可推导；官方模式（AUTOSHOT_TS=official）降级为无 viaItems。
    """
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage" and e.get("viaTemplate") == "VIP项")
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage" and e.get("viaTemplate") == "VIP项")
    # ItemLevel.Premium = 3，item.level >= 3 → 只有 level=3 的 basic3 进 A
    assert edge_a["viaItems"] == ["VIP项basic3"]
    # 其余（level 0/1/2）进 B
    assert "VIP项premium0" in edge_b["viaItems"]
    assert "VIP项premium1" in edge_b["viaItems"]
    assert "VIP项basic2" in edge_b["viaItems"]


# ---- 场景5：字符串方法 + 算术取模（P0 增量）----

def test_string_method_and_modulo_route_condition(scan):
    """isEvenIndexed(item, idx) = idx % 2 === 0 && item.kind.startsWith('pre')。"""
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage" and e.get("viaTemplate") == "偶数项")
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage" and e.get("viaTemplate") == "偶数项")
    # idx 偶数且 kind 以 'pre' 开头：只有 idx=0（premium）满足
    assert edge_a["viaItems"] == ["偶数项premium0"]
    assert "偶数项premium1" in edge_b["viaItems"]   # idx=1 奇数
    assert "偶数项basic2" in edge_b["viaItems"]      # idx=2 偶数但 kind=basic
    assert "偶数项basic3" in edge_b["viaItems"]      # idx=3 奇数


# ---- 场景6：三元动态路由名（P3-b）----

def test_ternary_dynamic_route_name(scan):
    """pushPathByName(type === 'A' ? 'NavRouteAPage' : 'NavRouteBPage') 拆成两条边。"""
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage" and e.get("viaTemplate") == "三元项")
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage" and e.get("viaTemplate") == "三元项")
    # routeCond 保留三元条件，A 是 then 分支（inElse=false），B 是 else（inElse=true）
    assert edge_a["routeCond"]["conditionText"] == "type === 'A'"
    assert edge_a["routeCond"]["inElse"] is False
    assert edge_b["routeCond"]["inElse"] is True
    # 枚举：type==='A' 的项进 A，其余进 B
    assert edge_a["viaItems"] == ["三元项A0", "三元项A2"]
    assert edge_b["viaItems"] == ["三元项B1", "三元项B3"]


# ---- 场景7：三元条件文案（P3-b）----

def test_ternary_condition_text(scan):
    """Text(type === 'A' ? '三元文案A' : '三元文案B')，文案随条件值变化。"""
    edge_a = next(e for e in scan.edges if e["to"] == "NavRouteAPage" and e.get("viaTernary"))
    edge_b = next(e for e in scan.edges if e["to"] == "NavRouteBPage" and e.get("viaTernary"))
    assert edge_a["viaTernary"] == {"condition": "type==='A'", "whenTrue": "三元文案A", "whenFalse": "三元文案B"}
    # type==='A' 的项（A0/A2）文案是「三元文案A」进 A
    assert edge_a["viaItems"] == ["三元文案A", "三元文案A"]
    # type==='B' 的项（B1/B3）文案是「三元文案B」进 B
    assert edge_b["viaItems"] == ["三元文案B", "三元文案B"]
