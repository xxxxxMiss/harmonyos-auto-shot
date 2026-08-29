"""阶段三：batch 批量执行器编排逻辑测试（用 mock，不依赖真机）。"""
import os
from unittest import mock

from autoshot.batch import BatchRunner, KeyResult, PageReport
from autoshot.config import Config
from autoshot.resource_index import ResourceIndex
from autoshot.scanner import ScanResult, Usage


def _scan():
    s = ScanResult(backend="ast")
    # key -> 页面归属与用法（模拟 ast 产物）
    s.key_pages = {
        "main_title": [{"page": "pages/Index", "depth": 0}],
        "dialog_message": [{"page": "pages/SettingsPage", "depth": 1}],
    }
    s.page_adj = {"pages/Index": [{"to": "pages/SettingsPage", "viaKey": "nav_settings", "viaText": None}]}
    s.usages = [
        Usage(key="main_title", rule="R1_$r", file="Index.ets", line=1, line_text="",
              trigger_hint="visible"),
        Usage(key="dialog_message", rule="R1_$r", file="SettingsPage.ets", line=2, line_text="",
              trigger_hint="click", trigger_via_key="dialog_button"),
    ]
    s.pages = [
        {"name": "pages/Index", "depth": 0, "exists": True},
        {"name": "pages/SettingsPage", "depth": 1, "exists": True},
    ]
    return s


def _index():
    idx = ResourceIndex()
    idx.values = {
        "main_title": {"zh_CN": "首页"},
        "dialog_message": {"zh_CN": "弹窗内容"},
        "nav_settings": {"zh_CN": "打开设置页"},
        "dialog_button": {"zh_CN": "显示弹窗"},
    }
    idx.modules = {k: {"entry"} for k in idx.values}
    return idx


def _cfg():
    return Config(project_root=".", output_dir="/tmp/autoshot_batch_test",
                  poll_interval=0.0, idle_timeout=0.1)


def test_run_one_triggers_and_resets():
    scan, index, cfg = _scan(), _index(), _cfg()
    driver = mock.Mock()
    driver.display_size.return_value = (1080, 2340)
    # dump_layout 返回含目标文案的树，capture 能成功
    driver.dump_layout.return_value = {"attributes": {},
        "children": [{"attributes": {"text": "弹窗内容", "bounds": "[100,200][900,400]",
                     "visible": "true", "clickable": "true"}, "children": []}]}
    driver.screenshot.return_value = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    runner = BatchRunner(driver, cfg, scan, index, "com.example.app")
    # 只测单个 key 的编排：不真正导航
    trigger = {"click": "text=显示弹窗"}
    reset = {"back": None}
    with mock.patch.object(runner.navigator, "run") as nav_run, \
         mock.patch.object(runner.shooter, "capture_texts", return_value="/tmp/x.png") as cap:
        res = runner._run_one("dialog_message", trigger, reset)
        assert res.ok and res.path == "/tmp/x.png"
        # 先触发、截图、后复位
        assert nav_run.call_count == 2
        assert nav_run.call_args_list[0].args == ([trigger],)
        assert nav_run.call_args_list[1].args == ([reset],)
        cap.assert_called_once()


def test_run_one_resets_even_on_failure():
    scan, index, cfg = _scan(), _index(), _cfg()
    driver = mock.Mock()
    runner = BatchRunner(driver, cfg, scan, index, "com.example.app")
    trigger = {"click": "text=显示弹窗"}
    reset = {"back": None}
    with mock.patch.object(runner.navigator, "run") as nav_run, \
         mock.patch.object(runner.shooter, "capture_texts", side_effect=RuntimeError("boom")):
        res = runner._run_one("dialog_message", trigger, reset)
        assert not res.ok and "boom" in res.error
        # 即使截图失败，复位仍被调用
        assert nav_run.call_count == 2
        assert nav_run.call_args_list[1].args == ([reset],)


def test_run_page_navigates_once():
    scan, index, cfg = _scan(), _index(), _cfg()
    driver = mock.Mock()
    runner = BatchRunner(driver, cfg, scan, index, "com.example.app")
    with mock.patch.object(runner.navigator, "run") as nav_run, \
         mock.patch.object(runner, "_run_one", return_value=KeyResult("k", True)) as one:
        rep = runner.run_page("pages/SettingsPage")
        assert rep.navigated and rep.succeeded == 1
        # 导航只调用一次（launch + click 由 navigator.run 一次执行）
        assert nav_run.call_count == 1
        one.assert_called_once()


def test_run_page_unreachable():
    scan, index, cfg = _scan(), _index(), _cfg()
    # 页面不可达：key_pages 指向不存在的边
    scan.key_pages = {"orphan": [{"page": "pages/Nowhere", "depth": None}]}
    driver = mock.Mock()
    runner = BatchRunner(driver, cfg, scan, index, "com.example.app")
    with mock.patch.object(runner.navigator, "run") as nav_run:
        rep = runner.run_page("pages/Nowhere")
        assert rep.nav_error is not None and not rep.navigated
        nav_run.assert_not_called()
