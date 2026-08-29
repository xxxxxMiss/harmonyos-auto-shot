"""场景注册表：key/页面 -> 到达该页面的操作步骤（YAML，随仓库管理）。

示例见 scenes.yaml。MVP 阶段人工维护，后续由自动探索（L4）沉淀生成。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class Scene:
    name: str
    steps: List[dict] = field(default_factory=list)   # navigator 可执行步骤
    keys: List[str] = field(default_factory=list)     # 该场景覆盖的资源 key
    conditions: Dict[str, str] = field(default_factory=dict)  # 如 network: broken（二期接入故障注入）

    def covers(self, key: str) -> bool:
        return key in self.keys


class SceneRegistry:
    def __init__(self) -> None:
        self.scenes: List[Scene] = []
        self.default: Optional[Scene] = None

    @classmethod
    def load(cls, path: str) -> "SceneRegistry":
        if not os.path.isfile(path):
            raise FileNotFoundError(f"场景注册表不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        reg = cls()
        raw_scenes = data.get("scenes") or []
        if isinstance(raw_scenes, dict):        # scenes: {name: {...}} 形式
            items = [dict(body or {}, name=name) for name, body in raw_scenes.items()]
        else:                                   # scenes: [{name: ..., ...}] 列表形式
            items = raw_scenes
        for item in items:
            scene = Scene(
                name=item.get("name", "unnamed"),
                steps=item.get("steps", []) or [],
                keys=list(item.get("keys", []) or []),
                conditions=item.get("conditions", {}) or {},
            )
            reg.scenes.append(scene)
        d = data.get("default")
        if d:
            reg.default = Scene(name="default", steps=d.get("steps", []) or [])
        return reg

    def find_for_key(self, key: str) -> Optional[Scene]:
        for s in self.scenes:
            if s.covers(key):
                return s
        return None
