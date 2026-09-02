"""白盒注入（P2）测试：路由解析 + 降级链注入层（mock driver）。"""
from unittest import mock

from autoshot.config import Config
from autoshot.inject import inject_enabled, inject_target_for_key
from autoshot.scanner import ScanResult


def _scan():
    s = ScanResult(backend="ast")
    s.key_pages = {
        "nav_detail_0": [{"page": "NavDetailPage", "depth": 1}],
        "nav_route_a": [{"page": "NavRouteAPage", "depth": 1}],
        "legacy_page": [{"page": "pages/Index", "depth": 0}],
    }
    s.pages = [
        {"name": "pages/Index", "via": "pages", "exists": True},
        {"name": "NavDetailPage", "via": "routerMap", "exists": True},
        {"name": "NavRouteAPage", "via": "routerMap", "exists": True},
    ]
    return s


def test_inject_target_for_routermap_only():
    scan = _scan()
    assert inject_target_for_key(scan, "nav_detail_0") == "NavDetailPage"
    assert inject_target_for_key(scan, "nav_route_a") == "NavRouteAPage"
    # pages 方案（非 routerMap）不可直达
    assert inject_target_for_key(scan, "legacy_page") is None
    # 无归属
    assert inject_target_for_key(scan, "unknown_key") is None


def test_inject_enabled_flag():
    assert not inject_enabled(Config(project_root="."))
    assert inject_enabled(Config(project_root=".", inject={"enabled": True}))
    assert not inject_enabled(Config(project_root=".", inject={"enabled": False}))


def test_inject_key_runs_only_when_enabled():
    """注入层：未启用时返回 None；启用且可达时重启直达并截图。"""
    from autoshot.cli import _inject_key
    from autoshot.shooter import Shooter

    # 未启用 → None
    cfg = Config(project_root=".", inject=None)
    assert _inject_key(mock.Mock(), cfg, "nav_detail_0", ["详情"], None) is None

    # 启用 + 可达 → 重启直达
    cfg = Config(project_root=".", inject={"enabled": True, "bundle": "com.example.app"},
                 poll_interval=0.0)
    driver = mock.Mock()
    shooter = Shooter(driver, cfg)
    driver.display_size.return_value = (1080, 2340)
    driver.dump_layout.return_value = {"attributes": {},
        "children": [{"attributes": {"text": "详情项2", "bounds": "[100,200][900,400]",
                     "visible": "true", "clickable": "false"}, "children": []}]}
    driver.screenshot.return_value = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    # 让 _inject_key 内部的 _scan_once 命中我们构造的 scan
    import autoshot.cli as cli_mod
    with mock.patch.object(cli_mod, "_scan_once", return_value=_scan()), \
         mock.patch("time.sleep"):
        path = _inject_key(driver, cfg, "nav_detail_0", ["详情项2"], shooter)

    assert path is not None and path.endswith(".png")
    driver.app_stop.assert_called_once_with("com.example.app")
    driver.inject_launch.assert_called_once_with("com.example.app", "NavDetailPage")
