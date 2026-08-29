import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';

const DOMAIN = 0x0000;

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(DOMAIN, 'HualiTD', 'Ability onCreate');
  }

  onDestroy(): void {
    hilog.info(DOMAIN, 'HualiTD', 'Ability onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, 'HualiTD', 'Failed to load page: %{public}s', JSON.stringify(err));
        return;
      }
      hilog.info(DOMAIN, 'HualiTD', 'Page loaded');
    });
  }

  onWindowStageDestroy(): void {
    hilog.info(DOMAIN, 'HualiTD', 'Window destroyed');
  }

  onForeground(): void {
    hilog.info(DOMAIN, 'HualiTD', 'Ability onForeground');
  }

  onBackground(): void {
    hilog.info(DOMAIN, 'HualiTD', 'Ability onBackground');
  }
}
