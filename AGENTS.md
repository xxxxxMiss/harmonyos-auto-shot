# AGENTS.md

本项目是 **HarmonyOS NEXT 语种资源截图自动化工具**（auto-shot）。给定一个或多个字符串资源 name（或一张含文案的截图），自动导航到展示该文案的页面、滚动到视野、截屏保存为 `<name>.png`，并输出成功/失败总结报告。

用户文档见 [README.md](./README.md)；静态扫描引擎架构见 [tools/ARCHITECTURE.md](./tools/ARCHITECTURE.md)；方案设计见 [auto-shot截图自动化方案设计.md](./auto-shot截图自动化方案设计.md)。

## 技术栈

- **Python**（3.9+）：资源解析、CLI、导航/截图执行、报告、场景推导。依赖 `PyYAML`，可选 `Pillow`（png 转码）、`rapidocr-onnxruntime`（截图 OCR 反查）。
- **Node.js + TypeScript Compiler API**：静态扫描引擎（`tools/*.mjs`）。依赖 `typescript`（npm）。
- **鸿蒙工具链**：`hdc`（设备连接）、hvigor（构建 TestApp）。

## 目录结构

```
autoshot/           Python 包（CLI + 执行层）
├── cli.py          CLI 入口（devices/scan/lookup/shoot/capture/gen-scenes/shoot-batch/demo）
├── resource_index.py    string.json 多语种解析、文案反查
├── scanner.py      AST 后端封装（调用 tools/ast_scan.mjs，regex 兜底）
├── resolve.py      输入解析：name/文案/截图 OCR → 资源 key
├── auto_scene.py   导航图 → 场景推导（BFS 路径 + 触发步道）
├── batch.py        按页面聚合批量截图
├── navigator.py    步骤执行器（launch/click/expect/swipe…）
├── shooter.py      滚动到视野 + 截屏（核心循环）
├── explorer.py     运行时探索兜底（观察→决策→动作闭环，静态 fast path 失败后接管）
├── llm.py          LLM 决策策略（P1，视觉模型判下一步，未配置自动跳过）
├── inject.py       白盒注入（P2，Want 参数直达 NavDestination 路由）
├── hdc_driver.py   hdc 封装（dumpLayout/uiInput/截屏/inject_launch）
├── layout.py       无障碍树解析 + 文本匹配降级链
└── report.py       总结报告（markdown + json）
tools/              Node 静态扫描引擎（见 ARCHITECTURE.md）
├── ast_scan.mjs    主流程（规则匹配/分类/条件路由/增量缓存）
├── predicate_eval.mjs  谓词求值器（抽象解释）
├── ts_program.mjs  Program + checker 语义分析
├── ts_loader.mjs   TS 双模式加载
└── gen_bench_fixture.mjs  基准 fixture 生成
TestApp/            可构建安装到真机的鸿蒙测试工程（覆盖全部测试形态）
fixtures/sample_app/  离线扫描测试用最小工程
tests/              单测（104 个，pytest）
```

## 关键命令

```bash
# 环境
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,image]"
npm install                                   # 阶段二 AST 后端依赖 typescript

# 测试
.venv/bin/python -m pytest tests/ -q          # 104 个测试；无 node 时 AST 组自动跳过

# 静态扫描（离线）
.venv/bin/python -m autoshot scan -p TestApp --backend ast
node tools/ast_scan.mjs TestApp /tmp/out.json   # 直接调内核

# 真机截图
.venv/bin/python -m autoshot capture -p TestApp <name...> --out-dir shots
.venv/bin/python -m autoshot capture -p TestApp --image shot.png   # 截图反查

# TestApp 构建（macOS，DevEco Studio 6.x）
cd TestApp && export DEVECO_SDK_HOME="/Applications/DevEco-Studio.app/Contents/sdk"
"/Applications/DevEco-Studio.app/Contents/tools/node/bin/node" \
  "/Applications/DevEco-Studio.app/Contents/tools/hvigor/bin/hvigorw.js" \
  --mode module -p product=default -p buildMode=debug assembleHap --analyze=false --daemon=false
```

## 真机环境的关键约束（踩过的坑）

- **hdc 版本**：PATH 里的 HMS Core 旧版 hdc（1.2.0a）在 `hidumper` 报 "sdk hdc.exe version is too low"。必须用 DevEco 自带的 3.2.0c：`/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc`。TestApp 的 `autoshot.yaml` 已配置 `hdc_path`。
- **`aa start` 必须显式 `-a EntryAbility`**，隐式启动报 10103101。
- **`aa force-stop` 后 ~2.5s 内再 start 会被限流吞掉**（场景隔离的重启式启动需留间隔）。
- **`uitest screenCap -p` 在 6.1 输出 PNG**（非 JPEG），驱动已做魔数嗅探。
- **screenCap 异步写盘有竞态**：固定路径残留旧文件会被 recv 拉回，已改为"删旧文件→截屏→轮询拉取"。
- **设备 USB 连接反复掉线**：真机验证前先 `hdc list targets` 确认设备在线。
- **隐私页禁止截屏（SECURE）**会得到黑图，驱动会显式报错。

## 关键技术决策

1. **静态扫描双模式 TS**：优先鸿蒙 fork TS（`4.9.5-r4`，原生支持 ArkTS `struct`），回退官方 TS + `struct→class` 预处理。`AUTOSHOT_TS=official` 强制官方模式。语义分析（checker）**仅 fork 模式可用**。
2. **规则匹配 R1-R6**：`$r` 字面量 / `.id` 换取 / 按名字面量 / 常量传播 / 函数摘要 / 查表。
3. **谓词求值器**（抽象解释）：替代正则模式匹配，支持同步纯函数求值（多语句函数体、函数返回数组、跨文件 import、枚举常量、对象数组、字符串/数组内建方法）。超出子集返回 unknown → 回退手写 scenes.yaml。
4. **条件路由枚举**：`if (type === 'A') push A else push B` 结合 ForEach 数据源，用求值器枚举出"点哪个列表项"。
5. **增量缓存**：指纹 = 源文件/配置 mtime+size + 扫描器自身版本。命中 0.32s（万级），改文件正确失效。
6. **场景注册表**：`scenes.yaml` 手写优先，`gen-scenes` 自动推导兜底。

## 已知边界（不要试图硬做静态分析的部分）

- **异步数据 / 服务端下发 / 副作用函数**：静态分析的理论天花板，编译期无值可求。这类走运行时兜底（`explorer.py` 观察→决策→动作闭环 + `llm.py` 视觉决策 + `inject.py` 白盒直达，已实现）。
- **并行解析**：评估结论为"不做"——checker 语义分析（57% 耗时）是全局单例不可并行，增量缓存已解决核心诉求。
- **跨 `.hsp/.har` 模块**：`$r` 引用归属用文件路径近似，可能不准。
- **按钮标签动态拼接、服务端配置路由**：自动推导会失败；运行时兜底靠"导航图边标签 + 语义锚点 + 环路检测"继续找，仍不可达才回退手写 scenes.yaml。

## 开发约定

- **Git 工作流（强制）**：
  - 每次开始修改代码前，先检查 `git status`；若工作区有未提交的改动，**先 commit**，再动手改新东西——避免把上一个 commit 搞丢、也方便出问题时 `git reset` 回退。
  - 每完成一个新功能（或一个可独立交付的修复），**自动 commit 并 push 到远程**（`origin/main`，`git@github.com:xxxxxMiss/harmonyos-auto-shot.git`），不要攒着等用户手动要求。
  - 提交前确认无敏感文件（`build-profile.json5` 含调试证书，已在 .gitignore 排除，用 `.example` 模板）。
- 修改 `tools/*.mjs` 后必须跑 `node --check` + 全量 pytest（增量缓存会因扫描器 mtime 变化自动失效，但测试前建议清缓存避免假失败）。
- 修改 TestApp 页面结构后，测试里依赖 pages/edges 的断言（`test_scanner_ast.py`、`test_nav_destination.py`）可能需要同步更新。
- `this` 在 TS AST 里是 `ThisKeyword` 不是 `Identifier`（`ts.isIdentifier(expr.expression)` 判断 `this.xxx` 是错的，用 `expr.expression.kind === ts.SyntaxKind.ThisKeyword`）。
- **跨平台（Linux/Windows/macOS）**：可执行程序路径一律走"PATH 探测 + 按平台兜底"，禁止硬编码 macOS 绝对路径。node 探测见 `scanner.py:find_node`，鸿蒙 fork TS 探测见 `tools/ts_loader.mjs:candidateForkPaths`（两者已按 `sys.platform` / `process.platform` 分支）。新增可执行程序探测时照此办理，且设备端路径（`/data/local/tmp/...`）是设备上的路径，不能与宿主 OS 混为一谈。
