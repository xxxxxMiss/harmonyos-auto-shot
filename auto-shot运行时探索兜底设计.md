# auto-shot 运行时探索兜底（Explorer）设计

## 1. 背景与目标

现有执行链路是**两段分离**的：

```
navigator.run(scene.steps)   # 静态推导的导航步骤，错一步即 StepError
    → shooter.capture_texts()  # 页内滚动循环，假设"已经在正确页面"
```

这两段的接缝是主要失败源：

- `navigator` 要求步骤**静态推导完全正确**（动态路由、文案变了、需要前置状态、服务端配置路由都会让 `bfs_path` 推导不出 → `_needs_manual_nav`）。
- `shooter` 假设**页面已经到达**（它只会滚，不会点，进不了别的页面）。

**目标（傻瓜式操作）**：只给资源 name 或含文案的截图，剩下全自动。为此补一条**运行时观察兜底**：把"跨页点击导航"和"页内滚动"合并成**一个"观察 → 决策 → 动作"闭环**，每一步都重新观察屏幕再决定下一步。静态场景降级为这个闭环的"首选路径提示"，而不是硬门槛。

## 2. 定位：降级链

`_capture_key` 从"静态两步"升级为"三级阶梯"，`CaptureResult` 增加 `method` 字段标识成败路径：

```
1. 静态场景 fast path：navigator.run + shooter.capture_texts（现状，零新增开销）
        ↓ 失败（StepError/TargetNotFound）或无场景
2. Explorer 运行时兜底：启发式（导航图引导 + 文本相似 + 滚动 + 回退）闭环
        ↓ 预算耗尽仍未果
3. LLM / 白盒注入兜底（P1/P2，本次不实现，接口预留为 Policy 子类）
        ↓
4. 失败报告：附 stuck_reason（"文案是服务端下发" / "被登录墙挡" / "页面不可达"）
```

白盒注入（Want 参数 / AppStorage）与 LLM 决策都实现为同一个 `Policy` 接口下的不同策略，
阶梯天然是：静态 → 启发式 → LLM → 注入 → 报告。本次只落地第 2 级（启发式）。

## 3. 模块结构

```
autoshot/explorer.py
  Snapshot         # 剪枝后的可见节点 + viewport + tree_signature + 历史
  Action           # Click(node) / Scroll(direction) / Back() / Stuck(reason)
  Policy(Protocol) # decide(snapshot, ...) -> Optional[Action]
  HeuristicPolicy  # 导航图引导 + 文本相似 + 弹窗处理 + 语义锚点 + 环路检测
  Explorer         # reach_and_shoot(name, candidates) -> 产物路径
```

- **剪枝（Snapshot）**：`iter_nodes` 遍历整树后，保留"带 text"或"clickable/scrollable"的节点，
  丢弃空文本纯容器，按 `(text, bounds)` 去重，上限 ~80 节点。为后续 LLM 留 token 预算、给启发式去噪。
- **Action 空间** 正好对应 `HdcDriver` 已有的 `click/swipe/key_event`，不新增驱动能力。

## 4. 主循环（Explorer）

```
for step in 0..max_explore_steps:
    tree = dump_layout(); root = parse_tree(tree); sig = tree_signature(tree)
    matches = find_by_text(root, candidates)

    content = [m for m in matches if not m.node.clickable]   # 展示内容（Text）
    if content:
        if 在视口内 → 截屏保存，返回路径
        else        → center_scroll 滚到中心，continue

    button = [m for m in matches if m.node.clickable]        # 目标是按钮文案
    if button:
        click(button[0]); record; continue

    # 无匹配 → 让策略决定下一步
    action = policy.decide(...)
    if action is None or action.kind == "stuck": break
    apply(action); record
```

**关键区分**：`find_by_text` 会同时命中"按钮文案"和"展示内容"（同一串字可能既是入口按钮又是目标内容）。
Explorer 把**非可点节点视为真实内容**优先截屏；**可点节点视为导航机会**点击继续。这是避免
"把入口按钮当成目标文案直接截了"的启发式。静态场景不受此问题影响（它知道确切目标页）。

**环路检测**：`tree_signature`（shooter 已有）作为状态指纹，`(action 签名, signature)` 去重，
保证不会无限点同一按钮；`max_explore_steps` 全局封顶（默认 40，图引导下通常几步收敛）。

## 5. 启发式决策（HeuristicPolicy）优先级

| 优先级 | 条件 | 动作 |
|---|---|---|
| 1 | 树里有权限/打扰弹窗文案（cfg.grant_texts/dismiss_texts） | 点掉 |
| 2 | 导航图 `page_adj` 某条边的 `viaText/viaItems/resolved(viaKey)` 出现在树里 | 点那个节点（**跟着地图走**） |
| 3 | 可点节点文本与目标/语义锚点相似 | 点最像的那个 |
| 4 | 有可滚动容器且方向未耗尽 | Scroll |
| 5 | 语义锚点（设置/更多/我的/登录…）在树里 | 点锚点 |
| 6 | 卡住且栈深 > 0 | Back |
| 7 | 全部穷尽 | Stuck(reason) |

第 2 条是决策器不吃盲猜的关键：`scan.page_adj` 里已存每条边的 `viaKey/viaText/viaItems`，
`auto_scene` 用它算最短路径，Explorer 改用它在**运行时**核对"这个按钮现在真的在屏幕上吗"，
把"静态推导的路径"变成"运行时可纠偏的路径"。

## 6. 成本与缓解

- **慢**：`dumpLayout` 单次 ~1.2s，40 步 ≈ 50s/key。
- **缓解**：
  1. 图引导把步数压到 2–4 次点击（多数目标静态路径直接命中，探索只在静态失败时触发）；
  2. 批内复用已到达页面（`shoot-batch` 已按页面聚合，后续可缓存探索出的页面位置）；
  3. `max_explore_steps` 预算收紧，LLM 只在最后几十分之一介入。
- **隔离性**：探索每 key 仍走"重启式启动"（force-stop + 2.5s 间隔），与现状一致。

## 7. 落地顺序

- **P0（本次）**：`explorer.py`（Snapshot/Action/HeuristicPolicy/Explorer）+ `_capture_key` 三级阶梯
  + 多页 `AppDriver`（fake）+ 单测。全部复用现有 hdc/dumpLayout/截屏，不碰被测工程。
- **P1**：`LLMPolicy`（剪枝树 + 截图 + token 预算，启发式在真实项目不够用时再做）。
- **P2**：`InjectPolicy`（Want 参数 / AppStorage 白盒注入）+ 报告 `method` 列收口。

## 8. 已知边界

- Explorer 不追踪"当前在哪个页面"，只依赖"屏幕上有什么"，因此图引导是"点任意出现在屏上的
  已知导航标签"，而非严格 BFS；配合环路检测 + 步数上限收敛。需要精确路径还原时靠静态 fast path。
- 目标文案与入口按钮文案相同的歧义，用"内容优先"启发式缓解，无法根治（本质是文本匹配的极限）。
