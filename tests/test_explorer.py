"""运行时探索兜底（Explorer）测试：用多页 AppDriver 验证观察->决策->动作闭环。"""
import os
import tempfile

import pytest

from autoshot.config import Config
from autoshot.explorer import Explorer, HeuristicPolicy
from autoshot.fake import AppDriver
from autoshot.shooter import TargetNotFound


def make_cfg(**kw):
    out = tempfile.mkdtemp(prefix="autoshot_exp_")
    base = dict(project_root=".", output_dir=out,
                viewport_margin_top=96, viewport_margin_bottom=96, viewport_margin_side=16,
                poll_interval=0.0, idle_timeout=0.2, max_scroll_steps=12, max_explore_steps=40)
    base.update(kw)
    return Config(**base)


def _explorer(driver, cfg, page_adj=None):
    policy = HeuristicPolicy(cfg, page_adj=page_adj)
    return Explorer(driver, cfg, policy)


def test_navigate_to_content_on_other_page():
    """目标文案在二级页：靠导航图边标签点击进页后截图。"""
    driver = AppDriver()
    driver.add_page("home", [{"text": "首页标题"},
                             {"text": "设置", "clickable": True, "target": "settings"}])
    driver.add_page("settings", [{"text": "设置页标题"},
                                 {"text": "关于本应用"}])
    cfg = make_cfg()
    page_adj = {"pages/Index": [{"to": "pages/SettingsPage", "viaText": "设置"}]}
    ex = _explorer(driver, cfg, page_adj)

    path = ex.reach_and_shoot("settings_about", ["关于本应用"])

    assert os.path.isfile(path)
    assert driver.current == "settings"
    assert len(driver.clicks) >= 1
    assert len(driver.shots) == 1


def test_scroll_lazy_list_to_find_content():
    """目标在懒加载列表深处：Explorer 滚动直到渲染并截图。"""
    items = [f"列表第{i}项" for i in range(1, 26)]
    items[18] = "关于本应用"
    driver = AppDriver()
    driver.set_list("home", items, window=6)
    cfg = make_cfg()
    ex = _explorer(driver, cfg)

    path = ex.reach_and_shoot("about", ["关于本应用"])

    assert os.path.isfile(path)
    assert len(driver.swipes) >= 2
    assert len(driver.shots) == 1


def test_content_priority_over_button():
    """目标文案作为非可点内容出现时，直接截图而非点击（内容优先）。"""
    driver = AppDriver()
    driver.add_page("home", [{"text": "关于本应用"}])   # 非可点 Text
    cfg = make_cfg()
    ex = _explorer(driver, cfg)

    path = ex.reach_and_shoot("about", ["关于本应用"])

    assert os.path.isfile(path)
    assert len(driver.clicks) == 0          # 未点击任何按钮
    assert len(driver.shots) == 1


def test_stuck_raises_target_not_found():
    """无导航标签、无滚动、无候选命中 → 放弃并抛出 TargetNotFound。"""
    driver = AppDriver()
    driver.add_page("home", [{"text": "孤零零的页面"}])
    cfg = make_cfg(max_explore_steps=5)
    ex = _explorer(driver, cfg)

    with pytest.raises(TargetNotFound):
        ex.reach_and_shoot("nope", ["根本不存在的文案"])
