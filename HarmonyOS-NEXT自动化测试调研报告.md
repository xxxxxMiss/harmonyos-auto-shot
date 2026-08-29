# HarmonyOS NEXT 自动化测试方案调研报告

> 调研时间：2026-08 ｜ 信息来源：华为官方文档、OpenHarmony 社区、GitHub、TesterHome/掘金/CSDN、各公司公开技术分享

---

## 一、背景：为什么 HarmonyOS NEXT 的测试体系需要重建

HarmonyOS NEXT（"纯血鸿蒙"，HarmonyOS 5.0 起）完全移除了 AOSP 和 Android 运行时，不再兼容 APK。这意味着 Android 那套成熟的 `adb + uiautomator2 + Appium + Monkey` 工具链**整体失效**，测试体系需要按新栈重建。好在华为的体系设计与 Android 有清晰的对照关系，迁移学习成本可控：

| Android 生态 | HarmonyOS NEXT 对应物 | 说明 |
|---|---|---|
| adb | **hdc**（HarmonyOS Device Connector） | 设备连接、安装卸载、shell、文件推拉、截屏录屏 |
| am / am instrument | **aa**（Ability Assistant） | `hdc shell aa test` 拉起测试进程 |
| uiautomator / uitest | **uitest**（arkxtest 设备端组件） | 控件树 dump、输入注入 |
| AndroidJUnitRunner + Espresso | **arkxtest**（JsUnit + UiTest） | 官方单元/UI 测试框架（JS/TS） |
| instrumentation Python 化 | **Hypium**（DevEco Testing Hypium） | 官方 Python 黑盒 UI 自动化框架 |
| AccessibilityService 控件树 | ArkUI **无障碍树** | `uitest dumpLayout` 的数据来源 |
| Monkey | **Wukong** | 随机事件稳定性压测 |
| Firebase Test Lab / 云真机 | **DevEco Testing 云测** | 远程真机、专项测试、上架预检 |
| Perfetto / systrace | **SmartPerf**、hiperf、hidumper | 性能采集与调优 |

### 底层原理（串联起来理解）

1. **hdc** 是 PC 与设备之间的通道（类似 adb 的 server-client 架构），所有自动化框架最终都经由它下发命令。
2. **单元/白盒测试链路**：`hdc shell aa test -b 包名 -m 模块 -s unittest OpenHarmonyTestRunner` → AMS（AbilityManagerService）拉起测试进程 → **TestRunner + AbilityDelegator** 驱动用例执行并回传结果。
3. **UI 自动化链路**：`uitest` 底层依赖 **ArkUI 无障碍树**做控件定位（`uitest dumpLayout` 输出控件树，等价于 Android 的 `uiautomator dump`），通过系统输入注入接口模拟点击/滑动（`hdc shell uitest uiInput click/swipe/drag`），机制与 Android 的 AccessibilityService + UiAutomator 类似。第三方框架（hmdriver2 等）本质上都是"hdc + uitest + 无障碍树"的封装。
4. 正因为走无障碍树，**自定义绘制、Canvas、游戏、Web 内嵌内容**等场景拿不到控件节点，需要退回图像识别或比例坐标方案（Hypium 官方就同时提供控件/图像/比例坐标三种定位方式）。

---

## 二、官方测试体系（第一优先了解）

华为把测试能力整合在 **DevEco Testing（应用测试服务平台）** 之下，覆盖"单元 → UI → 专项 → 云测"全链路。

### 2.1 单元测试：arkxtest JsUnit
- OpenHarmony 官方测试框架 **arkxtest** 分两部分：**JsUnit**（单元测试）+ **UiTest**（UI 测试）。
- JsUnit 使用 `describe / it / expect` 风格（类似 mocha），用 TS/ArkTS 编写，随应用工程放在 `ohosTest/` 目录，DevEco Studio 内置支持，可跑在本地模拟器/真机。
- 适合开发自测：ViewModel、数据层、工具函数、@ohos 接口 mock。

### 2.2 白盒 UI 测试：@ohos.UiTest（ArkTS）
- 面向开发者的应用内 UI 测试，提供 `On（定位）/ Driver（操作）` 两类 API：控件查找、点击、滑动、拖拽、文本输入、断言。
- 与应用同进程打包，适合开发随版本跑核心交互回归，不适合测试团队做跨应用黑盒。

### 2.3 黑盒 UI 自动化：DevEco Testing Hypium（Python）
- **官方主推的测试团队方案**，Python 编写脚本，pip 安装，无需修改被测应用（黑盒、跨应用）。
- 三种定位方式：**原生控件定位、图像识别、比例坐标定位**——控件定位不到的动态内容/自绘内容可用图像兜底。
- 支持多设备/分布式场景测试（鸿蒙特色：多设备协同、跨端迁移），支持多窗口操作、触屏交互。
- DevEco Studio 插件自动生成测试目录与用例模板；官方案例称测试开发效率提升约 30%。
- 文档：[Hypium Python 指南](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/hypium-python-guidelines)

### 2.4 稳定性 / 性能 / 专项
- **稳定性**：`Wukong`（随机事件 monkey 压测）+ DevEco Testing 稳定性测试（含新增的**智能稳定性测试/应用探索测试**：智能遍历应用路径、自动识别崩溃/冻屏/泄漏/异常退出等问题）。官方最佳实践文档对运行态稳定性、地址越界、资源泄漏、应用冻屏、异常退出各有检测方法。
- **性能**：**SmartPerf**（依托芯片+OS 的性能评估调优工具，普通应用与游戏均可用）+ DevEco Testing 性能测试（启动、响应、滑动/滚动流畅度、内存/功耗基线对比）。
- **兼容性 / UX / 功耗 / 安全**：DevEco Testing 提供基于**鸿蒙应用上架质量标准**的一键式检测，输出专业报告，可直接作为上架预检门禁。

### 2.5 云测
- DevEco Testing **云测（远程真机）**：无需购买全套真机即可做兼容/性能/稳定性验证；还有**众测/内测**分发能力。
- 被测设备需 HarmonyOS 5.0 及以上。

### 2.6 CI/CD 集成
- 官方明确支持命令行集成：流水线通过 **`run` 命令调用 Hypium** 执行 UI 自动化；hdc/aa/uitest 均为纯命令行工具，可无缝接入 Jenkins、GitLab CI 等。
- 典型流水线：代码提交 → hvigor 构建 HAP → 真机/模拟器自动安装 → `aa test` 跑单元测试 / Hypium 跑 UI 回归 → 报告回传 → DevEco Testing 上架预检门禁。

> 参考：[华为官方自动化测试框架最佳实践](https://developer.huawei.com/consumer/cn/doc/best-practices/bpta-automated-testing-frameworks) ｜ [HarmonyOS 生态测试白皮书](https://developer.huawei.com/consumer/cn/doc/guidebook/hoetwp-capability-info-0000002594762520) ｜ [HarmonyOS 开发者测试服务入门（含 CI/CD）](https://developer.huawei.com/consumer/cn/testing/get-started/)

---

## 三、社区与第三方方案

### 3.1 hmdriver2（社区最活跃的开源方案）⭐
- GitHub：[codematrixer/hmdriver2](https://github.com/codematrixer/hmdriver2)，Python，MIT 协议，`pip3 install hmdriver2` 即用。
- 定位：**无侵入式**（设备端不装 testRunner 类 APP）、轻量（几乎零依赖）、API 刻意对齐 Android 的 **uiautomator2**（`d(text="精选").click()`），Android 自动化同学可近乎零成本切换。
- 能力覆盖：
  - 应用管理：安装/卸载/启停/清数据/获取应用信息；
  - 设备：信息/分辨率/旋转、Home/返回、亮屏息屏解锁、按键、截图录屏、文件推拉、schema 打开；
  - 定位：id/key/text/type/description/clickable 等十余种属性选择器、组合定位、相对定位（isBefore/isAfter）、index 定位、**XPath**；
  - 操作：点击/双击/长按/拖拽/捏合缩放、文本输入、控件树 dump、Toast 监听、坐标级手势（支持百分比相对坐标）、链式复杂手势。
- 配套 ui-viewer 工具可视化查看控件树。
- 局限：全场景弹窗处理、控件模糊定位仍在 TODO；录屏依赖 opencv-python；本质依赖无障碍树，自绘内容同样定位不到。
- 衍生项目：[HMNextAuto](https://github.com/ziguiway/hmnextauto)（API 兼容 uiautomator2 的延续/分支）。

**hmdriver2 出现的原因**（TesterHome 发布帖 [《hmdriver2 发布》](https://testerhome.com/topics/40667)）：官方 Hypium 被诟病"安装依赖多、使用繁杂、脚本执行效率较低、未正式开源、bug 修复周期长"。这是很多测试团队实际选型时的真实痛点。

### 3.2 命令行工具集
- [awesome-hdc（鸿蒙 HDC 命令合集）](https://github.com/codematrixer/awesome-hdc)、[全网最全鸿蒙 HDC 命令合集（TesterHome）](https://testerhome.com/topics/39910)：hdc + uitest + aa 全套命令，适合自研轻量框架或做设备 farm。
- 常用：`hdc list targets`、`hdc install xxx.hap`、`hdc shell aa start`、`hdc shell uitest uiInput click x y`、`hdc shell uitest dumpLayout`、`hdc shell snapshot_display` 截屏。

### 3.3 传统跨平台框架现状（避坑）
- **Appium**：只支持早期"兼容 Android 的 HarmonyOS"，**不支持 HarmonyOS NEXT**。
- **Airtest（网易）/ Sonic**：官方未支持鸿蒙设备连接，社区反馈均无法连 NEXT 原生系统。
- 结论：纯血鸿蒙上不要押注这三家，优先 Hypium / hmdriver2。

### 3.4 第三方测试服务
- Testin 云测、优测（21kunpeng）、TestOne 等国产平台已提供 HarmonyOS 兼容性/专项/云真机服务，适合补齐设备矩阵（尤其低端机、折叠屏、多尺寸平板）。

---

## 四、业界优秀实践

### 4.1 字节/抖音：AI 重塑鸿蒙质量保障（公开最完整）⭐
来源：HDC 大会分享，[《主功能 100% 覆盖、效率提升 20%！鸿蒙版抖音用 AI 重塑质量保障体系》](https://www.51cto.com/article/846432.html)
- 成果：鸿蒙版抖音**主功能点 100% 覆盖**；规则明确、路径清晰的场景下 **AI 有效成功率 70%**；整体验证**效率提升 20%**；测试用例从约 1000 条扩展到 5000 条，实现 AI 测试规模化落地。
- 核心理念：**"AI 承担提效，人工坚守质量底线"**——规则明确、路径清晰的场景交给 AI 全自动；复杂/高风险场景保留人工介入。
- 字节移动端的既有积累（意图识别、步骤自动纠错修复、自动分级 mock、断言规则自动生成、精准测试 SmartEye 代码覆盖率平台）迁移到了鸿蒙侧。

### 4.2 美团
- 战略上 6 周完成鸿蒙原生 Beta 版，外卖/酒店等几十个业务模块多团队并行，测试体系需要"多模块、跨团队协作"的自动化基建支撑（[美团技术沙龙第 87 期：鸿蒙原生适配与跨端架构演进](https://www.eet-china.com/mp/a456966.html)）。
- QA 侧已有 **Multi-Agent 驱动的 UI 自动化测试**分享（B 站《美团 QA 智能测试实践》）；外卖时代的 Appium 封装 + 稳定性治理方法论（[自动化测试在美团外卖的实践与落地](https://tech.meituan.com/2022/09/15/Automated-Testing-in-meituan.html)）被沿用为鸿蒙侧的分层设计思想。

### 4.3 金融行业（合规重、风险敏感，做法最具参考性）
- **民生银行**：APP 鸿蒙化建设中引入 **AI 测试用例生成、多模态 AI 测试**，获 IDC 中国金融行业技术应用场景创新案例（[PDF](https://www.cmbc.com.cn/cmbc_new/2026-05/08/718e1d42dc5e44f89f386e51028288ee/2026050815345918539.pdf)）。
- **交通银行**：鸿蒙手机银行结合鸿蒙特性做了十余项创新场景，入选移动金融 App 创新典型案例。
- **京东金融**：2023 年 11 月启动调研、2024 年 6 月上架尝鲜版（[京东金融 APP 的鸿蒙之旅](https://developer.jdcloud.com/article/4043)），重点在安全迁移（手机盾、数字证书）。
- 行业面：2025 年初已有超 800 款金融应用/元服务上架原生鸿蒙应用市场。

### 4.4 蚂蚁/支付宝
- 较早启动鸿蒙原生开发；技术上有开源接口自动化框架 ACTS 沉淀，且在"大模型 + 多模态识别做业务异常与深度链路检测"方向与华为同属第一梯队。

### 4.5 业界共性最佳实践总结（提炼）
1. **分层测试金字塔**：单元/接口（arkxtest JsUnit，量大、快、CI 必跑）→ 核心 UI 回归（Hypium 或 hmdriver2，P0 流程）→ 专项（稳定性/性能/兼容，作为发布门禁）。UI 用例只保主链路，不追求全覆盖。
2. **框架组合而非单选**：开发用 @ohos.UiTest 白盒内嵌回归；测试团队用 Python 栈（Hypium 或 hmdriver2）做黑盒；底层 hdc/uitest 命令自研设备农场。
3. **上架预检做门禁**：DevEco Testing 的兼容/性能/稳定性/UX/功耗检测与华为上架质量标准对齐，直接作为发布流水线的一环。
4. **AI 分工原则**："AI 提效、人工守底线"（抖音模式）：AI 用于用例生成、脚本自愈/纠错、图像断言、遍历探索；高风险与复杂体验场景人工兜底。
5. **无障碍属性规范前移**：控件定位强依赖无障碍树，团队应把 id/key/text 等属性的规范性纳入开发编码规范（类似 Android 的 resource-id 治理），否则 UI 自动化维护成本极高。
6. **设备矩阵靠云测补齐**：真机成本高，自建 1-2 台核心机型 + 云测覆盖长尾机型是普遍做法。

---

## 五、选型建议（按团队场景）

| 场景 | 推荐方案 | 理由 |
|---|---|---|
| 开发自测（单元/接口） | arkxtest JsUnit + mock | 官方内置、随工程、CI 友好 |
| 开发核心交互回归 | @ohos.UiTest（ArkTS） | 白盒、与应用同打包、稳定 |
| 测试团队黑盒 UI 自动化 | **Hypium（Python）** 或 **hmdriver2** | Hypium 官方支持+图像识别+分布式；hmdriver2 轻量、uiautomator2 风格、社区活跃 |
| 稳定性 | Wukong + DevEco Testing 智能稳定性/探索测试 | 官方标准、能出报告 |
| 性能/功耗 | SmartPerf + DevEco Testing 性能 | 芯片级数据、与上架标准对齐 |
| 兼容性/UX | DevEco Testing 云测 + 第三方云测（Testin/优测） | 设备矩阵覆盖 |
| CI/CD | hvigor 构建 + hdc 安装 + `aa test`/Hypium `run` 命令 + Jenkins/GitLab CI | 全链路命令行化 |
| AI 增强方向 | 用例生成、脚本自愈、多模态断言（参考抖音/民生实践） | 业界明确趋势 |

**一句话选型**：有 Python 自动化沉淀的测试团队，从 **hmdriver2 起步最快**（一天可跑通冒烟），逐步切到 **Hypium** 拿官方支持与图像/分布式能力；开发侧用 **JsUnit + UiTest** 建好底座；发布门禁交给 **DevEco Testing**。

## 六、落地路线图建议

1. **Phase 1（1-2 周）**：搭环境（DevEco Studio + Command Line Tools + hdc）；hmdriver2/Hypium 跑通"安装-启动-登录-核心浏览"冒烟；规范无障碍属性。
2. **Phase 2（1 个月）**：P0 业务主流程 UI 回归用例（20-50 条）；JsUnit 覆盖核心数据层；接入 CI 每日构建执行。
3. **Phase 3（1-2 个月）**：加稳定性（Wukong 过夜跑）、性能基线、DevEco Testing 上架预检门禁；云测补设备矩阵。
4. **Phase 4（持续）**：AI 增强——用例自动生成、失败自愈、图像断言、智能遍历，按"AI 提效 + 人工守底线"分工推进。

---

## 七、关键参考链接

**官方**
- DevEco Testing 平台主页：https://developer.huawei.com/consumer/cn/deveco-testing/
- Hypium Python 指南：https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/hypium-python-guidelines
- arkxtest 指南：https://developer.huawei.com/consumer/cn/doc/harmonyos-guides-V5/arkxtest-guidelines-V5
- UiTest 使用指导：https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/uitest-guidelines
- hdc 命令：https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/hdc
- 自动化测试框架最佳实践：https://developer.huawei.com/consumer/cn/doc/best-practices/bpta-automated-testing-frameworks
- 稳定性测试最佳实践：https://developer.huawei.com/consumer/cn/doc/best-practices/bpta-stability-testing
- 鸿蒙生态测试白皮书：https://developer.huawei.com/consumer/cn/doc/guidebook/hoetwp-capability-info-0000002594762520
- 开发者测试服务入门（含 CI/CD）：https://developer.huawei.com/consumer/cn/testing/get-started/
- OpenHarmony test 目录（Wukong/SmartPerf 等）：https://gitcode.com/openharmony/docs/tree/master/zh-cn/application-dev/test

**开源/社区**
- hmdriver2：https://github.com/codematrixer/hmdriver2
- HMNextAuto：https://github.com/ziguiway/hmnextauto
- awesome-hdc：https://github.com/codematrixer/awesome-hdc
- hmdriver2 发布帖（TesterHome）：https://testerhome.com/topics/40667
- 鸿蒙 NEXT 原生自动化框架讨论（TesterHome）：https://testerhome.com/topics/39416
- 全网最全 HDC 命令合集：https://testerhome.com/topics/39910
- arkxtest 源码：https://gitee.com/openharmony/testfwk_arkxtest

**业界实践**
- 抖音 AI 质量保障：https://www.51cto.com/article/846432.html
- 美团外卖自动化测试实践：https://tech.meituan.com/2022/09/15/Automated-Testing-in-meituan.html
- 京东金融鸿蒙实践：https://developer.jdcloud.com/article/4043
- 民生银行鸿蒙智能化测试：https://www.cmbc.com.cn/cmbc_new/2026-05/08/718e1d42dc5e44f89f386e51028288ee/2026050815345918539.pdf
