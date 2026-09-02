# 白盒注入：被测工程入口改造示例

> 这是一次性合作代码。不改它，工具走纯黑盒（静态场景 + Explorer 启发式 + LLM）；
> 改了它，黑盒够不着的场景（登录墙、深层状态、前置条件）可通过 `aa start` 参数直达。

## 原理

工具用带参数的方式启动 Ability：

```
aa start -b <bundle> -a EntryAbility \
  --pi autoshot.route=NavDetailPage autoshot.state.isVip=true
```

入口读 `want.parameters` 里的 `autoshot.*`，写入 `AppStorage`；首页 `Navigation` 在
`aboutToAppear` 时读 `AppStorage`，发现 route 就 `pushPathByName` 直达。

## 1. EntryAbility 读参数写入 AppStorage

```ts
// entry/src/main/ets/entryability/EntryAbility.ets
import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    const p = want.parameters ?? {};
    const route = p['autoshot.route'];
    if (typeof route === 'string') {
      AppStorage.setOrCreate('autoshot.route', route);
      for (const k of Object.keys(p)) {
        if (k.startsWith('autoshot.state.')) {
          AppStorage.setOrCreate(k.slice('autoshot.state.'.length), p[k]);
        }
      }
    }
  }
  // ...其余不变
}
```

## 2. 首页 Navigation 读 AppStorage 直达

```ts
// entry/src/main/ets/pages/Index.ets（或 NavEntryPage 所在页）
@Entry
@Component
struct Index {
  @StorageProp('autoshot.route') route: string = ''
  private pathStack: NavPathStack = new NavPathStack()

  aboutToAppear() {
    if (this.route) {
      this.pathStack.pushPathByName(this.route, {})
      AppStorage.setOrCreate('autoshot.route', '')  // 消费一次即清
    }
  }

  build() {
    Navigation(this.pathStack) { /* ... */ }
  }
}
```

> 状态注入（`autoshot.state.isVip=true`）的消费方式取决于业务：目标页在 `aboutToAppear`
> 读 `@StorageProp('isVip')` 或 `AppStorage.get('isVip')`，把条件渲染的状态变量初始化成
> 注入值。工具侧由 `_inject_key` 的 `state` 参数透传（当前版本先支持 route 直达，
> state 透传预留了 `inject_launch` 的 `state` 入参）。

## 3. 启用注入

在 `autoshot.yaml` 配置：

```yaml
inject:
  enabled: true
  bundle: com.example.app   # 可选，缺省读 AppScope/app.json5 的 bundleName
```

## 4. 生效路径

`capture` 的降级链会按序尝试：静态场景 → 白盒注入直达 → Explorer 探索（启发式 + LLM）。
只有静态场景失败、且目标 key 归属 NavDestination（route_map.json）页面时，注入层才会介入。
报告里 `method` 列会显示 `注入`。
