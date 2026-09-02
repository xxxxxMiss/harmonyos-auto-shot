"""白盒注入（P2）：Want 参数直达路由 + AppStorage 状态注入。

黑盒探索（静态场景 + Explorer 启发式/LLM）够不着的场景——登录墙、深层状态、前置条件——
用"重启到目标状态"解决。机制：

  1. 被测工程入口（EntryAbility）读 want.parameters 里的 `autoshot.route` / `autoshot.state.*`，
     写入 AppStorage（一次性合作成本，示例见 docs/inject_example.md）；
  2. 首页 Navigation 在 aboutToAppear 读 AppStorage，若含 route 则 pushPathByName 直达；
  3. 工具侧：`aa start -b <bundle> -a <ability> --pi autoshot.route=<RouteName>` 直达。

注入能力是**配置声明**：用户在 autoshot.yaml 里配 `inject.enabled: true` 表示已装好入口。
仅对 NavDestination（routerMap）方案的页面有效——只有它有 pushPathByName 可达的 route name。
"""
from __future__ import annotations

from typing import Optional


def inject_target_for_key(scan, key: str) -> Optional[str]:
    """返回 key 可直达的 NavDestination 路由名；无归属或非 routerMap 页面返回 None。"""
    if scan is None:
        return None
    key_pages = getattr(scan, "key_pages", {}) or {}
    kps = key_pages.get(key) or []
    if not kps:
        return None
    # 仅 routerMap 方案的页面有 route name（pushPathByName 可达）
    via = {p.get("name"): p.get("via") for p in (getattr(scan, "pages", None) or [])}
    for kp in kps:
        page = kp.get("page")
        if via.get(page) == "routerMap":
            return page
    return None


def inject_enabled(cfg) -> bool:
    return bool((getattr(cfg, "inject", None) or {}).get("enabled"))
