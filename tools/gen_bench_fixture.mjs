#!/usr/bin/env node
/** 生成千文件级 fixture，复现大型工程的特征：
 *  - 大量 .ets 文件，每个含几十个 ArkUI 组件调用（Text/Column/Button/.fontSize() 等）
 *    这些是"带参 CallExpression"，但非 i18n 访问器 → 复现瓶颈 1 的 matchAccessor 白跑。
 *  - 部分文件含 $r 引用、this.xxx 属性、import 链。
 *  - 少量文件含 i18n 封装调用（I18n.t(...)），复现 R5 真实命中。
 *
 * 用法：node tools/gen_bench_fixture.mjs <输出目录> <文件数>
 */
import fs from 'fs';
import path from 'path';

const outRoot = process.argv[2] || '/tmp/bench_fixture';
const N = parseInt(process.argv[3] || '1000', 10);

fs.rmSync(outRoot, { recursive: true, force: true });
const pagesDir = path.join(outRoot, 'entry', 'src', 'main', 'ets', 'pages');
const commonDir = path.join(outRoot, 'entry', 'src', 'main', 'ets', 'common');
fs.mkdirSync(pagesDir, { recursive: true });
fs.mkdirSync(commonDir, { recursive: true });

// ---- 工程配置 ----
fs.mkdirSync(path.join(outRoot, 'entry', 'src', 'main', 'resources', 'base', 'profile'), { recursive: true });
fs.mkdirSync(path.join(outRoot, 'entry', 'src', 'main', 'resources', 'base', 'element'), { recursive: true });

const buildProfile = {
  app: { modules: [{ name: 'entry', srcPath: './entry' }] },
};
fs.writeFileSync(path.join(outRoot, 'build-profile.json5'), JSON.stringify(buildProfile, null, 2));

const pageNames = Array.from({ length: N }, (_, i) => `pages/Page${i}`);
fs.writeFileSync(path.join(outRoot, 'entry', 'src', 'main', 'module.json5'), JSON.stringify({
  module: { name: 'entry', type: 'entry', pages: '$profile:main_pages' },
}, null, 2));
fs.writeFileSync(path.join(outRoot, 'entry', 'src', 'main', 'resources', 'base', 'profile', 'main_pages.json'),
  JSON.stringify({ src: pageNames }, null, 2));

const stringRes = Array.from({ length: N }, (_, i) => ({ name: `title_${i}`, value: `标题${i}` }));
fs.writeFileSync(path.join(outRoot, 'entry', 'src', 'main', 'resources', 'base', 'element', 'string.json'),
  JSON.stringify({ string: stringRes }, null, 2));

// ---- 公共 i18n 封装（R5 访问器）----
fs.writeFileSync(path.join(commonDir, 'I18n.ets'),
`export class I18n {
  static t(name: string): string {
    return getContext(this)?.resourceManager?.getStringByNameSync(name) ?? name
  }
}
`);

// ---- 生成 N 个页面文件 ----
for (let i = 0; i < N; i++) {
  const lines = [];
  lines.push(`import { I18n } from '../common/I18n';`);
  lines.push(`@Component`);
  lines.push(`struct Page${i} {`);
  lines.push(`  @State title: string = ''`);
  lines.push(`  build() {`);
  lines.push(`    Column() {`);
  // 30 个普通组件调用（触发 matchAccessor 白跑）
  for (let k = 0; k < 30; k++) {
    lines.push(`      Text('item${i}_${k}').fontSize(${12 + k % 10}).width(${100 + k}).height(40).margin({ top: ${k} })`);
    if (k % 5 === 0) lines.push(`      Button('btn${i}_${k}').onClick(() => { this.title = '${k}' })`);
  }
  // 少量 $r 引用（真实命中 R1）
  lines.push(`      Text($r('app.string.title_${i}')).fontSize(20)`);
  // 少量 i18n 封装调用（真实命中 R5，约 1/10 文件）
  if (i % 10 === 0) lines.push(`      Text(I18n.t('title_${i}'))`);
  lines.push(`    }.width('100%').height('100%')`);
  lines.push(`  }`);
  lines.push(`}`);
  fs.writeFileSync(path.join(pagesDir, `Page${i}.ets`), lines.join('\n'));
}

console.log(`已生成 ${N} 个页面文件 + 1 公共文件 到 ${outRoot}`);
