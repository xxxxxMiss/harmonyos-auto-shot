"""资源解析：扫描工程内所有 string.json，建立 key -> {locale: value} 索引。

目录约定：`<module>/src/main/resources/<locale>/element/string.json`，
locale 如 base / zh_CN / en_US；模块名取 src/main 之前的路径段（如 entry）。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from typing import Dict, List, Optional, Tuple

# 遍历时剪枝的目录（构建产物/依赖）
PRUNE_DIRS = {"oh_modules", "node_modules", "build", ".hvigor", ".preview", ".cxx", ".idea", ".git"}

_COMMENT_LINE = re.compile(r"^\s*//.*$", re.M)
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)


def _load_json_tolerant(path: str) -> dict:
    """string.json 理论上是标准 JSON，个别工程会混入注释，做一次容错。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = _COMMENT_BLOCK.sub("", _COMMENT_LINE.sub("", raw))
        return json.loads(cleaned)


def find_string_json_files(project_root: str) -> List[str]:
    hits: List[str] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for name in filenames:
            if name != "string.json":
                continue
            parts = os.path.normpath(dirpath).split(os.sep)
            # .../<module>/src/main/resources/<locale>/element
            if len(parts) >= 4 and parts[-1] == "element" and parts[-3] == "resources":
                hits.append(os.path.join(dirpath, name))
    return sorted(hits)


def _module_of(path: str) -> str:
    parts = os.path.normpath(path).split(os.sep)
    try:
        i = parts[::-1].index("main")          # ... entry src main resources zh_CN element string.json
        return parts[len(parts) - i - 3] if len(parts) - i - 3 >= 0 else "app"
    except ValueError:
        return "app"


class ResourceIndex:
    """key -> {locale: value}；同 key 跨模块时 modules 记录全部来源模块。"""

    def __init__(self) -> None:
        self.values: Dict[str, Dict[str, str]] = {}
        self.modules: Dict[str, set] = {}

    # ---- 构建 ----
    @classmethod
    def from_project(cls, project_root: str) -> "ResourceIndex":
        idx = cls()
        for path in find_string_json_files(project_root):
            locale = os.path.basename(os.path.dirname(os.path.dirname(path)))  # element 的上级目录名
            module = _module_of(path)
            try:
                data = _load_json_tolerant(path)
            except (json.JSONDecodeError, OSError) as e:
                raise RuntimeError(f"解析失败 {path}: {e}") from e
            for item in data.get("string", []):
                name, value = item.get("name"), item.get("value")
                if not name or value is None:
                    continue
                idx.values.setdefault(name, {})[locale] = value
                idx.modules.setdefault(name, set()).add(module)
        return idx

    # ---- 查询 ----
    def get_values(self, key: str, locale: Optional[str] = None) -> Dict[str, str]:
        vals = self.values.get(key, {})
        if locale:
            return {locale: v for l, v in vals.items() if l == locale}
        return vals

    def texts_for_matching(self, key: str, locale: Optional[str] = None) -> List[str]:
        """参与运行时文本匹配的候选文案（去重、保序）。"""
        vals = self.get_values(key, locale)
        out: List[str] = []
        for v in vals.values():
            if v and v not in out:
                out.append(v)
        return out

    def lookup_text(self, text: str, locale: Optional[str] = None) -> List[Tuple[str, str, str, bool]]:
        """按显示文案反查 key（OCR/截图输入的入口）。返回 (key, locale, value, 是否精确命中)。"""
        res: List[Tuple[str, str, str, bool]] = []
        for key, vals in self.values.items():
            for loc, val in vals.items():
                if locale and loc != locale:
                    continue
                if val == text:
                    res.append((key, loc, val, True))
                elif text in val or val in text:
                    res.append((key, loc, val, False))
        # 精确命中优先
        res.sort(key=lambda t: not t[3])
        return res
