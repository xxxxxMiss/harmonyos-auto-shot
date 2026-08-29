import os
import tempfile

import pytest

from autoshot.config import Config
from autoshot.fake import FakeDriver
from autoshot.shooter import Shooter, TargetNotFound, wait_idle


def make_cfg():
    out = tempfile.mkdtemp(prefix="autoshot_test_")
    return Config(project_root=".", output_dir=out,
                  viewport_margin_top=96, viewport_margin_bottom=96, viewport_margin_side=16,
                  poll_interval=0.0, idle_timeout=1.0, max_scroll_steps=12)


def test_capture_after_scrolling_lazy_list():
    """目标在懒加载列表深处（初始不在无障碍树内）→ 循环滚动直至可见再截图。"""
    items = [f"列表第{i}项" for i in range(1, 26)]
    items[18] = "关于本应用"
    driver = FakeDriver(items, window=6)
    shooter = Shooter(driver, make_cfg())

    path = shooter.capture_texts("settings_about", ["关于本应用"])

    assert os.path.isfile(path)
    assert path.endswith(".png") or path.endswith(".jpeg")
    assert len(driver.shots) == 1
    assert "关于本应用" in driver.visible_texts()   # 截图时目标确实在视口内
    assert len(driver.swipes) >= 2                  # 经历了滚动


def test_capture_reverse_direction_fallback():
    """目标在列表头部，默认向上翻找不到时自动反向。"""
    items = ["关于本应用"] + [f"第{i}项" for i in range(1, 25)]
    driver = FakeDriver(items, window=6)
    driver.offset = 12                               # 已滚到中下部
    shooter = Shooter(driver, make_cfg())

    path = shooter.capture_texts("about", ["关于本应用"])
    assert os.path.isfile(path)


def test_target_not_found_raises():
    driver = FakeDriver(["项目A", "项目B"], window=6)
    shooter = Shooter(driver, make_cfg())
    with pytest.raises(TargetNotFound):
        shooter.capture_texts("nope", ["根本不存在的文案"])


def test_wait_idle_stops_when_stable():
    driver = FakeDriver(["A", "B", "C"], window=6)
    cfg = make_cfg()
    wait_idle(driver, cfg)                            # FakeDriver 树不变，应立即稳定返回
    assert True


def test_placeholder_text_matched():
    """资源值含 %d 占位符，页面渲染为具体数字时仍可命中。"""
    items = [f"第{i}行" for i in range(10)]
    items[7] = "还剩3个"
    driver = FakeDriver(items, window=6)
    shooter = Shooter(driver, make_cfg())
    path = shooter.capture_texts("remaining_count", ["还剩%d个"])
    assert os.path.isfile(path)
