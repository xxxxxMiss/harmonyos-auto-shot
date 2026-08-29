"""阶段三：导航图 → 自动场景生成。

利用 ast 扫描产出的 pageAdj（导航边含触发标签）、usage 的 trigger/toggle 富集信息，
推导到达每个页面/每个 key 的操作步骤，生成 scenes.auto.yaml（可被 SceneRegistry 加载，
并与手写 scenes.yaml 合并，手写优先）。

核心推导：
  - 导航路径：入口页面 BFS 到目标页，每条边用 viaKey/viaText 转成 click 步骤；
  - 触发步道：click 类（弹窗/Toast）→ 点 triggerVia 按钮；conditional 类 → 点 toggleVia 开关；
  - 复位动作：弹窗 → Back；状态开关 → 再点一次还原。
"""
from __future__ import annotations

import os
import re
from collections import deque
from typing import Dict, List, Optional, Tuple

import yaml

from .resource_index import ResourceIndex
from .scanner import ScanResult, Usage

# 触发类型优先级：visible 优先（静态可见是最典型的展示，导航即可截），
# 其次是 click（弹窗/Toast）、conditional（条件渲染）、runtime、unknown。
# 同 key 多用法时选"最省事、最典型"的语境截图。
_TRIGGER_PRIORITY = {"visible": 0, "click": 1, "conditional": 2, "runtime": 3, "unknown": 4}


def resolve_label(via_key: Optional[str], via_text: Optional[str], index: ResourceIndex) -> Optional[str]:
    """触发按钮的 key/字面量 → 设备上显示的文案（优先 base/zh_CN）。"""
    if via_text:
        return via_text
    if via_key:
        vals = index.get_values(via_key)
        for loc in ("base", "zh_CN", "en_US"):
            if vals.get(loc):
                return vals[loc]
        for v in vals.values():
            if v:
                return v
    return None


def bundle_name(project_root: str) -> Optional[str]:
    """从 AppScope/app.json5 读取 bundleName（json5 容错：正则抽取）。"""
    p = os.path.join(project_root, "AppScope", "app.json5")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'"bundleName"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else None


def entry_page(scan: ScanResult) -> Optional[str]:
    for p in scan.pages:
        if p.get("depth") == 0 and p.get("exists"):
            return p["name"]
    for p in scan.pages:
        if p.get("exists"):
            return p["name"]
    return None


def bfs_path(page_adj: Dict[str, List[dict]], entry: str, target: str) -> Optional[List[dict]]:
    """从 entry 到 target 的边序列（BFS 最短路径）。同页返回空列表。"""
    if entry == target:
        return []
    parent: Dict[str, Tuple[Optional[str], Optional[dict]]] = {entry: (None, None)}
    q = deque([entry])
    visited = {entry}
    while q:
        cur = q.popleft()
        for edge in page_adj.get(cur, []):
            nxt = edge.get("to")
            if nxt is None or nxt in visited:
                continue
            visited.add(nxt)
            parent[nxt] = (cur, edge)
            if nxt == target:
                path: List[dict] = []
                node = target
                while parent[node][0] is not None:
                    prev, e = parent[node]
                    path.append(e)
                    node = prev
                path.reverse()
                return path
            q.append(nxt)
    return None


def click_steps_for_path(path: List[dict], index: ResourceIndex) -> List[dict]:
    steps: List[dict] = []
    for edge in path:
        label = resolve_label(edge.get("viaKey"), edge.get("viaText"), index)
        if label:
            steps.append({"click": f"text={label}"})
    return steps


def pick_usage(usages: List[Usage]) -> Optional[Usage]:
    if not usages:
        return None
    return min(usages, key=lambda u: _TRIGGER_PRIORITY.get(u.trigger_hint, 4))


def trigger_and_reset(usage: Usage, index: ResourceIndex) -> Tuple[Optional[dict], Optional[dict]]:
    """返回 (触发步骤, 复位步骤)。触发步骤为 navigator 可执行 dict 或 None。"""
    trigger = reset = None
    if usage.trigger_hint == "click":
        label = resolve_label(usage.trigger_via_key, usage.trigger_via_text, index)
        if label:
            trigger = {"click": f"text={label}"}
            reset = {"back": None}     # 弹窗/Toast 复位：返回键（Toast 无副作用）
    elif usage.trigger_hint == "conditional":
        label = resolve_label(usage.toggle_via_key, usage.toggle_via_text, index)
        if label:
            trigger = {"click": f"text={label}"}
            reset = {"click": f"text={label}"}   # 状态开关再点一次还原
    return trigger, reset


# ---- 对外：生成场景注册表内容 ----

def generate(scan: ScanResult, index: ResourceIndex, project_root: str) -> Dict:
    """产出与 scenes.yaml 兼容的结构（scenes 列表 + default）。"""
    bundle = bundle_name(project_root)
    default_steps = [{"launch": bundle}] if bundle else []
    scenes: List[dict] = []
    entry = entry_page(scan)

    # key → 目标页（取最浅）
    key_target: Dict[str, str] = {}
    for key, pages in scan.key_pages.items():
        if pages:
            key_target[key] = pages[0]["page"]

    # 按 (页面, 触发签名) 分组
    groups: Dict[Tuple[str, str], List[str]] = {}
    for key, page in key_target.items():
        usage = pick_usage([u for u in scan.usages if u.key == key])
        if usage is None:
            continue
        trigger, _ = trigger_and_reset(usage, index)
        sig = ""
        if trigger:
            sig = f"trigger:{trigger.get('click', '')}"
        groups.setdefault((page, sig), []).append(key)

    for (page, sig), keys in sorted(groups.items()):
        usage = pick_usage([u for u in scan.usages if u.key == keys[0]])
        steps: List[dict] = list(default_steps)
        if entry and entry != page:
            path = bfs_path(scan.page_adj, entry, page)
            if path is None:
                # 无法自动导航：留空，标注需要人工补
                scenes.append({"name": page, "keys": keys, "steps": list(default_steps),
                               "conditions": {"_needs_manual_nav": "true"}})
                continue
            steps += click_steps_for_path(path, index)
        trigger, _ = trigger_and_reset(usage, index)
        if trigger:
            steps.append(trigger)
        name = page.replace("/", "_").replace("pages_", "")
        if sig:
            name += "__" + sig.replace("trigger:click:text=", "").replace(" ", "_")
        scenes.append({"name": name, "keys": keys, "steps": steps})

    return {"default": {"steps": default_steps}, "scenes": scenes}


def page_groups_for_batch(scan: ScanResult, index: ResourceIndex) -> Dict[str, List[dict]]:
    """批量执行分组：page -> [{key, trigger, reset}]（导航一次，逐 key 触发+截图）。"""
    entry = entry_page(scan)
    out: Dict[str, List[dict]] = {}
    for key, pages in scan.key_pages.items():
        if not pages:
            continue
        page = pages[0]["page"]
        usage = pick_usage([u for u in scan.usages if u.key == key])
        if usage is None:
            continue
        trigger, reset = trigger_and_reset(usage, index)
        out.setdefault(page, []).append({"key": key, "trigger": trigger, "reset": reset})
    return out


def navigation_steps_to_page(scan: ScanResult, index: ResourceIndex, page: str, bundle: str) -> Optional[List[dict]]:
    """到达某页面的完整导航步骤（含 launch）。不可达返回 None。"""
    entry = entry_page(scan)
    steps: List[dict] = [{"launch": bundle}]
    if entry == page:
        return steps
    path = bfs_path(scan.page_adj, entry, page)
    if path is None:
        return None
    steps += click_steps_for_path(path, index)
    return steps


def write_scenes_yaml(scan: ScanResult, index: ResourceIndex, project_root: str, out_path: str) -> str:
    data = generate(scan, index, project_root)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# 自动生成（autoshot gen-scenes）。可复制/合并到手写 scenes.yaml，手写优先。\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return out_path
