# auto-shot — HarmonyOS NEXT 语种资源截图自动化

给定资源 `name`（或含文案的截图），自动导航到对应页面、把目标滚入视野、截屏保存为 `<name>.png/.jpeg`。
方案背景与完整设计见 [auto-shot截图自动化方案设计.md](./auto-shot截图自动化方案设计.md)。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,image]"
npm install                                   # 阶段二 AST 后端依赖（typescript）
.venv/bin/python -m pytest tests/ -q          # 64 个离线单测（无 node 时 AST 组自动跳过）
.venv/bin/python -m autoshot demo             # 无真机自检：滚动截图核心循环
```

> Windows 下 venv 脚本在 `.venv\Scripts\`，命令相应改为
> `.venv\Scripts\pip install -e ".[dev,image]"` 与 `.venv\Scripts\python -m ...`。

## 跨平台说明（Linux / macOS / Windows）

工具本体是纯 Python + Node，跨平台运行；与平台相关的只有两处**可执行程序探测**：

- **node**：优先 `PATH`（`shutil.which`），找不到时按平台兜底找 DevEco Studio 自带
  node（macOS `/Applications/DevEco-Studio.app/...`、Windows `Program Files\Huawei\DevEco Studio\...`、
  Linux `~/DevEco-Studio/...` 或 `/opt/...`）。见 `autoshot/scanner.py:find_node`。
- **鸿蒙 fork TypeScript**（`tools/ts_loader.mjs`）：优先 `DEVECO_SDK_HOME` 环境变量，
  否则按平台兜底找 DevEco SDK 内 `ets-loader/node_modules/typescript`；找不到则回退项目
  `node_modules/typescript`（官方版 + struct→class 预处理），功能等价。

真机截图需要 `hdc`：Linux/Windows 上若 `hdc` 不在 `PATH`，用 `--hdc-path` 或
`autoshot.yaml` 的 `hdc_path` 指定（Windows 下是 `hdc.exe`）。设备端命令（`aa`/`uitest`/
`hidumper`）运行在 HarmonyOS 设备上，与宿主 OS 无关。

## 静态扫描性能

`tools/ast_scan.mjs` 是 AST 扫描内核（TypeScript Compiler API）。

**typescript 双模式（`tools/ts_loader.mjs`）**：
- 默认优先加载 **鸿蒙官方 fork 的 TypeScript**（DevEco SDK 内 `ets-loader/node_modules/typescript`，
  版本 `4.9.5-r4`），原生支持 ArkTS 的 `struct` 关键字（`SyntaxKind.StructDeclaration`），
  parse 错误从官方 TS 的 110 个降到 40 个（剩余是 `$r` 资源引用，AST 完整保留）。
- 找不到 SDK 时回退项目 node_modules 的**官方 TypeScript** + `struct→class` 预处理。
- `AUTOSHOT_TS=official` 可强制走官方模式（CI 无 DevEco 环境）。
- 实测：两种模式对 TestApp 扫描输出**逐项一致**（41 usages / 10 edges / 9 pages）。

**语义分析（`tools/ts_program.mjs`，P2 阶段）**：
- fork 模式下构建 TS `Program` + `checker`，用 `getSymbolAtLocation` + `getAliasedSymbol`
  做**跨文件符号解析**（import alias 解开），用符号路径做**枚举成员常量求值**
  （如 `ItemLevel.Premium = 3`，`checker.getConstantValue` 对枚举成员无效，需走 EnumMember.initializer）。
- `.ets` 后缀模块解析通过自定义 `resolveModuleNames` 实现。
- 性能：千文件语义分析 663ms（纯语法 334ms 的 2 倍），绝对值可接受。
- **仅 fork 模式可用**：官方模式（无 ArkTS 语义支持）降级为纯语法扫描，枚举/跨文件
  常量符号解析不可用（场景4 枚举路由判断在官方模式回退为无 viaItems，需手写兜底）。

1001 文件 / 4.5 万行基准（`node tools/gen_bench_fixture.mjs /tmp/bench 1000` 生成）：

- 优化前 ~0.87s，优化后 ~0.75s；其中 `preprocess`（struct→class）由逐字符状态机改为
  "掩码保护+正则替换"，单步 220ms → 12ms（18×）。fork 模式下无需 preprocess。
- 剩余耗时大头是 TS 解析本身（每文件独立 `ts.createSourceFile`，~0.4s/千文件），
  属 Compiler API 固有成本；遍历+规则匹配仅 ~40ms（非瓶颈）。
- 已做：`resolveImport` 结果缓存、`matchAccessor` Map 索引、R5 主遍历廉价预判、
  `owningPages` memoize + 索引推进（替代 `shift()`）、classify 廉价判断（避免 getText 文本匹配）。

**万级文件性能（P5）**：

- 10001 文件实测：首次全量扫描 **7.2s**，其中 createProgram 语义分析占 4.1s（57%，
  checker 全局单例，**不可并行**），主遍历约 3s。
- **增量缓存（已实现）**：基于源文件 + 配置文件 mtime/size 指纹 + **扫描器自身版本**（
  ast_scan/predicate_eval/ts_program/ts_loader 的 mtime，避免工具升级后旧缓存返回过时结果）。
  命中缓存时 **0.32s**（快 22 倍）；改一个文件后正确失效重扫。
- **并行解析评估结论：不做**。语义分析（57% 大头）是 checker 单例不可并行；剩余可并行的
  语法遍历仅 ~3s，worker 通信开销 + checker 无法跨 worker 共享，收益低复杂度高。
  对"老项目反复扫描"场景，增量缓存已解决核心诉求（日常 0.32s）。

## 命令

```bash
# 列出设备（需 DevEco Command Line Tools 的 hdc；路径可用 --hdc-path 指定）
autoshot devices

# 离线：资源索引 + 静态扫描（$r / resourceManager 两种引用）+ 双向对账
# 后端：ast（默认，TS AST：R4 常量传播/R5 i18n 封装/上下文分类/导航图）| regex（无 node 时兜底）
autoshot scan -p /path/to/project --backend ast   # 产出 usage_index.json

# 用导航图自动生成场景（BFS 路径 + 触发按钮/状态开关步道）→ scenes.auto.yaml
autoshot gen-scenes -p /path/to/project

# 按页面聚合批量截图：导航一次，逐 key 触发+复位+截图，输出报告
autoshot shoot-batch -p /path/to/project --out-dir shots --json report.json
autoshot shoot-batch -p /path/to/project --pages pages/SettingsPage   # 限定页面

# 批量截图（多个 name 或一张截图）+ 总结报告（capture 是 skill 的主入口）
autoshot capture -p /path/to/project settings_about network_error
autoshot capture -p /path/to/project --image shot.png
# 报告：<out-dir>/capture_report.md（+ .json），含待处理项/处理结果/汇总三部分

# 按显示文案（或截图，需 [ocr] 组件）反查资源 key
autoshot lookup -p /path/to/project --text "设置"
autoshot lookup -p /path/to/project --image shot.png

# 按资源 key 截图：场景导航 -> 滚动到视野 -> 视口校验 -> 截屏
autoshot shoot -p /path/to/project settings_about \
    --bundle com.example.app \               # 或在 scenes.yaml 中定义场景
    --locale zh_CN --format png --out-dir shots
```

也可 `python -m autoshot ...` 免安装直接运行。

## 工程结构

| 模块 | 职责 | 对应设计文档 |
|---|---|---|
| `autoshot/resource_index.py` | string.json 多语种解析、文案反查 | A 层 |
| `autoshot/resolve.py` | 输入解析：key / 文案 / 截图 OCR 行 → 资源 key | A 层 |
| `autoshot/report.py` | 总结报告模型 + markdown/json 渲染 | E 层 |
| `autoshot/scanner.py` + `tools/ast_scan.mjs` | 静态扫描双后端：ast（TS Compiler API + struct→class 预处理，R1-R5 + 查表 + 分类 + 导航图）/ regex（字面量兜底） | A 层 |
| `autoshot/auto_scene.py` | 导航图→自动场景推导（BFS 路径 + 触发按钮/状态开关步道） | B 层 |
| `autoshot/batch.py` | 按页面聚合批量截图（导航一次，逐 key 触发+复位+截图） | D/E 层 |
| `autoshot/scene.py` + `scenes.yaml` | 场景注册表（key→导航步骤） | B/D-L1 层 |
| `autoshot/navigator.py` | 步骤执行（launch/click/expect/swipe…）、权限弹窗自动处理 | B 层 |
| `autoshot/hdc_driver.py` | hdc 封装：dumpLayout、uiInput 注入、截屏 | C 层 |
| `autoshot/layout.py` | 无障碍树解析、文本匹配降级链（精确→占位符正则→包含）、视口判定 | C 层 |
| `autoshot/shooter.py` | 滚动到视野主循环 + idle 稳定 + 截屏保存 | C/E 层 |
| `autoshot/fake.py` | FakeDriver：无真机模拟懒加载滚动（测试/demo） | — |

`fixtures/sample_app` 是内置的示例 HarmonyOS 工程（含 $r、resourceManager、
条件渲染、动态引用四种形态），离线命令都可用它验证。

## 场景注册表（scenes.yaml）

放在工程根目录，步骤语法：

```yaml
default:
  steps: [{launch: com.example.app}]

scenes:
  settings:                       # 也支持列表形式 - name: settings
    keys: [settings_title, network_error]
    steps:
      - launch: com.example.app
      - expect: text=首页
      - click: text=设置
      - swipe: "0.5,0.8 -> 0.5,0.4"
    conditions:                   # 二期故障注入预留
      network_error: network_broken
```

## 项目配置（autoshot.yaml，可选，工程根目录）

```yaml
device_serial: "127.0.0.1:5555"   # 多设备时指定
hdc_path: hdc
screen_size: [1080, 2340]         # 自动探测失败时手动指定
image_format: png                 # 注意：设备截屏恒为 JPEG，png 由 Pillow 转码
viewport_margin_top: 96           # 避开吸顶栏
viewport_margin_bottom: 96        # 避开底部 tab
dismiss_texts: ["我知道了", "暂不升级"]  # 自动点掉的打扰弹窗
```

## TestApp：内置真机验证工程

`TestApp/` 是一个可构建、可安装到真机的 HarmonyOS NEXT 测试工程（bundleName
`com.example.hualitd`，复用本机既有调试签名），覆盖全部测试形态：

| 页面 | 覆盖形态 |
|---|---|
| `pages/Index` | `$r` 直引、`resourceManager.getStringByNameSync` 按名取值、`%d` 占位符格式化 |
| `pages/SettingsPage` | 点击触发 AlertDialog / Toast（瞬态 UI） |
| `pages/ListPage` | 30 项长列表，第 21 项为目标文案（懒加载滚动定位） |
| `pages/StatePage` | 条件渲染：断网错误提示 / 空列表态（需点击触发） |
| `pages/DetailPage` | router 方案：同路由不同参数（手写场景兜底） |
| `pages/NavEntryPage` + `route_map.json` | **NavDestination 方案**：同路由不同参数（自动推导）+ 不同条件进不同路由（手写兜底） |

构建与安装（macOS，DevEco Studio 6.x）：

```bash
cd TestApp
export DEVECO_SDK_HOME="/Applications/DevEco-Studio.app/Contents/sdk"
"/Applications/DevEco-Studio.app/Contents/tools/node/bin/node" \
  "/Applications/DevEco-Studio.app/Contents/tools/hvigor/bin/hvigorw.js" \
  --mode module -p product=default -p buildMode=debug assembleHap --analyze=false --daemon=false
hdc install -r entry/build/default/outputs/default/entry-default-signed.hap
```

真机截图（场景表见 `TestApp/scenes.yaml`，已在 HarmonyOS 6.1 / API 24 真机验证）：

```bash
.venv/bin/python -m autoshot shoot -p TestApp main_title --out-dir shots
.venv/bin/python -m autoshot shoot -p TestApp list_target --out-dir shots    # 懒加载滚动
.venv/bin/python -m autoshot shoot -p TestApp network_error --out-dir shots  # 条件渲染
.venv/bin/python -m autoshot shoot -p TestApp dialog_message --out-dir shots # 点击触发弹窗
.venv/bin/python -m autoshot shoot -p TestApp toast_message --out-dir shots  # 瞬态 Toast
```

## 真机实测结论（HarmonyOS 6.1 / BRA-AL00 / API 24）

- `uitest screenCap -p <path>` 在 6.1 上输出 **PNG**（非 JPEG）；驱动已做魔数嗅探按需转码。
- **screenCap 异步写盘有竞态**：固定路径残留旧文件会被 `file recv` 拉回（曾导致连续截图拿到上一张）。驱动已改为"先删旧文件→截屏→轮询拉取校验魔数"。
- `aa start` **必须显式 `-a EntryAbility`**，隐式启动（只给 `-b`）报 10103101 并弹"暂无可用打开方式"。
- `aa force-stop` 后 **~2.5s 内**再 `aa start` 会被系统限流吞掉，场景隔离的重启式启动需留足间隔。
- 瞬态 UI（Toast）存活 ~8s：截图链路不能走长 wait_idle+二次确认，当前策略是"命中且在视口内→短稳定→立即截图"。
- dumpLayout 单次 ~1.2s，是主要耗时项；一张截图全流程 8~27s（含重启式启动 ~5s、滚动寻找）。

## 已知边界

- **NavDestination（route_map.json 方案，已支持）**：`discoverPages` 已解析
  module.json5 的 `routerMap` 字段（route_map.json 的 name/pageSourceFile）；导航边提取
  支持 `NavPathStack.pushPathByName('X', param)` / `pushPath`，param 支持对象字面量
  （含 `as` 类型断言剥离）+ 一层变量追踪。
  - **同路由不同参数（自动推导 ✓）**：`if (this.index === 2)` + 边 param `{index: idx}` +
    列表项模板串 `` `详情项${idx}` `` → 自动推导"点详情项2"。真机验证 nav_detail_0/1/2 均成功。
  - **不同条件进不同路由（自动推导 ✓）**：`if (type === 'A') push A else push B`，结合
    ForEach 数据源 `this.routes = ['A','B','A','B']` 枚举出满足条件的列表项文案
    （routeCond + forEach 上下文 + viaItems）。真机验证 nav_route_a/b 均成功，无需手写。
  - **谓词求值器（`tools/predicate_eval.mjs`）**：条件路由判定从正则匹配升级为抽象解释，
    支持同步可求值的条件形态——等值/比较/逻辑运算、返回布尔值的函数（**多语句函数体**：
    if/else、多 return、局部变量、有界 for 循环）、数据源为**函数返回数组**
    （含 `arr.push()` 构造）、**跨文件 import** 的函数定位、列表项为**对象数组**
    （`item.kind` 成员访问）。超出子集的（递归/无界循环/真实异步数据）→ 回退手写 scenes.yaml。
  - **动态路由名 + 三元文案（P3-b）**：`pushPathByName(cond ? 'A' : 'B')` 拆成真假两条边；
    `Text(cond ? '文案A' : '文案B')` 按条件值选文案生成 viaItems。
  - **求值器方法（P0）**：算术 `* / %`；字符串 `startsWith/endsWith/includes/indexOf` 等 12 个；
    数组 `some/every/find/filter/map` 等 13 个（含箭头回调）。
- **同路由不同参数（旧 router 方案）**：`router.pushUrl({url, params})` 只记录 url 不记录
  params，且列表项三元表达式提取不到触发标签；这类 key 需手写 scenes.yaml
  （见 `detail_*` 场景，`tests/test_same_route_diff_params.py`）。
- **静态扫描（阶段二 ast 后端已就绪）**：R4 常量传播（1 层间接 + 跨文件 import）、R5 函数摘要
  （识别"以参数转发调用 resourceManager 按名 API"的封装并回溯调用点）、上下文分类
  （visible/conditional[含条件源码]/click/runtime）、导航图（module.json5 pages + routerMap +
  pushUrl/pushPathByName 边 + import 归属 + BFS 深度）均已实现。残余边界：R6 动态查表
  （`MAP[变量]`）仍标记 dynamic 需人工/LLM 兜底；`$r` 引用跨 `.hsp/.har` 的归属用文件路径近似。
- **自动场景（阶段三）**：导航边/弹窗/状态开关的触发标签由 onClick 回调向上爬取，能覆盖
  `Button($r(...)).width(...).onClick(...)`、`if (this.x)` 开关、`if (type === 'A')` 条件路由
  （ForEach 数据枚举）三种形态；按钮标签由变量动态拼接、服务端配置的路由、跨 `.hsp/.har`
  边仍可能推导不出，会产出 `_needs_manual_nav` 标记或跳过，需手写 scenes.yaml 兜底
  （手写优先于自动）。
- **条件 UI（断网提示等）**：MVP 靠 UI 上的"模拟断网"开关按钮点击（状态开关步道）；
  真实网络故障注入（代理 L2）与白盒状态注入（L3）为后续阶段。
- **设备侧命令兼容性**：已在 HarmonyOS 6.1 真机验证（见上方实测结论）；其他系统版本
  若 `uitest dumpLayout/screenCap` 报错，优先核对参数差异。
- **hdc 版本**：PATH 里的 HMS Core 附带的旧版 hdc（1.2.0a）在 hidumper 时报
  "sdk hdc.exe version is too low"，无法获取屏幕分辨率。需用 DevEco 自带的新版
  （3.2.0c，`/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc`），
  通过 `--hdc-path` 或工程根的 `autoshot.yaml` 的 `hdc_path` 指定（TestApp 已配置）。
- 隐私页禁止截屏（SECURE）会得到黑图，驱动会显式报错。
