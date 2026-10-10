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

# 穿戴（圆屏）真机验证：OffsetListPage 的"有效底部"行为，产物在 watch_verify/
.venv/bin/python tools/verify_wearable_offset.py --out-dir watch_verify

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
- **`contentStartOffset` + `contentEndOffset` 之和不能超过 List 显示区高度**：超过时框架把两者**静默置 0**（文档："超过 List 内容区长度后置 0"，实测比对的是显示区高度）。手表圆屏列表区只有 211vp，`OffsetListPage` 原先取 `12+200=212` 直接失效——`contentEndOffset` 完全不产生可滚动空间、末条永远贴着列表底边收不回来；改成 `12+160=172` 才正常。手机参数 `24+520=544` 远小于手机列表高度，不受影响。
- **在 `onScrollStop` 里调 `scroller.scrollTo` 会被框架补发一次 `onScrollStop`**（实测 ~1ms 后），此时偏移还停在原处。挡这个补发事件**不能只看时间窗**：`CORRECTION_GUARD_MS`(150ms) 之内也可能是用户手势真正结束的那次事件，被吞掉就意味着"这次划到底完全没结算"（真机表现：有时候划到底不回弹）。`OffsetListPage` 现在的判据是"时间窗内 **且** 偏移相对发起修正时一点没动（`correctionFromOffset`）"，两个条件同时满足才丢弃。
- **`getRectangleById()` 的坐标首帧不可信**：布局稳定前量到的按钮位置实测偏高 22vp，缓存下来会永久偏高。要作为几何锚点就得在每次滚动停止后重新量。
- **`getRectangleById()` 对已经滚出显示区的列表项照样返回布局矩形**：实测能给出屏幕下方 1000px 之外的坐标，只看 `size.height > 0` 会把"早就不在屏上"误判成"还在屏上"。需要"是否可见"时必须显式算 `item 矩形 ∩ list 矩形`（`OffsetListPage.measureLastItemBottomPx` 就是这么做的），否则"末条在屏上就拉回安全线"的规则会把翻到列表中部的用户也拽回底部。
- **数据变更后立刻用 `getRectangleById` 量"最后一条"会量到旧布局**：`ForEach` 重渲染之前，原来那条仍带着 `LAST_ITEM_ID`（还停在安全线上），量出来的位置是插入前的。`OffsetListPage` 从列表底部追加数据时踩过这个竞态——用固定延时（120ms）重判提示条，真机 3 轮里漏 1 次。结论：**数据变更引起的状态迁移不要依赖"变更后立刻测量"**，改成由变更本身直接决定（`appendAtBottom` 里直接让提示条让位），再用一个保护窗（`APPEND_LAYOUT_GUARD_MS`）挡掉这期间基于旧布局的解锁判定。
- **回弹是硬约束，不要"放弃修正"**：`OffsetListPage` 的契约是"末条只要还在列表显示区内，就必须停在安全线（按钮顶边上方 34vp）"。原先"上一次修正的目标没落位就放弃本次"的分支会造成永久停在框架真正底部（末条贴屏底、状态误报"已到滚动极限"）——快速连划时 pendingTarget 会被后一次手势改写，所以**每次停下都要按当前实测重新结算**；同时"是否强制回弹"不能只信 `onReachEnd`/`isAtEnd()`（快速连划会漏报），要由每帧实测的末条位置自证。想离开末尾区域只能用一次大幅度滑动把末条滑出显示区。
- **圆屏手势坐标要避开悬浮层**：466x466 圆屏上状态条(0~44px)、提示条(294~334px)、底部按钮(346~406px) 都会截走触摸，`uitest uiInput` 划动要落在中间"纯列表带"(约 y 70~280) 才划得动列表。
- **Button(Capsule) 默认左右内边距 16vp，一排塞三个按钮就会把文案挤到折行/裁切**：穿戴参数 `buttonWidth='66%'`(154vp) 一排三个，每个仅约 46vp，减掉默认内边距只剩约 14vp 文案区。真机表现：文案折成两行，30vp 高的按钮把第二行裁掉（实测按钮 146px 宽、白字只占中间 68px，第二行 glyph 画到按钮底边之外，两个按钮都只剩 3 个字 + 省略号）。修法：① 文案改短；② 显式 `.padding({left:2,right:2})`；③ `.labelStyle({maxLines:1})`；④ 穿戴按钮字号 10→9vp。`tools/verify_wearable_offset.py` 第 15 项为此改成**像素复核**（白字像素必须单行、上下各留 4px、左右各留 2px、宽度 ≥ 理论宽度 75%），以后再改文案/字号会被它挡住。
- **列表项内容长高时，屏幕上是"底边往下沉"而不是"顶边往上走"**：末条在自己那条的内容坐标系里顶边不变（上面的项没动），height 变大只把它自己的底边往下推（撑在下面的是 `contentEndOffset` 那 160vp 垫底）；滚动偏移不变时末条**顶边**停在原处、底边一路下沉。于是"内容持续增长"和"用户往回滑离开有效底部"在几何上是同一件事，都只能等下一次 `onScrollStop` 结算——**数据变更不产生滚动事件**，所以流式写入期间漂移会一直累积、期间没有任何自动回弹，必须靠用户滚动停止或"停止写入"按钮主动触发一次结算。
- **稳定 key 只解决「别整条重建」，还要 @Observed + @ObjectLink 才刷新**：`OffsetListPage` 每 150ms 改写同一条的文本。若沿用「文本当 key」，每次写入都被判成新数据、整条 ListItem 重建（`LAST_ITEM_ID` 会飘、`getRectangleById` 量到旧布局）；但**光把 key 换成稳定 id 又掉进另一个坑**——ForEach 键值不变时框架**复用旧组件、不再执行 itemGenerator**，写在 itemGenerator 里的 `Text(item.text)` 就再也不刷新。真机实测：文本已写到 492 字，控件树里那条仍停在 15 字、项高一直 40vp，「流式文本」看起来根本没动静。正解是 `@Observed class OffsetItem` + 把行拆成 `@Component struct OffsetRow { @ObjectLink item }`，写入时**改同一个对象的属性**（`this.items[idx].text += chunk`，不换数组、不换对象），框架只通知这一行重渲染——顺带把父组件和带几十项的 ForEach 从「每 150ms 重跑一次」里解放出来（之前这种重跑会让 `dumpLayout` 都变得不可靠）。行的长高靠 `.constraintSize({minHeight:40})`（普通项由 minHeight 兜住，流式那一条随文本长高），`growing` 标志放在被观察的数据对象上、停写后不回收（否则停止瞬间项高缩回 40vp，屏上内容突然消失）。
- **`uitest dumpLayout` 会偶发返回残缺树，不能当成「节点消失」**：这块表上一次 dump 要 1.3~3.6s；UI 频繁重渲染时实测抓到过**一条节点都没有**的树（截图里画面完全正常，连一直显示在屏上的底部按钮都读不到）。后果是「提示条节点不见了 = 让位生效」这类判据会**假通过**，而「量不到末条」又会假失败。`tools/verify_wearable_offset.py` 的 `tree(require=...)` 因此改成：把永远在屏上的常量 id（列表 + 三个底部按钮）作为「这次 dump 是否完整」的判据，不完整就重取（最多 4 次）；凡是用「节点消失」表达状态的检查（提示条让位、按钮文案）都必须先过这道完整性判据。
- **末条长高后控件树给的矩形会被窗口裁剪**：流式写入把末条撑到超过屏高时，`getRectangleById` 量到的底边读数封顶在 466px（= 屏高），所以「漂移」能观察到的最大值只有 466-278=188px。验证脚本里 `STREAM_DRIFT_MAX_PX` 只能用来做「没跑到天上去」的粗校验，真正有判据意义的是**下界**（漂移 < -2px 说明末条还压在框架真正底部、回弹没做）。
- **流式写入期间回弹动画永远落不了地，必须改成瞬移**：内容持续增长时列表被框架钉在**真正底部**（`isAtEnd()` 一直为 true），下一次内容变更会把还在飞的 `scrollTo(animation)` 取消并把偏移重新钉回末尾，于是修正永远不生效、还会以 **4Hz 反复重发**。真机日志（每 250ms 一条）：`onScrollStop: reachedEnd=true -> force=true` / `settle: 发起修正 delta=-71.6 target=1359.0 animate=true` / `measure: itemBottom=134.8 safe=278.0 overflow=-71.62` —— 末条始终停在安全线上方 71vp，就是用户看到的「划到底不回弹」，同时把 hilog 刷爆、把 `dumpLayout` 拖到不可靠。修法：`OffsetListPage.settleAtEffectiveBottom` 里`const useAnimation = animate && !this.streaming` —— 流式写入期间用 `animation: false` 瞬移，下一帧就落地，落地后偏移已离开末尾（不再 isAtEnd），后续增长不会再把它拽回真底；非流式场景照旧用 `BOUNCE_DURATION` 的动画回弹。写入节奏取 350ms/2 字（约 5~10 字/s），让「内容还在长」覆盖整段验证窗口，停止写入那一下才一定还有落差可以回弹。
- **USB 掉线时 `dumpLayout` / `screenCap` 会「成功」返回陈旧文件**：掉线后 `uitest dumpLayout` 和 `screenCap` 都不报错，返回的是设备上**上一次**留下的 JSON/PNG —— 实测 19 张截图逐字节相同、控件树冻结在第 7 步那一刻（末条都不在树里、按钮坐标却还是对的），后面每一步都以「量不到」连锁失败，完全看不出真因（一度误判成「流式把回弹打断了」）。`tools/verify_wearable_offset.py` 现在在每个阶段边界 + `measure()` 里校验 `hdc list targets`，掉线抛 `DeviceOfflineError`（**刻意不继承 RuntimeError**，否则会被各处 `except RuntimeError` 降级成「这一次量不到」）并中止整轮。

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
