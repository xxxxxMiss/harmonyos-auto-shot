"""截图任务总结报告：数据模型 + markdown / json 渲染。

capture 命令批量处理多个资源 key（或截图反查出的多个 key）后，产出统一报告：
  - 待处理项（含解析来源：key 直给 / 文案反查 / OCR 行反查）
  - 成功项（产物路径）
  - 失败项（失败原因）
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ResolvedInput:
    """一个输入（name/文案/OCR 行）到资源 key 的解析结果。"""
    input: str                          # 原始输入
    key: str                            # 解析出的资源 key
    source: str                         # key | text | ocr
    candidates: List[str] = field(default_factory=list)   # 参与运行时匹配的文案


@dataclass
class UnresolvedInput:
    """无法唯一定位到 key 的输入。"""
    input: str
    reason: str                         # 无匹配 / 歧义 / OCR 噪声
    candidates: List[str] = field(default_factory=list)


@dataclass
class CaptureResult:
    input: str
    key: str
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    duration: float = 0.0
    method: str = "scene"   # scene=静态场景 fast path / explore=运行时探索兜底


@dataclass
class Report:
    project_root: str
    resolved: List[ResolvedInput] = field(default_factory=list)
    unresolved: List[UnresolvedInput] = field(default_factory=list)
    results: List[CaptureResult] = field(default_factory=list)
    output_dir: str = "shots"
    locale: Optional[str] = None
    image_source: Optional[str] = None      # 截图输入路径
    generated_at: float = field(default_factory=time.time)

    # ---- 汇总 ----
    def summary(self) -> Dict:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.ok)
        return {
            "total": total,
            "succeeded": ok,
            "failed": total - ok,
            "unresolved": len(self.unresolved),
            "input_count": len(self.resolved) + len(self.unresolved),
        }

    # ---- 渲染 ----
    def to_markdown(self) -> str:
        s = self.summary()
        lines: List[str] = []
        lines.append("# 截图任务报告")
        lines.append("")
        lines.append(f"- 目标工程: `{self.project_root}`")
        if self.image_source:
            lines.append(f"- 输入: 截图 `{self.image_source}`")
        else:
            lines.append(f"- 输入: {s['input_count']} 个 name")
        lines.append(f"- 语种: {self.locale or '全部'}")
        lines.append(f"- 产物目录: `{self.output_dir}`")
        lines.append(f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.generated_at))}")
        lines.append("")

        # 待处理项
        lines.append(f"## 一、待处理项（{s['input_count']}）")
        lines.append("")
        lines.append("| # | 输入 | 解析为 key | 来源 |")
        lines.append("|---|---|---|---|")
        for i, r in enumerate(self.resolved, 1):
            lines.append(f"| {i} | `{r.input}` | `{r.key}` | {r.source} |")
        lines.append("")

        if self.unresolved:
            lines.append(f"### 未能解析的输入（{len(self.unresolved)}）")
            lines.append("")
            lines.append("| 输入 | 原因 | 候选 key |")
            lines.append("|---|---|---|")
            for u in self.unresolved:
                lines.append(f"| `{u.input}` | {u.reason} | {', '.join(f'`{c}`' for c in u.candidates) or '-'} |")
            lines.append("")

        # 处理结果
        lines.append(f"## 二、处理结果")
        lines.append("")
        lines.append("| # | key | 结果 | 方式 | 产物 / 失败原因 |")
        lines.append("|---|---|---|---|---|")
        for i, r in enumerate(self.results, 1):
            mark = "✅ 成功" if r.ok else "❌ 失败"
            detail = r.path or r.error or "-"
            method = {"scene": "场景", "explore": "探索", "inject": "注入"}.get(r.method, r.method)
            lines.append(f"| {i} | `{r.key}` | {mark} | {method} | {detail} |")
        lines.append("")

        # 汇总
        lines.append("## 三、汇总")
        lines.append("")
        lines.append(f"- ✅ 成功: **{s['succeeded']}**")
        lines.append(f"- ❌ 失败: **{s['failed']}**")
        lines.append(f"- ⚠️ 未能解析: **{s['unresolved']}**")
        lines.append(f"- 总计: {s['total']} 个 key 已处理 / {s['input_count']} 个输入")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            "project_root": self.project_root,
            "image_source": self.image_source,
            "locale": self.locale,
            "output_dir": self.output_dir,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.generated_at)),
            "summary": self.summary(),
            "resolved": [{"input": r.input, "key": r.key, "source": r.source,
                          "candidates": r.candidates} for r in self.resolved],
            "unresolved": [{"input": u.input, "reason": u.reason,
                            "candidates": u.candidates} for u in self.unresolved],
            "results": [{"input": r.input, "key": r.key, "ok": r.ok,
                         "path": r.path, "error": r.error, "duration": round(r.duration, 2),
                         "method": r.method} for r in self.results],
        }

    def write_json(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def write_markdown(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_markdown())
        return path
