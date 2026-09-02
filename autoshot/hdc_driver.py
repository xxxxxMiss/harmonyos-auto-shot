"""hdc 设备驱动：封装设备管理、控件树 dump、输入注入、截屏。

⚠️ 设备侧命令在不同系统版本上偶有差异（尤其 dumpLayout 的落盘参数），
所有命令都做了 primary + fallback 两级尝试，失败时给出可读错误便于现场排查。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple

from .config import Config


class HdcError(RuntimeError):
    pass


class HdcDriver:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._screen: Optional[Tuple[int, int]] = None
        if cfg.screen_size:
            self._screen = (int(cfg.screen_size[0]), int(cfg.screen_size[1]))

    # ---- 基础 ----
    def _hdc(self, *args: str, timeout: float = 30.0, binary: bool = False) -> bytes:
        cmd = [self.cfg.hdc_path]
        if self.cfg.device_serial:
            cmd += ["-t", self.cfg.device_serial]
        cmd += list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise HdcError(
                f"找不到 hdc（{self.cfg.hdc_path}）。请安装 DevEco Command Line Tools，"
                "并在 autoshot.yaml 或 --hdc-path 指定路径") from None
        except subprocess.TimeoutExpired:
            raise HdcError(f"hdc 命令超时: {' '.join(cmd)}") from None
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise HdcError(f"hdc 失败({proc.returncode}): {' '.join(cmd)}\n{err}")
        return proc.stdout if binary else proc.stdout

    def _shell(self, *args: str, timeout: float = 30.0) -> str:
        out = self._hdc("shell", *args, timeout=timeout)
        return out.decode("utf-8", "replace")

    @classmethod
    def list_devices(cls, hdc_path: str = "hdc") -> List[str]:
        try:
            proc = subprocess.run([hdc_path, "list", "targets"], capture_output=True, timeout=10)
        except FileNotFoundError:
            raise HdcError(
                f"找不到 hdc（{hdc_path}）。请安装 DevEco Command Line Tools 并配置 PATH") from None
        if proc.returncode != 0:
            raise HdcError(proc.stderr.decode("utf-8", "replace").strip() or "hdc list targets 失败")
        out = proc.stdout.decode("utf-8", "replace")
        serials = []
        for line in out.splitlines():
            s = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()   # 去 ANSI 颜色码
            s = s.split("\t")[0].strip()
            # 序列号不含空格；[Empty] 与混入 stdout 的 W/E 日志行都排除
            if s and " " not in s and s != "[Empty]":
                serials.append(s)
        return serials

    # ---- 应用 ----
    def app_start(self, bundle: str, ability: Optional[str] = None) -> None:
        ability = ability or self.cfg.main_ability
        self._shell("aa", "start", "-b", bundle, "-a", ability)

    def open_uri(self, uri: str) -> None:
        # deeplink 拉起；个别版本参数形式不同，失败时提示人工确认
        self._shell("aa", "start", "-U", uri)

    def app_stop(self, bundle: str) -> None:
        self._shell("aa", "force-stop", bundle)

    def inject_launch(self, bundle: str, route: str, state: Optional[dict] = None) -> None:
        """白盒注入直达：带 autoshot.route / autoshot.state.* 参数启动（配合入口注入分支）。"""
        params = [f"autoshot.route={route}"]
        for k, v in (state or {}).items():
            params.append(f"autoshot.state.{k}={v}")
        self._shell("aa", "start", "-b", bundle, "-a", self.cfg.main_ability,
                    "--pi", " ".join(params))

    # ---- UI 树 ----
    def dump_layout(self) -> dict:
        """uitest dumpLayout，优先落盘再 recv，失败时直接解析 stdout。"""
        device_path = "/data/local/tmp/autoshot_layout.json"
        try:
            self._shell("uitest", "dumpLayout", "-p", device_path, timeout=20)
            time.sleep(0.15)  # 落盘异步完成
            local = os.path.join(tempfile.gettempdir(), "autoshot_layout.json")
            self._hdc("file", "recv", device_path, local, timeout=20)
            with open(local, "r", encoding="utf-8") as f:
                return json.load(f)
        except (HdcError, json.JSONDecodeError, OSError):
            pass
        out = self._shell("uitest", "dumpLayout", timeout=20)
        m = re.search(r"[\[{].*[\]}]", out, re.S)
        if not m:
            raise HdcError("dumpLayout 失败：无有效 JSON 输出。请确认设备已连接且 UI 可达")
        return json.loads(m.group(0))

    # ---- 输入注入 ----
    def click(self, x: int, y: int) -> None:
        self._shell("uitest", "uiInput", "click", str(int(x)), str(int(y)))

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._shell("uitest", "uiInput", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)))

    def key_event(self, key: str = "Back") -> None:
        self._shell("uitest", "uiInput", "keyEvent", key)

    def input_text(self, x: int, y: int, text: str) -> None:
        self._shell("uitest", "uiInput", "inputText", str(int(x)), str(int(y)), text)

    # ---- 截屏（格式随系统版本而异：实测 6.1 上 uitest screenCap -p 输出 PNG；
    #      snapshot_display 输出 JPEG。驱动做魔数嗅探，不假设格式）----
    def screenshot(self) -> bytes:
        """screenCap 异步写盘有竞态：先删旧文件，截图后轮询拉取，确保是本次产物。"""
        device_path = "/data/local/tmp/autoshot_shot.png"
        local = os.path.join(tempfile.gettempdir(), "autoshot_shot.png")
        try:
            self._shell("rm", "-f", device_path)
        except HdcError:
            pass
        self._shell("uitest", "screenCap", "-p", device_path, timeout=20)
        last_err: Optional[str] = None
        for _ in range(5):
            time.sleep(0.3)
            try:
                self._hdc("file", "recv", device_path, local, timeout=20)
                with open(local, "rb") as f:
                    data = f.read()
                if len(data) > 100 and (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8"):
                    return data
                last_err = f"文件无效（{len(data)} 字节，前 8 字节 {data[:8].hex()}）"
            except (HdcError, OSError) as e:
                last_err = str(e)
        raise HdcError(f"screenCap 未产出有效文件: {last_err}。" 
                       "部分隐私页面禁止截屏会得到黑图/空图")

    # ---- 屏幕 ----
    def display_size(self) -> Tuple[int, int]:
        if self._screen:
            return self._screen
        for attempt in (self._size_from_render_service, self._size_from_window_manager):
            try:
                size = attempt()
                if size:
                    self._screen = size
                    return size
            except HdcError:
                continue
        raise HdcError("无法获取屏幕分辨率，请在 autoshot.yaml 配置 screen_size: [w, h]")

    def _size_from_render_service(self) -> Optional[Tuple[int, int]]:
        out = self._shell("hidumper", "-s", "RenderService", "-a", "screen", timeout=15)
        m = re.search(r"(\d{3,4})\s*[xX×]\s*(\d{3,4})", out)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def _size_from_window_manager(self) -> Optional[Tuple[int, int]]:
        out = self._shell("hidumper", "-s", "WindowManagerService", "-a", "-a", timeout=15)
        m = re.search(r"(\d{3,4})\s*[xX×]\s*(\d{3,4})", out)
        return (int(m.group(1)), int(m.group(2))) if m else None
