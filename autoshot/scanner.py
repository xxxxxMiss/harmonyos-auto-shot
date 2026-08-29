"""静态扫描：双后端。

- ast（阶段二正式方案）：调用 tools/ast_scan.mjs（TypeScript Compiler API + struct→class
  预处理），支持 R1 $r / R2 .id / R3 按名 / R4 常量传播 / R5 函数摘要 / R6 查表，
  输出上下文分类（visible/conditional[条件源码]/click/runtime）与导航图（key→页面+深度）。
- regex（阶段一 MVP）：无 node/typescript 环境时的兜底，仅覆盖字面量形态。

scan_project(root, backend='auto') 对外接口不变，Usage/ScanResult 为超集结构。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .resource_index import PRUNE_DIRS

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
AST_SCRIPT = os.path.join(TOOLS_DIR, "ast_scan.mjs")

_KEY = r"[A-Za-z_][A-Za-z0-9_]*"

RULES = [
    # (规则名, 正则, key 所在捕获组)
    ("R1_$r", re.compile(r"\$r\(\s*(['\"])app\.string\.(" + _KEY + r")\1"), 2),
    ("R2_id", re.compile(r"getString(?:Sync|Value)?\(\s*\$r\(\s*(['\"])app\.string\.(" + _KEY + r")\1\s*\)\s*\.\s*id"), 2),
    ("R3_byname", re.compile(
        r"(?:getStringByNameSync|getStringByName|getPluralStringByNameSync|getPluralStringByName"
        r"|getStringArrayByNameSync|getStringArrayByName)\(\s*(['\"])(" + _KEY + r")\1"), 2),
]

# 按名 API 传变量（非字面量）→ 动态引用
_RULE_DYNAMIC = re.compile(
    r"(getStringByNameSync|getStringByName|getPluralStringByNameSync|getPluralStringByName"
    r"|getStringArrayByNameSync|getStringArrayByName)\(\s*(?!['\"\\\)])([A-Za-z_$][\w$.]*)")


@dataclass
class Usage:
    key: str
    rule: str
    file: str           # 相对工程根
    line: int
    line_text: str
    trigger_hint: str = "unknown"   # visible | conditional | click | runtime | unknown
    # 阶段二新增（regex 后端不填充）
    triggers: List[str] = field(default_factory=list)
    condition_text: Optional[str] = None
    struct_name: Optional[str] = None
    method_name: Optional[str] = None
    # 阶段三新增：触发按钮标签（弹窗/导航）与状态开关
    trigger_via_key: Optional[str] = None
    trigger_via_text: Optional[str] = None
    toggle_var: Optional[str] = None
    toggle_via_key: Optional[str] = None
    toggle_via_text: Optional[str] = None


@dataclass
class DynamicRef:
    expr: str
    file: str
    line: int
    line_text: str = ""


@dataclass
class ScanResult:
    usages: List[Usage] = field(default_factory=list)
    dynamic_refs: List[DynamicRef] = field(default_factory=list)
    backend: str = "regex"
    accessors: List[dict] = field(default_factory=list)
    pages: List[dict] = field(default_factory=list)
    edges: List[dict] = field(default_factory=list)
    key_pages: Dict[str, List[dict]] = field(default_factory=dict)
    # 阶段三新增：页面级邻接表（含触发标签）与状态开关表
    page_adj: Dict[str, List[dict]] = field(default_factory=dict)
    toggles: List[dict] = field(default_factory=list)

    @property
    def by_key(self) -> Dict[str, List[Usage]]:
        out: Dict[str, List[Usage]] = {}
        for u in self.usages:
            out.setdefault(u.key, []).append(u)
        return out


# ---------- AST 后端 ----------

def find_node() -> Optional[str]:
    """node 探测：PATH 优先，其次 DevEco Studio 自带。"""
    n = shutil.which("node")
    if n:
        return n
    deveco = "/Applications/DevEco-Studio.app/Contents/tools/node/bin/node"
    return deveco if os.path.isfile(deveco) else None


def ast_backend_available() -> bool:
    node = find_node()
    return bool(node) and os.path.isfile(AST_SCRIPT) and \
        os.path.isdir(os.path.join(os.path.dirname(TOOLS_DIR), "node_modules", "typescript"))


def run_ast_scan(project_root: str) -> dict:
    node = find_node()
    if not node:
        raise RuntimeError("未找到 node：ast 后端需要 Node.js（PATH 或 DevEco Studio 自带）")
    if not ast_backend_available():
        raise RuntimeError("ast 后端未就绪：请在工程根执行 npm install（安装 typescript）")
    out = os.path.join(tempfile.gettempdir(), "autoshot_ast_scan.json")
    proc = subprocess.run([node, AST_SCRIPT, os.path.abspath(project_root), out],
                          capture_output=True, timeout=180, cwd=os.path.dirname(TOOLS_DIR))
    if proc.returncode != 0:
        raise RuntimeError(f"ast_scan 执行失败: {proc.stderr.decode('utf-8', 'replace')[:500]}")
    with open(out, "r", encoding="utf-8") as f:
        return json.load(f)


def _scan_ast(root: str) -> ScanResult:
    data = run_ast_scan(root)
    res = ScanResult(backend="ast")
    for u in data.get("usages", []):
        res.usages.append(Usage(
            key=u["key"], rule=u["rule"], file=u["file"], line=u["line"],
            line_text=u.get("lineText", ""), trigger_hint=u.get("triggerHint", "unknown"),
            triggers=u.get("triggers", []), condition_text=u.get("conditionText"),
            struct_name=u.get("struct"), method_name=u.get("method"),
            trigger_via_key=u.get("triggerViaKey"), trigger_via_text=u.get("triggerViaText"),
            toggle_var=u.get("toggleVar"), toggle_via_key=u.get("toggleViaKey"),
            toggle_via_text=u.get("toggleViaText")))
    for d in data.get("dynamicRefs", []):
        res.dynamic_refs.append(DynamicRef(d["expr"], d["file"], d["line"], d.get("lineText", "")))
    res.accessors = data.get("accessors", [])
    res.pages = data.get("pages", [])
    res.edges = data.get("edges", [])
    res.key_pages = data.get("keyPages", {})
    res.page_adj = data.get("pageAdj", {})
    res.toggles = data.get("toggles", [])
    return res


# ---------- regex 后端（MVP 兜底） ----------

_DIALOG_MARKERS = re.compile(
    r"AlertDialog|showToast|showDialog|CustomDialog|bindSheet|bindContentCover|openCustomDialog|promptAction")


def _iter_source_files(project_root: str):
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for name in filenames:
            if name.endswith((".ets", ".ts")):
                yield os.path.join(dirpath, name)


def _classify_regex(line_text: str) -> str:
    if _DIALOG_MARKERS.search(line_text):
        return "click"
    return "unknown"


def _scan_regex(project_root: str) -> ScanResult:
    result = ScanResult(backend="regex")
    root = os.path.abspath(project_root)
    for path in _iter_source_files(root):
        rel = os.path.relpath(path, root)
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for rule_name, pattern, group in RULES:
                for m in pattern.finditer(line):
                    result.usages.append(Usage(
                        key=m.group(group), rule=rule_name, file=rel, line=lineno,
                        line_text=line.strip(), trigger_hint=_classify_regex(line)))
            for m in _RULE_DYNAMIC.finditer(line):
                result.dynamic_refs.append(DynamicRef(
                    expr=m.group(2), file=rel, line=lineno, line_text=line.strip()))
    return result


# ---------- 对外入口 ----------

def scan_project(project_root: str, backend: str = "auto") -> ScanResult:
    """backend: auto（ast 可用则 ast，否则 regex）| ast | regex。"""
    if backend == "ast":
        return _scan_ast(project_root)
    if backend == "auto" and ast_backend_available():
        try:
            return _scan_ast(project_root)
        except RuntimeError:
            pass  # ast 失败自动回落 regex
    return _scan_regex(project_root)


def scan_report(index, scan: ScanResult) -> Dict[str, object]:
    """静态扫描 × 资源索引 双向对账。"""
    by_key = scan.by_key
    all_keys = set(index.values)
    used = set(by_key)
    unknown = sorted(k for k in used if k not in all_keys)       # 引用了但资源里没有
    unreferenced = sorted(all_keys - used)                        # 资源里有但没人引用（或动态引用）
    hints = {k: sorted({u.trigger_hint for u in usages}) for k, usages in by_key.items()}
    report: Dict[str, object] = {
        "backend": scan.backend,
        "total_resource_keys": len(all_keys),
        "referenced_keys": len(used & all_keys),
        "unreferenced_keys": len(unreferenced),
        "unreferenced_key_list": unreferenced,
        "keys_not_in_resources": unknown,
        "dynamic_refs": len(scan.dynamic_refs),
        "dynamic_ref_list": [
            {"expr": d.expr, "file": d.file, "line": d.line} for d in scan.dynamic_refs],
        "trigger_hints": hints,
    }
    if scan.backend == "ast":
        report["i18n_accessors"] = len(scan.accessors)
        report["pages"] = len(scan.pages)
        report["keys_with_pages"] = len(scan.key_pages)
    return report


def build_usage_index(index, scan: ScanResult, project_root: str) -> Dict[str, object]:
    """产出 usage_index.json 的内容：key -> 各语种值 + 引用位置 + 触发分类 + 页面归属。"""
    by_key = scan.by_key
    modules = {k: sorted(mods) for k, mods in index.modules.items() if k in by_key}
    keys: Dict[str, object] = {}
    for key, usages in sorted(by_key.items()):
        if key not in index.values:
            continue
        keys[key] = {
            "values": index.get_values(key),
            "modules": modules.get(key, []),
            "pages": scan.key_pages.get(key, []),
            "usages": [{
                "file": u.file, "line": u.line, "rule": u.rule,
                "trigger_hint": u.trigger_hint, "triggers": u.triggers,
                "condition": u.condition_text, "struct": u.struct_name, "method": u.method_name,
                "trigger_via": u.trigger_via_key or u.trigger_via_text,
                "toggle_via": u.toggle_via_key or u.toggle_via_text,
                "code": u.line_text,
            } for u in usages],
        }
    return {
        "project_root": os.path.abspath(project_root),
        "backend": scan.backend,
        "keys": keys,
        "dynamic_refs": [{"expr": d.expr, "file": d.file, "line": d.line} for d in scan.dynamic_refs],
        "accessors": scan.accessors,
        "pages": scan.pages,
        "edges": scan.edges,
    }
