"""auto-shot CLI。

  autoshot devices                                  列出设备
  autoshot scan    -p <工程根> [--out usage.json]  资源索引 + 静态扫描 + 对账报告
  autoshot lookup  -p <工程根> --text "设置"       按显示文案反查 key（截图输入的 OCR 侧入口）
  autoshot shoot   -p <工程根> <name>              按 key 截图（导航 -> 滚动 -> 截屏）
  autoshot capture -p <工程根> <name...> [--image] 批量截图 + 总结报告
  autoshot gen-scenes -p <工程根>                  导航图自动生成场景
  autoshot shoot-batch -p <工程根>                 按页面聚合批量截图
  autoshot demo                                     无真机自检：FakeDriver 跑通滚动截图循环
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import Config, load_config


def cmd_devices(args) -> int:
    from .hdc_driver import HdcDriver, HdcError
    try:
        devices = HdcDriver.list_devices(args.hdc_path)
    except HdcError as e:
        print(f"[错误] {e}")
        return 2
    if not devices:
        print("未发现设备。请确认 USB 调试已开启，或用 --device 指定序列号")
        return 1
    for d in devices:
        print(d)
    return 0


def cmd_scan(args) -> int:
    from .resource_index import ResourceIndex
    from .scanner import build_usage_index, scan_project, scan_report
    cfg = load_config(args.project)
    index = ResourceIndex.from_project(cfg.project_root)
    scan = scan_project(cfg.project_root, backend=args.backend)
    report = scan_report(index, scan)
    payload = build_usage_index(index, scan, cfg.project_root)
    payload["report"] = report
    out = args.out or "usage_index.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"扫描后端:      {report['backend']}"
          + ("" if report["backend"] == "ast" or args.backend != "auto" else "（ast 不可用已回落，npm install 可启用）"))
    print(f"资源 key 总数: {report['total_resource_keys']}")
    print(f"有引用 key:    {report['referenced_keys']}")
    print(f"零引用 key:    {report['unreferenced_keys']}（含动态引用，见 dynamic_ref_list）")
    print(f"引用但缺资源:  {len(report['keys_not_in_resources'])} {report['keys_not_in_resources'][:10]}")
    print(f"动态引用:      {report['dynamic_refs']}")
    if report["backend"] == "ast":
        print(f"i18n 封装:     {report['i18n_accessors']} 个访问器")
        print(f"页面/导航图:   {report['pages']} 个页面，{report['keys_with_pages']} 个 key 有页面归属")
    print(f"已写入: {out}")
    return 0


def cmd_lookup(args) -> int:
    from .resource_index import ResourceIndex
    cfg = load_config(args.project)
    index = ResourceIndex.from_project(cfg.project_root)
    text = args.text
    if args.image:
        text = _ocr(args.image)
        print(f"OCR 结果: {text}")
        if not text:
            return 1
    hits = index.lookup_text(text, locale=args.locale)
    if not hits:
        print(f"未找到文案对应的 key: {text}")
        return 1
    for key, locale, value, exact in hits[:20]:
        print(f"{'[精确]' if exact else '[模糊]'} {key}  ({locale})  {value}")
    print(f"共 {len(hits)} 条候选（最多展示 20 条）")
    return 0


def _ocr(image_path: str) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("图片反查需要 OCR：pip3 install rapidocr-onnxruntime（或直接用 --text 传文案）")
        return ""
    ocr = RapidOCR()
    result, _ = ocr(image_path)
    if not result:
        return ""
    return " ".join(line[1] for line in result)


def _resolve_scene(cfg, index, key, scenes_path=None, bundle=None):
    """场景解析：手写 scenes.yaml > 自动场景(ast 导航图推导) > --bundle 直启。"""
    from .scene import Scene, SceneRegistry
    scene = None
    if scenes_path and os.path.isfile(scenes_path):
        registry = SceneRegistry.load(scenes_path)
        scene = registry.find_for_key(key) or registry.default
    if scene is None:
        scene = _auto_scene_for_key(cfg, index, key)
    if bundle and not scene:
        scene = Scene(name="cli", steps=[{"launch": bundle}])
    return scene


def _capture_key(driver, cfg, index, key, candidates, scene, navigator, shooter):
    """导航 + 滚动截图，返回 CaptureResult（异常不抛出，转为失败结果）。"""
    import time
    from .hdc_driver import HdcError
    from .shooter import TargetNotFound
    from .report import CaptureResult
    t0 = time.time()
    try:
        navigator.run(scene.steps)
        path = shooter.capture_texts(key, candidates)
        return CaptureResult(input=key, key=key, ok=True, path=path,
                             duration=time.time() - t0)
    except (HdcError, TargetNotFound) as e:
        return CaptureResult(input=key, key=key, ok=False, error=str(e),
                             duration=time.time() - t0)
    except Exception as e:  # 兜底：任何异常都转失败结果，不中断批量
        return CaptureResult(input=key, key=key, ok=False,
                             error=f"{type(e).__name__}: {e}", duration=time.time() - t0)


def cmd_shoot(args) -> int:
    from .hdc_driver import HdcDriver, HdcError
    from .navigator import Navigator
    from .resource_index import ResourceIndex
    from .shooter import Shooter
    from .config import load_config as _lc

    overrides = {
        "device_serial": args.device, "image_format": args.format,
        "output_dir": args.out_dir, "hdc_path": args.hdc_path, "locale": args.locale,
    }
    cfg = _lc(args.project, overrides)
    index = ResourceIndex.from_project(cfg.project_root)

    if args.text:
        candidates = [args.text]
    else:
        candidates = index.texts_for_matching(args.name, cfg.locale)
        if not candidates:
            print(f"[错误] 资源中不存在 key: {args.name}（或该语种无值）。"
                  f"可用 autoshot lookup 反查，或用 --text 直接给文案")
            return 1

    scenes_path = args.scenes or os.path.join(cfg.project_root, "scenes.yaml")
    scene = _resolve_scene(cfg, index, args.name, scenes_path, args.bundle)
    if scene is None:
        print(f"[错误] {args.name} 没有可用场景：请在 scenes.yaml 定义 keys 含该 key 的场景、"
              "default 场景，或用 --bundle <包名> 直接启动（或 npm install 启用自动场景推导）")
        return 1

    driver = HdcDriver(cfg)
    navigator = Navigator(driver, cfg)
    shooter = Shooter(driver, cfg)
    res = _capture_key(driver, cfg, index, args.name, candidates, scene, navigator, shooter)
    if res.ok:
        print(f"[完成] {res.path}  （{res.duration:.1f}s，场景: {scene.name}）")
        return 0
    print(f"[失败] {res.error}")
    return 1


def cmd_capture(args) -> int:
    """批量截图：多个 name 或一张截图，输出总结报告。"""
    from .hdc_driver import HdcDriver
    from .navigator import Navigator
    from .resource_index import ResourceIndex
    from .shooter import Shooter
    from .config import load_config as _lc
    from .resolve import resolve_inputs
    from .report import Report, CaptureResult

    overrides = {
        "device_serial": args.device, "image_format": args.format,
        "output_dir": args.out_dir, "hdc_path": args.hdc_path, "locale": args.locale,
    }
    cfg = _lc(args.project, overrides)
    index = ResourceIndex.from_project(cfg.project_root)

    names = list(args.names or [])
    if not names and not args.image:
        print("[错误] 需要至少一个 name，或用 --image 指定截图")
        return 1

    try:
        resolved, unresolved = resolve_inputs(index, names=names, image_path=args.image,
                                              locale=cfg.locale)
    except RuntimeError as e:
        print(f"[错误] {e}")
        return 1

    if not resolved and not unresolved:
        print("[错误] 没有可处理的输入")
        return 1

    scenes_path = args.scenes or os.path.join(cfg.project_root, "scenes.yaml")
    driver = HdcDriver(cfg)
    navigator = Navigator(driver, cfg)
    shooter = Shooter(driver, cfg)

    report = Report(project_root=cfg.project_root, resolved=resolved,
                    unresolved=unresolved, output_dir=cfg.output_dir,
                    locale=cfg.locale, image_source=args.image)

    for r in resolved:
        candidates = index.texts_for_matching(r.key, cfg.locale) or r.candidates
        scene = _resolve_scene(cfg, index, r.key, scenes_path, args.bundle)
        if scene is None:
            report.results.append(CaptureResult(input=r.input, key=r.key, ok=False,
                                 error="无可用场景（请配置 scenes.yaml 或 --bundle）"))
            continue
        report.results.append(
            _capture_key(driver, cfg, index, r.key, candidates, scene, navigator, shooter))

    # 输出报告：stdout markdown + 文件（markdown + json）
    print(report.to_markdown())
    md_path = args.report_md or os.path.join(cfg.output_dir, "capture_report.md")
    json_path = args.report_json or os.path.join(cfg.output_dir, "capture_report.json")
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    report.write_markdown(md_path)
    report.write_json(json_path)
    print(f"\n报告已写入: {md_path}\n          {json_path}")

    s = report.summary()
    return 0 if (s["failed"] == 0 and s["unresolved"] == 0) else 1


def cmd_demo(args) -> int:
    from .fake import FakeDriver
    from .shooter import Shooter, TargetNotFound
    cfg = Config(project_root=".", output_dir=os.path.join(tempfile_dir(), "demo_shots"))
    items = [f"列表第{i}项" for i in range(1, 21)]
    items[12] = "关于本应用"
    driver = FakeDriver(items, window=5)
    shooter = Shooter(driver, cfg)
    try:
        path = shooter.capture_texts("about_app", ["关于本应用"])
    except TargetNotFound as e:
        print(f"[demo 失败] {e}")
        return 1
    print(f"[demo 通过] 目标在第 13 项（懒加载初始不可见）")
    print(f"  滚动次数: {len(driver.swipes)}  截屏次数: {len(driver.shots)}")
    print(f"  截屏时视口内容: {driver.visible_texts()}")
    print(f"  产物: {path}")
    return 0


def _auto_scene_for_key(cfg, index, key):
    """用 ast 导航图自动推导该 key 的场景（无 ast/node 环境返回 None）。"""
    from .auto_scene import bundle_name, generate
    from .scene import Scene, SceneRegistry
    from .scanner import ast_backend_available, scan_project
    if not ast_backend_available():
        return None
    try:
        scan = scan_project(cfg.project_root, backend="ast")
    except Exception:
        return None
    data = generate(scan, index, cfg.project_root)
    if not data.get("scenes") and not bundle_name(cfg.project_root):
        return None
    # 直接用 generate 结果构造一个临时注册表，避免写文件
    tmp = os.path.join(cfg.project_root, ".autoshot_scenes_tmp.yaml")
    try:
        import yaml
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        reg = SceneRegistry.load(tmp)
        return reg.find_for_key(key) or reg.default
    except Exception:
        return None
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)


def cmd_gen_scenes(args) -> int:
    from .resource_index import ResourceIndex
    from .scanner import ast_backend_available, scan_project
    from .auto_scene import write_scenes_yaml
    if not ast_backend_available():
        print("[错误] 自动场景生成需要 ast 后端：请先 npm install（安装 typescript）并确保 node 可用")
        return 2
    index = ResourceIndex.from_project(os.path.abspath(args.project))
    try:
        scan = scan_project(os.path.abspath(args.project), backend="ast")
    except Exception as e:
        print(f"[错误] 扫描失败: {e}")
        return 1
    out = args.out or os.path.join(os.path.abspath(args.project), "scenes.auto.yaml")
    write_scenes_yaml(scan, index, os.path.abspath(args.project), out)
    print(f"已生成 {out}（共 {len(scan.key_pages)} 个 key 有页面归属）")
    print("提示：可与手写 scenes.yaml 合并；shoot 未命中手写场景时也会自动推导")
    return 0


def cmd_shoot_batch(args) -> int:
    import json as _json
    from .hdc_driver import HdcDriver, HdcError
    from .resource_index import ResourceIndex
    from .scanner import ast_backend_available, scan_project
    from .batch import BatchRunner, print_report
    from .config import load_config as _lc
    from .auto_scene import bundle_name

    overrides = {"device_serial": args.device, "image_format": args.format,
                 "output_dir": args.out_dir, "hdc_path": args.hdc_path, "locale": args.locale}
    cfg = _lc(args.project, overrides)
    if not ast_backend_available():
        print("[错误] shoot-batch 需要 ast 后端（导航图 + 触发推导）")
        return 2
    index = ResourceIndex.from_project(cfg.project_root)
    try:
        scan = scan_project(cfg.project_root, backend="ast")
    except Exception as e:
        print(f"[错误] 扫描失败: {e}")
        return 1
    bundle = args.bundle or bundle_name(cfg.project_root)
    if not bundle:
        print("[错误] 无法确定 bundleName：请 --bundle 指定，或检查 AppScope/app.json5")
        return 1
    try:
        driver = HdcDriver(cfg)
        runner = BatchRunner(driver, cfg, scan, index, bundle)
        reports = runner.run_all(args.pages)
    except HdcError as e:
        print(f"[失败] {e}")
        return 1
    summary = print_report(reports)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            _json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"报告已写入: {args.json}")
    return 0 if summary["failed"] == 0 else 1


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autoshot", description="HarmonyOS NEXT 语种资源截图自动化")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("-p", "--project", default=".", help="HarmonyOS 工程根目录")
        sp.add_argument("--hdc-path", default="hdc", help="hdc 可执行文件路径")

    d = sub.add_parser("devices", help="列出已连接设备")
    d.add_argument("--hdc-path", default="hdc")
    d.set_defaults(func=cmd_devices)

    s = sub.add_parser("scan", help="资源索引 + 静态扫描 + 对账")
    common(s)
    s.add_argument("--out", default=None, help="输出 json 路径，默认 usage_index.json")
    s.add_argument("--backend", choices=["auto", "ast", "regex"], default="auto",
                   help="扫描后端：ast（TS AST，支持 R4/R5/分类/导航图）/ regex（MVP 兜底）/ auto")
    s.set_defaults(func=cmd_scan)

    l = sub.add_parser("lookup", help="按显示文案反查资源 key")
    common(l)
    l.add_argument("--text", help="显示文案")
    l.add_argument("--image", help="含文案的截图路径（需安装 OCR 组件）")
    l.add_argument("--locale", default=None, help="限定语种，如 zh_CN")
    l.set_defaults(func=cmd_lookup)

    sh = sub.add_parser("shoot", help="按资源 key 截图")
    common(sh)
    sh.add_argument("name", help="资源 key（string.json 的 name）")
    sh.add_argument("--text", help="跳过资源解析，直接用该文案匹配")
    sh.add_argument("--locale", default=None, help="限定语种")
    sh.add_argument("--device", default=None, help="设备序列号")
    sh.add_argument("--format", choices=["png", "jpeg"], default=None, help="输出格式，默认 png")
    sh.add_argument("--out-dir", default=None, help="截图输出目录，默认 shots/")
    sh.add_argument("--scenes", default=None, help="scenes.yaml 路径，默认 <工程根>/scenes.yaml")
    sh.add_argument("--bundle", default=None, help="无场景时直接启动的包名")
    sh.set_defaults(func=cmd_shoot)

    c = sub.add_parser("capture", help="批量截图（多个 name 或一张截图），输出总结报告")
    common(c)
    c.add_argument("names", nargs="*", help="一个或多个资源 key（或显示文案，自动反查）")
    c.add_argument("--image", default=None, help="含文案的截图路径，OCR 反查多个 key（需 OCR 组件）")
    c.add_argument("--locale", default=None, help="限定语种")
    c.add_argument("--device", default=None, help="设备序列号")
    c.add_argument("--format", choices=["png", "jpeg"], default=None, help="输出格式，默认 png")
    c.add_argument("--out-dir", default=None, help="截图与报告输出目录，默认 shots/")
    c.add_argument("--scenes", default=None, help="scenes.yaml 路径，默认 <工程根>/scenes.yaml")
    c.add_argument("--bundle", default=None, help="无场景时直接启动的包名")
    c.add_argument("--report-md", default=None, help="markdown 报告路径（默认 <out-dir>/capture_report.md）")
    c.add_argument("--report-json", default=None, help="json 报告路径（默认 <out-dir>/capture_report.json）")
    c.set_defaults(func=cmd_capture)

    dm = sub.add_parser("demo", help="无真机自检：FakeDriver 滚动截图循环")
    dm.set_defaults(func=cmd_demo)

    g = sub.add_parser("gen-scenes", help="用导航图自动生成场景（scenes.auto.yaml）")
    common(g)
    g.add_argument("--out", default=None, help="输出路径，默认 <工程根>/scenes.auto.yaml")
    g.set_defaults(func=cmd_gen_scenes)

    b = sub.add_parser("shoot-batch", help="按页面聚合批量截图（导航一次，逐 key 触发+截图）")
    common(b)
    b.add_argument("--pages", nargs="*", default=None, help="限定页面（如 pages/SettingsPage），缺省全部")
    b.add_argument("--device", default=None)
    b.add_argument("--format", choices=["png", "jpeg"], default=None)
    b.add_argument("--out-dir", default=None)
    b.add_argument("--locale", default=None)
    b.add_argument("--bundle", default=None, help="包名（缺省读 AppScope/app.json5）")
    b.add_argument("--json", default=None, help="报告输出 json 路径")
    b.set_defaults(func=cmd_shoot_batch)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
