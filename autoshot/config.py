"""运行配置：CLI 参数 + 项目根目录下的 autoshot.yaml（可选）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # 允许仅使用离线命令（scan/lookup）时不装 yaml
    yaml = None


@dataclass
class Config:
    project_root: str = "."
    hdc_path: str = "hdc"
    device_serial: Optional[str] = None
    output_dir: str = "shots"
    image_format: str = "png"            # png | jpeg（注意：设备截屏原始输出恒为 JPEG）
    locale: Optional[str] = None         # 限定语种；None=用所有语种值参与匹配
    max_scroll_steps: int = 12
    # 视口安全边距：避开吸顶栏/底部 tab 悬浮遮挡
    viewport_margin_top: int = 96
    viewport_margin_bottom: int = 96
    viewport_margin_side: int = 16
    idle_timeout: float = 6.0
    poll_interval: float = 0.4
    scroll_direction: str = "up"         # up=看下方内容；找不到时自动反向再试
    # 导航过程中出现这些可点击文案时自动点掉（系统弹窗/权限），按序取第一个命中的
    grant_texts: List[str] = field(default_factory=lambda: ["允许", "仅使用期间允许"])
    dismiss_texts: List[str] = field(default_factory=lambda: ["我知道了", "知道了", "暂不"])
    main_ability: str = "EntryAbility"      # aa start 必须显式指定 ability（系统禁止隐式启动）
    screen_size: Optional[List[int]] = None  # [w, h] 手动指定，跳过自动探测


def _merge(cfg: Config, data: Dict[str, Any]) -> Config:
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg


def load_config(project_root: str, overrides: Optional[Dict[str, Any]] = None) -> Config:
    cfg = Config(project_root=os.path.abspath(project_root))
    path = os.path.join(cfg.project_root, "autoshot.yaml")
    if os.path.isfile(path):
        if yaml is None:
            raise RuntimeError("项目配置 autoshot.yaml 需要 PyYAML：pip3 install PyYAML")
        with open(path, "r", encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    if overrides:
        cfg = _merge(cfg, {k: v for k, v in overrides.items() if v is not None})
    if cfg.image_format not in ("png", "jpeg"):
        raise ValueError(f"image_format 仅支持 png/jpeg，当前: {cfg.image_format}")
    return cfg
