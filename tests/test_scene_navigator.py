import os
import time

import pytest

from autoshot.config import Config
from autoshot.fake import FakeDriver
from autoshot.navigator import Navigator, StepError
from autoshot.scene import SceneRegistry

FIXTURE_SCENES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_app", "scenes.yaml")


def make_cfg():
    return Config(project_root=".", poll_interval=0.0, idle_timeout=1.0)


def test_scene_registry_load_and_match():
    reg = SceneRegistry.load(FIXTURE_SCENES)
    assert reg.find_for_key("settings_about").name == "settings"
    assert reg.find_for_key("main_title").name == "home"
    assert reg.find_for_key("unknown_key") is None
    assert reg.default is not None


def test_navigator_expect_and_click():
    driver = FakeDriver(["首页", "设置", "登录"], window=6)
    nav = Navigator(driver, make_cfg())
    nav.run([
        {"expect": "text=设置"},
        {"click": "text=设置"},
    ])
    assert driver.clicks, "应点击了目标节点中心"


def test_navigator_click_missing_raises():
    driver = FakeDriver(["首页"], window=6)
    nav = Navigator(driver, make_cfg())
    with pytest.raises(StepError):
        nav.run([{"click": "text=不存在的按钮"}])


def test_navigator_unknown_action():
    driver = FakeDriver(["首页"], window=6)
    nav = Navigator(driver, make_cfg())
    with pytest.raises(StepError):
        nav.run([{"frobnicate": 1}])
