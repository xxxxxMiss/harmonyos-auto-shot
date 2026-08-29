"""阶段三：按页面聚合的批量截图执行器。

导航到页面一次，逐 key 触发（弹窗/开关）→ 滚动截图 → 复位（Back / 再点开关），
失败不中断整批，逐 key 记录结果并输出报告。

用法：
  runner = BatchRunner(driver, cfg, scan, index, bundle)
  report = runner.run_page("pages/SettingsPage")
  # 或 runner.run_all(...)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .auto_scene import navigation_steps_to_page, page_groups_for_batch, resolve_label
from .navigator import Navigator
from .resource_index import ResourceIndex
from .scanner import ScanResult
from .shooter import Shooter, TargetNotFound, safe_name


@dataclass
class KeyResult:
    key: str
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    duration: float = 0.0


@dataclass
class PageReport:
    page: str
    navigated: bool = False
    nav_error: Optional[str] = None
    results: List[KeyResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)


class BatchRunner:
    def __init__(self, driver, cfg, scan: ScanResult, index: ResourceIndex, bundle: str):
        self.driver = driver
        self.cfg = cfg
        self.scan = scan
        self.index = index
        self.bundle = bundle
        self.navigator = Navigator(driver, cfg)
        self.shooter = Shooter(driver, cfg)

    # ---- 单个 key ----
    def _candidates(self, key: str) -> List[str]:
        return self.index.texts_for_matching(key, self.cfg.locale)

    def _run_one(self, key: str, trigger: Optional[dict], reset: Optional[dict]) -> KeyResult:
        start = time.time()
        if trigger:
            try:
                self.navigator.run([trigger])
            except Exception as e:
                return KeyResult(key, False, error=f"触发失败: {e}", duration=time.time() - start)
        try:
            path = self.shooter.capture_texts(key, self._candidates(key))
            res = KeyResult(key, True, path=path, duration=time.time() - start)
        except TargetNotFound as e:
            res = KeyResult(key, False, error=str(e), duration=time.time() - start)
        except Exception as e:
            res = KeyResult(key, False, error=f"截图异常: {e}", duration=time.time() - start)
        finally:
            if reset:
                try:
                    self.navigator.run([reset])
                except Exception:
                    pass
        return res

    # ---- 页面级 ----
    def run_page(self, page: str, keys: Optional[List[dict]] = None) -> PageReport:
        report = PageReport(page=page)
        items = keys if keys is not None else page_groups_for_batch(self.scan, self.index).get(page, [])
        nav_steps = navigation_steps_to_page(self.scan, self.index, page, self.bundle)
        if nav_steps is None:
            report.nav_error = "导航图无法到达该页面"
            return report
        try:
            self.navigator.run(nav_steps)
            report.navigated = True
        except Exception as e:
            report.nav_error = str(e)
            return report
        for item in items:
            report.results.append(self._run_one(item["key"], item.get("trigger"), item.get("reset")))
        return report

    # ---- 全部 ----
    def run_all(self, pages: Optional[List[str]] = None) -> List[PageReport]:
        groups = page_groups_for_batch(self.scan, self.index)
        targets = pages or list(groups.keys())
        reports = []
        for page in targets:
            reports.append(self.run_page(page))
        return reports


def print_report(reports: List[PageReport]) -> Dict:
    total_ok = total = 0
    summary = {"pages": [], "total": 0, "succeeded": 0, "failed": 0}
    for rep in reports:
        if rep.nav_error:
            print(f"[页] {rep.page}  导航失败: {rep.nav_error}")
            summary["pages"].append({"page": rep.page, "navigated": False, "error": rep.nav_error})
            continue
        print(f"[页] {rep.page}  已导航，{rep.succeeded}/{len(rep.results)} 成功")
        summary["pages"].append({"page": rep.page, "navigated": True,
                                 "succeeded": rep.succeeded, "total": len(rep.results)})
        for r in rep.results:
            total += 1
            total_ok += 1 if r.ok else 0
            mark = "✓" if r.ok else "✗"
            print(f"    {mark} {r.key:<24} {r.duration:5.1f}s  {r.path or r.error or ''}")
    summary["total"] = total
    summary["succeeded"] = total_ok
    summary["failed"] = total - total_ok
    return summary
