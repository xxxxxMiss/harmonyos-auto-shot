/**
 * typescript 加载器：双模式。
 *
 * 优先使用鸿蒙官方 fork 的 TypeScript（DevEco SDK 内，原生支持 ArkTS 的 struct 等语法，
 * parse 错误更少、AST 与鸿蒙实际编译一致）；找不到时回退项目 node_modules 的官方 TypeScript
 * （此时需配合 preprocess 把 struct→class）。
 *
 * 探测路径：DEVECO_SDK_HOME 环境变量 / 默认 DevEco Studio 安装位置。
 * 可用 AUTOSHOT_TS=official 强制走官方版（调试/CI 无 DevEco 环境）。
 */
import fs from 'fs';
import path from 'path';
import os from 'os';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);

function candidateForkPaths() {
  const paths = [];
  const sdkHome = process.env.DEVECO_SDK_HOME;
  if (sdkHome) {
    paths.push(path.join(sdkHome, 'openharmony', 'ets', 'build-tools', 'ets-loader', 'node_modules', 'typescript'));
    paths.push(path.join(sdkHome, 'default', 'openharmony', 'ets', 'build-tools', 'ets-loader', 'node_modules', 'typescript'));
  }
  const home = os.homedir();
  if (process.platform === 'darwin') {
    paths.push('/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/ets/build-tools/ets-loader/node_modules/typescript');
  } else if (process.platform === 'win32') {
    const base = process.env.ProgramFiles || 'C:\\Program Files';
    paths.push(path.join(base, 'Huawei', 'DevEco Studio', 'sdk', 'default', 'openharmony', 'ets', 'build-tools', 'ets-loader', 'node_modules', 'typescript'));
  } else {
    // linux
    paths.push(path.join(home, 'DevEco-Studio', 'sdk', 'default', 'openharmony', 'ets', 'build-tools', 'ets-loader', 'node_modules', 'typescript'));
    paths.push('/opt/DevEco-Studio/sdk/default/openharmony/ets/build-tools/ets-loader/node_modules/typescript');
  }
  return paths;
}

let _loaded = null;

export function loadTypescript() {
  if (_loaded) return _loaded;
  const forceOfficial = process.env.AUTOSHOT_TS === 'official';

  if (!forceOfficial) {
    for (const base of candidateForkPaths()) {
      const entry = path.join(base, 'lib', 'typescript.js');
      if (fs.existsSync(entry)) {
        try {
          _loaded = { ts: require(entry), source: 'fork' };
          return _loaded;
        } catch (_) { /* 继续尝试 */ }
      }
    }
  }
  // 回退官方 typescript（项目 node_modules）
  try {
    _loaded = { ts: require('typescript'), source: 'official' };
  } catch (e) {
    throw new Error('未找到 typescript：请 npm install（或配置 DEVECO_SDK_HOME 指向鸿蒙 SDK）');
  }
  return _loaded;
}

// 是否原生支持 struct（fork 版支持，官方版不支持需 preprocess）
export function needsStructPreprocess() {
  const { source } = loadTypescript();
  return source === 'official';
}

export function typescriptSource() {
  return loadTypescript().source;
}

// struct→class 预处理（仅官方 TS 需要；fork 版原生支持 struct）。
// 掩码保护字符串/注释，正则替换 struct，再还原。
export function preprocessStruct(source) {
  const protectedParts = [];
  const masked = source.replace(
    /\/\/[^\n]*|\/\*[\s\S]*?\*\/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`/g,
    (m) => { protectedParts.push(m); return `\u0000${protectedParts.length - 1}\u0000`; });
  const replaced = masked.replace(/\bstruct\s+([A-Za-z_$][\w$]*)/g, 'class $1');
  return replaced.replace(/\u0000(\d+)\u0000/g, (_, i) => protectedParts[+i]);
}
