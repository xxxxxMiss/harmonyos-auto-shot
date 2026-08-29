"""输入解析：把用户给的 name / 文案 / 截图 映射为资源 key 列表。

规则（对每个输入）：
  1. 是资源 key（存在 index.values）→ 直接使用，source=key；
  2. 否则当作显示文案，用 index.lookup_text 反查：
     - 存在精确命中 → 取首个精确命中的 key；
     - 只有模糊命中或多条候选 → 标记歧义（unresolved，附候选）；
     - 无命中 → 无匹配（unresolved）。

截图输入：OCR 提取逐行文案，每行按上述规则反查。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .report import ResolvedInput, UnresolvedInput
from .resource_index import ResourceIndex


def ocr_lines(image_path: str) -> List[str]:
    """OCR 提取截图中逐行文案（保留行结构，非空格拼接）。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise RuntimeError(
            "图片输入需要 OCR：pip3 install rapidocr-onnxruntime（或直接用 name 参数传字符串）")
    ocr = RapidOCR()
    result, _ = ocr(image_path)
    if not result:
        return []
    lines: List[str] = []
    for item in result:
        text = (item[1] or "").strip()
        if text:
            lines.append(text)
    return lines


def resolve_one(index: ResourceIndex, text: str, locale: Optional[str] = None,
                source: str = "text") -> Tuple[Optional[ResolvedInput], Optional[UnresolvedInput]]:
    """把单个输入字符串解析为 key。返回 (resolved, unresolved)，恰一个非 None。"""
    t = text.strip()
    if not t:
        return None, None
    # 1) 直接是 key
    if t in index.values:
        return ResolvedInput(input=t, key=t, source="key",
                             candidates=index.texts_for_matching(t, locale)), None
    # 2) 反查文案
    hits = index.lookup_text(t, locale=locale)
    if not hits:
        return None, UnresolvedInput(input=t, reason="无匹配资源")
    # 精确命中优先
    exact = [h for h in hits if h[3]]
    if exact:
        key = exact[0][0]
        return ResolvedInput(input=t, key=key, source=source,
                             candidates=index.texts_for_matching(key, locale)), None
    # 只有模糊命中 → 歧义
    cand_keys = sorted({h[0] for h in hits})
    return None, UnresolvedInput(input=t, reason="歧义（仅模糊匹配）", candidates=cand_keys)


def resolve_inputs(index: ResourceIndex, names: Optional[List[str]] = None,
                   image_path: Optional[str] = None,
                   locale: Optional[str] = None) -> Tuple[List[ResolvedInput], List[UnresolvedInput]]:
    resolved: List[ResolvedInput] = []
    unresolved: List[UnresolvedInput] = []
    seen_keys = set()

    def add(r: Optional[ResolvedInput], u: Optional[UnresolvedInput]):
        if r:
            if r.key not in seen_keys:
                seen_keys.add(r.key)
                resolved.append(r)
        elif u:
            unresolved.append(u)

    if image_path:
        for line in ocr_lines(image_path):
            r, u = resolve_one(index, line, locale=locale, source="ocr")
            add(r, u)
    for name in (names or []):
        r, u = resolve_one(index, name, locale=locale)
        add(r, u)
    return resolved, unresolved
