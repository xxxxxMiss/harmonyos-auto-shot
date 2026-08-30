#!/usr/bin/env node
/**
 * auto-shot 阶段二静态分析内核（AST 后端）。
 *
 * 流程：struct→class 词法预处理 → TypeScript Compiler API 解析 →
 *   R1 $r / R2 .id 换取 / R3 按名字面量 / R4 常量传播 / R5 函数摘要(i18n 封装) / R6 查表
 *   → 上下文分类（visible / conditional[条件源码] / click / runtime）
 *   → 导航图（module.json5 pages + router/NavPathStack 边 + import 归属）
 *
 * 用法：node tools/ast_scan.mjs <projectRoot> [outJsonPath]
 * 输出：JSON（stdout 或 --out 文件），由 Python 侧 autoshot/scanner.py 调用合并。
 */
import fs from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import { pathToFileURL, fileURLToPath } from 'url';
import { evaluateRouteItems } from './predicate_eval.mjs';
import { loadTypescript, needsStructPreprocess, typescriptSource } from './ts_loader.mjs';
import { buildProgram, resolveSymbol, constantValue } from './ts_program.mjs';
const ts = loadTypescript().ts;

const isMain = import.meta.url === pathToFileURL(process.argv[1] || '').href;

// struct（ArkTS/fork TS 原生）或 class（官方 TS + preprocess）统一视为"结构体声明"
const isStructLike = (node) =>
  ts.isClassDeclaration(node) || (ts.isStructDeclaration && ts.isStructDeclaration(node));

const PRUNE = new Set(['oh_modules', 'node_modules', 'build', '.hvigor', '.preview',
  '.cxx', '.idea', '.git', 'dist', '.test', 'entry/build']);
const BYNAME = new Set(['getStringByNameSync', 'getStringByName', 'getPluralStringByNameSync',
  'getPluralStringByName', 'getStringArrayByNameSync', 'getStringArrayByName']);
const ID_APIS = new Set(['getStringSync', 'getStringValue', 'getString']);
// dialog 方法名集合（property access 的 name 部分，供 classify 廉价判断，避免 getText）
const DIALOG_METHODS = new Set(['showToast', 'showDialog', 'openCustomDialog', 'showActionMenu', 'showAlertDialog']);

// ---------- 工具 ----------
function fail(msg) { console.error('[ast_scan] ' + msg); process.exit(2); }
function readText(p) { try { return fs.readFileSync(p, 'utf-8'); } catch { return null; } }
function readJsonLoose(p) {
  const t = readText(p); if (t == null) return null;
  const clean = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/,(\s*[}\]])/g, '$1');
  try { return JSON.parse(t); } catch { /* 宽松重试：去注释 + 去尾逗号 */ }
  try { return JSON.parse(clean(t)); } catch { return null; }
}
function rel(root, p) { return path.relative(root, p).split(path.sep).join('/'); }

// ---------- 增量缓存（P5）：文件指纹比对，未变则复用上次扫描结果 ----------
function cachePathFor(root) {
  const key = crypto.createHash('md5').update(path.resolve(root)).digest('hex').slice(0, 16);
  return path.join(os.tmpdir(), `autoshot_cache_${key}.json`);
}

// 收集指纹输入：所有源文件 + 配置文件（json5/json，含 build-profile/module.json5/资源）
function collectFingerprintInputs(root, files) {
  const inputs = [...files];
  const CONFIG_PRUNE = new Set(['node_modules', '.hvigor', 'build', '.idea', '.git', 'oh_modules']);
  (function walk(dir) {
    let es; try { es = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of es) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { if (!CONFIG_PRUNE.has(e.name)) walk(p); continue; }
      if (e.name.endsWith('.json5') || e.name.endsWith('.json')) inputs.push(p);
    }
  })(root);
  return inputs;
}

function computeFingerprint(root, files) {
  const entries = [];
  for (const f of collectFingerprintInputs(root, files)) {
    try {
      const st = fs.statSync(f);
      entries.push(rel(root, f) + '\u0000' + st.mtimeMs + '\u0000' + st.size);
    } catch { entries.push(rel(root, f) + '\u0000MISSING'); }
  }
  entries.sort();
  // 关键：纳入扫描器自身版本（含 ast_scan/predicate_eval/ts_program/ts_loader 的 mtime），
  // 避免工具逻辑升级后旧缓存返回过时结果。
  const toolsDir = path.dirname(fileURLToPath(import.meta.url));
  const selfFiles = [fileURLToPath(import.meta.url),
    path.join(toolsDir, 'predicate_eval.mjs'),
    path.join(toolsDir, 'ts_program.mjs'),
    path.join(toolsDir, 'ts_loader.mjs')];
  for (const f of selfFiles) {
    try {
      const st = fs.statSync(f);
      entries.push('__SELF__\u0000' + f + '\u0000' + st.mtimeMs + '\u0000' + st.size);
    } catch { /* 忽略 */ }
  }
  entries.sort();
  return crypto.createHash('sha256').update(entries.join('\n')).digest('hex');
}

function tryLoadCache(root, files) {
  const cacheFile = cachePathFor(root);
  if (!fs.existsSync(cacheFile)) return null;
  try {
    const cache = JSON.parse(fs.readFileSync(cacheFile, 'utf-8'));
    const fp = computeFingerprint(root, files);
    if (cache.fingerprint === fp && cache.result) return cache.result;
  } catch { /* 缓存损坏，忽略 */ }
  return null;
}

function writeCache(root, files, result) {
  try {
    const cacheFile = cachePathFor(root);
    const fp = computeFingerprint(root, files);
    fs.writeFileSync(cacheFile, JSON.stringify({ fingerprint: fp, result }, null, 0));
  } catch { /* 写缓存失败不影响主流程 */ }
}

// ---------- struct→class 预处理（跳过字符串与注释内的关键字） ----------
export function preprocess(source) {
  // 性能版：先"掩码保护"字符串/注释，再正则替换 struct，最后还原。
  // 相比逐字符状态机（~200ms/万文件），本实现 ~0ms，且行为等价：
  // 字符串/注释/模板串里的 "struct X" 不会误替换（先被占位符盖住）。
  const protectedParts = [];
  const masked = source.replace(
    /\/\/[^\n]*|\/\*[\s\S]*?\*\/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`/g,
    (m) => { protectedParts.push(m); return `\u0000${protectedParts.length - 1}\u0000`; });
  const replaced = masked.replace(/\bstruct\s+([A-Za-z_$][\w$]*)/g, 'class $1');
  return replaced.replace(/\u0000(\d+)\u0000/g, (_, i) => protectedParts[+i]);
}

// ---------- 文件与页面发现 ----------
function collectSourceFiles(root) {
  const files = [];
  (function walk(dir) {
    let entries; try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (e.isDirectory()) { if (!PRUNE.has(e.name)) walk(path.join(dir, e.name)); continue; }
      if (e.name.endsWith('.ets') || e.name.endsWith('.ts')) files.push(path.join(dir, e.name));
    }
  })(root);
  return files;
}

function discoverPages(root) {
  const pages = []; // {name:'pages/Index' 或 'DetailPage', file:'entry/src/main/ets/pages/X.ets', module, via:'pages'|'routerMap'}
  const bp = readJsonLoose(path.join(root, 'build-profile.json5')) || {};
  for (const mod of bp.app?.modules || bp.modules || []) {
    const srcPath = mod.srcPath || mod.srcpath; if (!srcPath) continue;
    const base = path.join(root, srcPath, 'src', 'main');
    const moduleJson = readJsonLoose(path.join(base, 'module.json5')); if (!moduleJson) continue;

    // 1) 传统 router 方案：module.json5.pages -> main_pages.json（name 形如 'pages/Index'）
    const pagesRef = moduleJson.module?.pages; // "$profile:main_pages" 或数组
    let pageSrcs = [];
    if (typeof pagesRef === 'string' && pagesRef.startsWith('$profile:')) {
      const pj = readJsonLoose(path.join(base, 'resources', 'base', 'profile', pagesRef.slice('$profile:'.length) + '.json'));
      pageSrcs = pj?.src || [];
    } else if (Array.isArray(pagesRef)) pageSrcs = pagesRef;
    for (const s of pageSrcs) {
      const f = path.join(base, 'ets', s + '.ets');
      pages.push({ name: s, file: rel(root, f), module: mod.name || path.basename(srcPath), exists: fs.existsSync(f), via: 'pages' });
    }

    // 2) NavDestination 方案：module.json5.routerMap -> route_map.json
    //    routerMap 条目 name 为路由名（如 'DetailPage'，不带 pages/ 前缀），
    //    pageSourceFile 为相对模块根路径（如 'src/main/ets/pages/DetailPage.ets'）。
    const routerMapRef = moduleJson.module?.routerMap; // "$profile:route_map"
    if (typeof routerMapRef === 'string' && routerMapRef.startsWith('$profile:')) {
      const rm = readJsonLoose(path.join(base, 'resources', 'base', 'profile', routerMapRef.slice('$profile:'.length) + '.json'));
      const entries = rm?.routerMap || [];
      for (const e of entries) {
        const name = e?.name; const psf = e?.pageSourceFile;
        if (!name || !psf) continue;
        const f = path.join(root, srcPath, psf);
        pages.push({ name, file: rel(root, f), module: mod.name || path.basename(srcPath), exists: fs.existsSync(f), via: 'routerMap' });
      }
    }
  }
  return pages;
}

// ---------- 每文件符号收集 ----------
export function analyzeFile(root, filePath, sourceFile) {
  const rec = {
    file: rel(root, filePath),
    consts: new Map(),        // name -> string 字面量
    constObjs: new Map(),     // name -> Map(key -> string 字面量)
    constObjExprs: new Map(), // name -> Map(key -> 表达式文本)；用于参数一层变量追踪
    constArrays: new Map(),   // name -> ArrayLiteralExpression（顶层 const 数组，节点引用）
    constFns: new Map(),      // name -> ArrowFunction（顶层 const 箭头函数，节点引用）
    classes: new Map(),       // className -> {decorators, props, arrayProps, methods}
    functions: new Map(),     // funcName -> node
    imports: new Map(),       // localName -> {spec, imported}
    exports: new Set(),
  };
  // imports
  for (const st of sourceFile.statements) {
    if (ts.isImportDeclaration(st)) {
      const spec = st.moduleSpecifier.text;
      const bind = st.importClause?.namedBindings;
      if (bind && ts.isNamedImports(bind)) {
        for (const el of bind.elements) rec.imports.set(el.name.text, { spec, imported: el.propertyName?.text || el.name.text });
      } else if (bind && ts.isNamespaceImport(bind)) {
        rec.imports.set(bind.name.text, { spec, imported: '*', ns: true });
      }
    }
  }
  // 顶层声明
  const visitTop = (node) => {
    if (ts.isVariableStatement(node)) {
      for (const d of node.declarationList.declarations) {
        if (ts.isIdentifier(d.name) && d.initializer) {
          if (ts.isStringLiteral(d.initializer)) rec.consts.set(d.name.text, d.initializer.text);
          else if (ts.isObjectLiteralExpression(d.initializer)) {
            const m = new Map();       // string 字面量值（供 R6 查表）
            const em = new Map();      // 表达式文本（供参数一层变量追踪）
            for (const p of d.initializer.properties) {
              if (ts.isPropertyAssignment(p) && ts.isIdentifier(p.name)) {
                if (ts.isStringLiteral(p.initializer)) m.set(p.name.text, p.initializer.text);
                em.set(p.name.text, p.initializer.getText(sourceFile));
              }
            }
            rec.constObjs.set(d.name.text, m);
            rec.constObjExprs.set(d.name.text, em);
          }
          else if (ts.isArrayLiteralExpression(d.initializer)) {
            rec.constArrays.set(d.name.text, d.initializer);   // 存节点，求值器统一处理
          }
          else if (ts.isArrowFunction(d.initializer)) {
            rec.constFns.set(d.name.text, d.initializer);      // const isTypeA = (x) => {...}
          }
        }
      }
    }
    if (isStructLike(node) && node.name) {
      const cls = { decorators: node.modifiers?.filter(ts.isDecorator).map(d => d.getText(sourceFile)) || [], props: new Map(), arrayProps: new Map(), methods: new Map() };
      for (const m of node.members) {
        if (ts.isPropertyDeclaration(m) && ts.isIdentifier(m.name) && m.initializer) {
          if (ts.isStringLiteral(m.initializer)) cls.props.set(m.name.text, m.initializer.text);
          else if (ts.isArrayLiteralExpression(m.initializer)) cls.arrayProps.set(m.name.text, m.initializer);
        }
        if (ts.isMethodDeclaration(m) && m.name) cls.methods.set(m.name.getText(sourceFile), m);
      }
      rec.classes.set(node.name.text, cls);
      if (node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) rec.exports.add(node.name.text);
    }
    if (ts.isFunctionDeclaration(node) && node.name) {
      rec.functions.set(node.name.text, node);
      if (node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) rec.exports.add(node.name.text);
    }
    if (ts.isExportDeclaration(node) || node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) {
      if (ts.isVariableStatement(node)) for (const d of node.declarationList.declarations)
        if (ts.isIdentifier(d.name)) rec.exports.add(d.name.text);
    }
  };
  sourceFile.statements.forEach(visitTop);
  return rec;
}

// 常量传播：Identifier / this.x / 对象取值
function resolveString(expr, fileRec, currentClass, fileIndex, root, depth = 0) {
  if (!expr || depth > 4) return null;
  if (ts.isStringLiteral(expr)) return expr.text;
  if (ts.isIdentifier(expr)) {
    if (fileRec.consts.has(expr.text)) return fileRec.consts.get(expr.text);
    // 跨文件：import { X } from './y'
    const imp = fileRec.imports.get(expr.text);
    if (imp && !imp.ns) {
      const target = resolveImport(root, fileRec.file, imp.spec);
      if (target && fileIndex.has(target)) {
        const t = fileIndex.get(target);
        if (t.consts.has(imp.imported)) return t.consts.get(imp.imported);
      }
    }
    return null;
  }
  if (ts.isPropertyAccessExpression(expr) && expr.expression.kind === ts.SyntaxKind.ThisKeyword) {
    return fileRec.classes.get(currentClass)?.props.get(expr.name.text) ?? null;
  }
  // constObj.key / constObj[idx]（R6 查表：key 已知才可解析，动态 idx 返回 null）
  if (ts.isPropertyAccessExpression(expr) && ts.isIdentifier(expr.expression)) {
    const obj = fileRec.constObjs.get(expr.expression.text);
    if (obj) return obj.get(expr.name.text) ?? null;
  }
  if (ts.isElementAccessExpression(expr) && ts.isIdentifier(expr.expression)) {
    const obj = fileRec.constObjs.get(expr.expression.text);
    if (obj && ts.isStringLiteral(expr.argumentValue)) return obj.get(expr.argumentValue.text) ?? null;
  }
  return null;
}

// 路径解析结果缓存：同一 (fromFile, spec) 只做一次 fs.existsSync 探测。
// 单进程单次扫描，模块级缓存无跨调用残留问题。
const _importCache = new Map();
function resolveImport(root, fromFile, spec) {
  if (!spec.startsWith('.')) return null;
  const key = root + '\u0000' + fromFile + '\u0000' + spec;
  const hit = _importCache.get(key);
  if (hit !== undefined) return hit;   // 含缓存了 null 的情况
  const base = path.resolve(path.dirname(path.join(root, fromFile)), spec);
  let result = null;
  for (const cand of [base, base + '.ets', base + '.ts', path.join(base, 'index.ets'), path.join(base, 'index.ts')]) {
    const relp = rel(root, cand);
    if (fs.existsSync(cand) && !relp.startsWith('..')) { result = relp; break; }
  }
  _importCache.set(key, result);
  return result;
}

// ---------- i18n 访问器（R5 函数摘要） ----------
export function findAccessors(sourceFile, fileRec) {
  // 函数/静态方法体内以“自身参数”调用按名 API → 访问器
  const found = [];
  const checkFn = (fnName, className, params, body) => {
    if (!body) return;
    const hit = { name: fnName, className, paramIndex: -1, paramName: null, file: fileRec.file };
    let matched = false;
    const walk = (node) => {
      if (ts.isCallExpression(node)) {
        const callee = node.expression;
        const prop = ts.isPropertyAccessExpression(callee) ? callee.name.text : (ts.isIdentifier(callee) ? callee.text : null);
        if (prop && BYNAME.has(prop) && node.arguments.length > 0) {
          const a0 = node.arguments[0];
          if (ts.isIdentifier(a0)) {
            const idx = params.findIndex(p => p.name.getText(sourceFile) === a0.text);
            if (idx >= 0) { hit.paramIndex = idx; hit.paramName = a0.text; matched = true; }
          }
        }
      }
      ts.forEachChild(node, walk);
    };
    walk(body);
    if (matched) found.push(hit);
  };
  for (const [name, fn] of fileRec.functions) checkFn(name, null, fn.parameters, fn.body);
  for (const [clsName, cls] of fileRec.classes) {
    // 类方法在顶层 collect 时未存 node，这里重扫一遍 class
    for (const st of sourceFile.statements) {
      if (isStructLike(st) && st.name?.text === clsName) {
        for (const m of st.members) {
          if (ts.isMethodDeclaration(m) && m.name)
            checkFn(m.name.getText(sourceFile), clsName, m.parameters, m.body);
        }
      }
    }
  }
  return found;
}

// ---------- 上下文分类 ----------
function classify(sourceFile, node, currentClass, currentFn) {
  const tags = []; let conditionText = null; let dialogNode = null;
  let p = node.parent;
  let fn = currentFn, cls = currentClass;
  while (p) {
    if (ts.isIfStatement(p) || ts.isConditionalExpression(p)) {
      const cond = p.expression ?? p.condition;
      if (!conditionText && cond) conditionText = cond.getText(sourceFile).replace(/\s+/g, ' ').slice(0, 120);
      if (!tags.includes('conditional')) tags.push('conditional');
    }
    if (ts.isCallExpression(p)) {
      // 廉价判断：先看 callee 是不是 property access，且 name 命中 dialog 方法名集合，
      // 避免对每个祖先 CallExpression 都做 getText() 字符串匹配（万级文件的大头）。
      if (!dialogNode && ts.isPropertyAccessExpression(p.expression)) {
        const mname = p.expression.name.text;
        const recvText = ts.isIdentifier(p.expression.expression) ? p.expression.expression.text : null;
        // showToast/showDialog/openCustomDialog/showActionMenu 等方法名；
        // AlertDialog.show / CustomDialogController 等 receiver 为 dialog 控制器的 .show
        if (DIALOG_METHODS.has(mname) || (mname === 'show' && (recvText === 'AlertDialog' || /Dialog/i.test(recvText || '')))) {
          dialogNode = p;
          if (!tags.includes('click')) tags.push('click');
        }
      }
      if (ts.isPropertyAccessExpression(p.expression)) {
        const m = /^on[A-Z]/.exec(p.expression.name.text);
        if (m && p.expression.name.text !== 'onAppear' && p.expression.name.text !== 'onDisappear') {
          if (!tags.includes('runtime')) tags.push('runtime');
        }
        if (['catch', 'then', 'finally'].includes(p.expression.name.text) && !tags.includes('runtime')) tags.push('runtime');
      }
    }
    if (isStructLike(p) && p.name) {
      const decos = p.modifiers?.filter(ts.isDecorator).map(d => d.getText(sourceFile)).join(' ') || '';
      if (decos.includes('CustomDialog') && !tags.includes('click')) tags.push('click');
      cls = p.name.text;
    }
    if ((ts.isMethodDeclaration(p) || ts.isFunctionDeclaration(p) || ts.isArrowFunction(p)) && p.name) fn = p.name.getText(sourceFile);
    p = p.parent;
  }
  // 优先级：click > conditional > runtime > visible
  const hint = tags.includes('click') ? 'click' : tags.includes('conditional') ? 'conditional'
    : tags.includes('runtime') ? 'runtime' : 'visible';
  return { tags, hint, conditionText, fn, cls, dialogNode };
}

// ---------- 触发标签提取：从 onClick 回调向上找挂载组件（Button/Text）的标签 ----------
const STYLE_CHAIN = new Set(['width', 'height', 'margin', 'fontSize', 'backgroundColor', 'borderRadius',
  'padding', 'fontWeight', 'fontColor', 'layoutWeight', 'alignItems', 'justifyContent', 'border', 'opacity']);

function labelOfArg(arg0) {
  if (!arg0) return null;
  if (ts.isCallExpression(arg0) && ts.isIdentifier(arg0.expression) && arg0.expression.text === '$r') {
    const r = arg0.arguments[0];
    if (ts.isStringLiteral(r)) { const m = /^app\.string\.([A-Za-z_][\w]*)$/.exec(r.text); if (m) return { viaKey: m[1] }; }
  }
  if (ts.isStringLiteral(arg0)) return { viaText: arg0.text };
  // 无变量模板串：纯文案
  if (ts.isNoSubstitutionTemplateLiteral(arg0)) return { viaText: arg0.text };
  if (ts.isTemplateExpression(arg0)) {
    // 多变量模板：`路由项${type}${idx}` / `高级项${item.kind}${idx}`
    // viaVars 存各 span 的表达式文本（Identifier 或成员访问 item.kind），
    // 由谓词求值器在 ForEach 参数环境中逐个求值。
    const parts = { prefix: arg0.head.text, vars: [] };
    for (const span of arg0.templateSpans) {
      const e = span.expression;
      if (ts.isIdentifier(e) || ts.isPropertyAccessExpression(e)) {
        parts.vars.push(e.getText().replace(/\s+/g, ''));
      } else {
        return { viaTemplate: arg0.head.text, viaVar: null };   // 复杂表达式 → 只留前缀
      }
    }
    return { viaTemplate: parts.prefix, viaVars: parts.vars, viaVar: parts.vars.length === 1 ? parts.vars[0] : null };
  }
  // 三元条件文案：Text(cond ? '文案A' : '文案B')
  if (ts.isConditionalExpression(arg0)) {
    const whenTrue = ts.isStringLiteral(arg0.whenTrue) ? arg0.whenTrue.text : null;
    const whenFalse = ts.isStringLiteral(arg0.whenFalse) ? arg0.whenFalse.text : null;
    if (whenTrue !== null && whenFalse !== null) {
      return { viaTernary: { condition: arg0.condition.getText().replace(/\s+/g, ''), whenTrue, whenFalse } };
    }
  }
  return null;
}

export function climbTriggerLabel(node, sourceFile) {
  // 向上：找最近的 onXxx 回调宿主组件，如 Button($r(...)).width(...).onClick(() => {...})
  let p = node.parent, arrow = null;
  while (p) {
    if (ts.isArrowFunction(p) || ts.isFunctionExpression(p)) { arrow = p; break; }
    if (isStructLike(p) || ts.isSourceFile(p)) return null;
    p = p.parent;
  }
  if (!arrow) return null;
  const call = arrow.parent;
  if (!ts.isCallExpression(call) || !ts.isPropertyAccessExpression(call.expression)) return null;
  if (!/^on[A-Z]/.test(call.expression.name.text)) return null;
  let inner = call.expression.expression;
  while (ts.isCallExpression(inner) && ts.isPropertyAccessExpression(inner.expression)
    && STYLE_CHAIN.has(inner.expression.name.text)) {
    inner = inner.expression.expression;
  }
  if (ts.isCallExpression(inner) && ts.isIdentifier(inner.expression) && inner.arguments.length > 0) {
    return labelOfArg(inner.arguments[0]);
  }
  return null;
}

// ---------- 路由参数解析（NavPathStack.pushPathByName/pushPath 的 param） ----------
// 返回 { literal: {key: exprText} }（对象字面量，或一层变量追踪到 const 对象字面量）
// 或 { varName }（参数是变量但追踪不到字面量）或 null（无法解析）。
function extractParam(arg, fileRec) {
  if (!arg) return null;
  // 剥掉 as 类型断言：{ index: idx } as NavDetailParam
  if (ts.isAsExpression(arg)) arg = arg.expression;
  if (ts.isTypeAssertionExpression(arg)) arg = arg.expression;
  if (ts.isObjectLiteralExpression(arg)) {
    const literal = {};
    for (const p of arg.properties) {
      if (ts.isPropertyAssignment(p) && ts.isIdentifier(p.name)) {
        literal[p.name.text] = p.initializer.getText();
      }
    }
    return { literal };
  }
  if (ts.isIdentifier(arg)) {
    // 一层变量追踪：const p = {index: idx}; pushByName('X', p)
    const em = fileRec.constObjExprs?.get(arg.text);
    if (em) {
      const literal = {};
      for (const [k, v] of em) literal[k] = v;
      return { literal };
    }
    return { varName: arg.text };
  }
  if (ts.isPropertyAccessExpression(arg)) {
    return { varName: arg.getText() };
  }
  return null;
}

// ---------- 条件路由识别：if (var === 'X') pushPathByName('RouteA') ----------
// 从 pushPathByName/pushPath 调用向上找到包裹它的 if 条件，返回 { varName, value, negated }
// 例：if (type === 'A') push('A') → { condition, inElse }
//     if (isTypeA(item)) push('A') → { condition: isTypeA(item), inElse }
// 返回 if 的条件表达式 AST + 调用是否在 else 分支；求值交给 predicate_eval。
function routeConditionOf(node, sourceFile) {
  let p = node.parent;
  while (p) {
    if (ts.isIfStatement(p)) {
      const inElse = !!p.elseStatement && isDescendantOf(node, p.elseStatement);
      return { condition: p.expression, inElse, ifNode: p };
    }
    if (ts.isArrowFunction(p) || ts.isFunctionExpression(p) || isStructLike(p) || ts.isSourceFile(p)) break;
    p = p.parent;
  }
  return null;
}

// 判断 node 是否是 ancestor 的后代
function isDescendantOf(node, ancestor) {
  if (!ancestor) return false;
  let p = node.parent;
  while (p) {
    if (p === ancestor) return true;
    p = p.parent;
  }
  return false;
}

// 解析 ForEach 数据源：ForEach(this.routes, (type, idx) => ...) → { sourceNode, sourceExpr, paramNames }
function forEachContextOf(arrowOrNode, sourceFile) {
  // 从某个节点向上找 ForEach 调用，提取数据源与回调参数名
  let p = arrowOrNode;
  while (p) {
    if (ts.isCallExpression(p) && ts.isIdentifier(p.expression) && p.expression.text === 'ForEach') {
      const args = p.arguments;
      if (args.length >= 2 && (ts.isArrowFunction(args[1]) || ts.isFunctionExpression(args[1]))) {
        const cb = args[1];
        const paramNames = cb.parameters.map(pp => ts.isIdentifier(pp.name) ? pp.name.text : null).filter(Boolean);
        return { sourceNode: args[0], sourceExpr: args[0].getText(sourceFile).replace(/\s+/g, ''), paramNames };
      }
    }
    if (isStructLike(p) || ts.isSourceFile(p)) break;
    p = p.parent;
  }
  return null;
}

// ---------- 主流程 ----------
function main() {
  const root = process.argv[2]; if (!root || !fs.existsSync(root)) fail('工程根不存在: ' + root);
  const outPath = process.argv[3];
  const files = collectSourceFiles(root);
  const pages = discoverPages(root);
  const pageByFile = new Map(pages.filter(p => p.exists).map(p => [p.file, p.name]));

  // P5 增量缓存：文件未变则直接复用上次结果（跳过 createProgram + 全量遍历）
  const cached = tryLoadCache(root, files);
  if (cached) {
    const json = JSON.stringify(cached);
    if (outPath) fs.writeFileSync(outPath, json); else process.stdout.write(json);
    return;
  }

  // P2：优先用 Program + checker（语义分析，跨文件符号/常量求值），
  //     官方 TS 模式（无 struct 原生支持）回退纯语法扫描。
  const prog = buildProgram(root);
  const checker = prog?.checker ?? null;
  const program = prog?.program ?? null;

  const parsed = [];
  const doPreprocess = needsStructPreprocess();   // fork 版原生支持 struct，无需预处理
  for (const f of files) {
    const raw = readText(f); if (raw == null) continue;
    // 优先复用 Program 里的 sourceFile（checker 能解析其符号）；回退则独立 createSourceFile
    const abs = path.resolve(f);
    let src = program?.getSourceFile(abs) ?? null;
    if (!src) {
      src = ts.createSourceFile(f, doPreprocess ? preprocess(raw) : raw, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    }
    const rec = analyzeFile(root, f, src);
    parsed.push({ abs: f, src, rec });
  }
  const fileIndex = new Map(parsed.map(p => [p.rec.file, p.rec]));

  // 访问器总表（含跨文件）
  const accessors = []; // {name, className, paramIndex, file}
  for (const p of parsed) accessors.push(...findAccessors(p.src, p.rec));

  // 访问器索引：Map 化，O(1) 查找，替代 matchAccessor 里的线性扫描
  const funcAccessors = new Map();   // file\u0000name -> accessor（顶层函数）
  const methodAccessors = new Map(); // file\u0000className\u0000methodName -> accessor（类方法）
  for (const a of accessors) {
    if (a.className) methodAccessors.set(a.file + '\u0000' + a.className + '\u0000' + a.name, a);
    else funcAccessors.set(a.file + '\u0000' + a.name, a);
  }

  // 访问器匹配：本地函数/类 或 经 import 解析到定义文件（均 O(1) Map 查找）
  function matchAccessor(rec, callee) {
    if (ts.isIdentifier(callee)) {
      const name = callee.text;
      const local = funcAccessors.get(rec.file + '\u0000' + name);
      if (local) return local;
      const imp = rec.imports.get(name);
      if (imp) {
        const target = resolveImport(root, rec.file, imp.spec);
        if (target) return funcAccessors.get(target + '\u0000' + imp.imported) || null;
      }
      return null;
    }
    if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression)) {
      const clsName = callee.expression.text;
      const methodName = callee.name.text;
      const local = methodAccessors.get(rec.file + '\u0000' + clsName + '\u0000' + methodName);
      if (local) return local;
      const imp = rec.imports.get(clsName);
      if (imp) {
        const target = resolveImport(root, rec.file, imp.spec);
        if (target) return methodAccessors.get(target + '\u0000' + imp.imported + '\u0000' + methodName) || null;
      }
    }
    return null;
  }

  const usages = []; const dynamicRefs = []; const edges = []; const toggles = [];
  for (const p of parsed) {
    const { src, rec } = p;
    let currentClass = null, currentFn = null;
    const classStack = [], fnStack = [];
    const enter = (node) => {
      if (isStructLike(node) && node.name) { currentClass = node.name.text; classStack.push(currentClass); }
      if ((ts.isMethodDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isArrowFunction(node)) ) {
        currentFn = node.name ? node.name.getText(src) : (ts.isArrowFunction(node) && ts.isVariableDeclaration(node.parent) ? node.parent.name.getText(src) : currentFn);
        fnStack.push(currentFn);
      }
    };
    const exit = (node) => {
      if (isStructLike(node)) { classStack.pop(); currentClass = classStack[classStack.length - 1] ?? null; }
      if (ts.isMethodDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isArrowFunction(node)) { fnStack.pop(); currentFn = fnStack[fnStack.length - 1] ?? null; }
    };
    const pushUsage = (node, key, rule) => {
      const { tags, hint, conditionText, fn, cls, dialogNode } = classify(src, node, currentClass, currentFn);
      const { line } = src.getLineAndCharacterOfPosition(node.getStart(src));
      usages.push({
        key, rule, file: rec.file, line: line + 1,
        lineText: node.getText(src).replace(/\s+/g, ' ').slice(0, 160),
        triggers: tags, triggerHint: hint, conditionText, struct: cls, method: fn,
        _src: src, _dialogNode: dialogNode,
      });
    };
    const pushDynamic = (node, expr) => {
      const { line } = src.getLineAndCharacterOfPosition(node.getStart(src));
      dynamicRefs.push({ expr, file: rec.file, line: line + 1, lineText: expr });
    };

    const walk = (node) => {
      enter(node);
      if (ts.isCallExpression(node)) {
        const callee = node.expression;
        const calleeProp = ts.isPropertyAccessExpression(callee) ? callee.name.text : null;
        const calleeId = ts.isIdentifier(callee) ? callee.text : null;
        const args = node.arguments;
        // R1/R2：$r('app.string.x') 或 getStringSync($r(...).id, ...)
        if (calleeId === '$r' && args.length > 0 && ts.isStringLiteral(args[0])) {
          const m = /^app\.string\.([A-Za-z_][\w]*)$/.exec(args[0].text);
          if (m) pushUsage(node, m[1], 'R1_$r');
        }
        if (calleeProp && ID_APIS.has(calleeProp) && args.length > 0 &&
          ts.isPropertyAccessExpression(args[0]) && args[0].name.text === 'id' &&
          ts.isCallExpression(args[0].expression) && ts.isIdentifier(args[0].expression.expression) &&
          args[0].expression.expression.text === '$r') {
          const r = args[0].expression.arguments[0];
          if (ts.isStringLiteral(r)) {
            const m = /^app\.string\.([A-Za-z_][\w]*)$/.exec(r.text);
            if (m) pushUsage(node, m[1], 'R2_id');
          }
        }
        // R3/R4：按名 API
        if (calleeProp && BYNAME.has(calleeProp) && args.length > 0) {
          const a0 = args[0];
          if (ts.isStringLiteral(a0)) pushUsage(node, a0.text, 'R3_byname');
          else {
            // 访问器定义内部的“参数转发”不是动态引用（调用点已由 R5 解析）
            const inAccessorBody = accessors.some(a => a.file === rec.file
              && (a.className ?? null) === (currentClass ?? null) && a.name === currentFn
              && ts.isIdentifier(a0) && a.paramName === a0.text);
            if (inAccessorBody) { ts.forEachChild(node, walk); exit(node); return; }
            const resolved = resolveString(a0, rec, currentClass, fileIndex, root);
            if (resolved != null) pushUsage(node, resolved, 'R4_const');
            else pushDynamic(node, a0.getText(src).replace(/\s+/g, ' ').slice(0, 80));
          }
        }
        // R5/R6：i18n 封装调用 —— 廉价预判：callee 须是本地函数/import 或 类方法/import 类，
        // 否则（ArkUI 组件 Text/Column、链式 .width()/.fontSize() 等）直接跳过，不做查表。
        const maybeFn = calleeId ? (rec.functions.has(calleeId) || rec.imports.has(calleeId)) : false;
        const maybeMethod = (calleeProp && ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression))
          ? (rec.classes.has(callee.expression.text) || rec.imports.has(callee.expression.text)) : false;
        if (args.length > 0 && (maybeFn || maybeMethod)) {
          const acc = matchAccessor(rec, callee);
          if (acc) {
            const a = args[acc.paramIndex];
            if (ts.isStringLiteral(a)) pushUsage(node, a.text, 'R5_wrapper');
            else {
              const resolved = resolveString(a, rec, currentClass, fileIndex, root);
              if (resolved != null) pushUsage(node, resolved, 'R5_wrapper');
              else pushDynamic(node, callee.getText(src).replace(/\s+/g, ' ') + '(' + a.getText(src).replace(/\s+/g, ' ').slice(0, 60) + ')');
            }
          }
        }
        // 状态开关采集：onXxx 回调内对 this.X 赋值 → 记录 {varName, 触发按钮标签}
        if (calleeProp && /^on[A-Z]/.test(calleeProp) && args.length > 0 &&
            (ts.isArrowFunction(args[0]) || ts.isFunctionExpression(args[0])) && args[0].body) {
          const bodyText = args[0].body.getText(src);
          const vars = [...bodyText.matchAll(/this\.([A-Za-z_]\w*)\s*=(?![=>])/g)].map(m => m[1]);
          if (vars.length) {
            const label = climbTriggerLabel(args[0].body.statements[0] ?? args[0].body, src);
            for (const v of new Set(vars)) toggles.push({ file: rec.file, varName: v, viaKey: label?.viaKey ?? null, viaText: label?.viaText ?? null });
          }
        }
        // 导航边：router.pushUrl/replaceUrl({url:'pages/X'})、NavPathStack.pushPathByName/pushPath
        const apiName = calleeProp || calleeId || '';
        if (['pushUrl', 'replaceUrl'].includes(apiName) && args.length > 0 && ts.isObjectLiteralExpression(args[0])) {
          const via = climbTriggerLabel(node, src);
          for (const prop of args[0].properties) {
            if (ts.isPropertyAssignment(prop) && prop.name.getText(src) === 'url' && ts.isStringLiteral(prop.initializer)) {
              edges.push({ from: rec.file, to: prop.initializer.text, api: apiName,
                viaKey: via?.viaKey ?? null, viaText: via?.viaText ?? null, param: null });
            }
          }
        }
        // pushPathByName('Name'[, param])：NavDestination 系统路由表方案，name 为路由名
        if (apiName === 'pushPathByName' && args.length > 0) {
          const via = climbTriggerLabel(node, src);
          const param = extractParam(args[1], rec);
          const routeCond = routeConditionOf(node, src);
          const fe = forEachContextOf(node, src);   // ForEach 上下文查找独立于 if 条件
          const baseEdge = {
            from: rec.file, api: 'pushPathByName',
            viaKey: via?.viaKey ?? null, viaText: via?.viaText ?? null,
            viaTemplate: via?.viaTemplate ?? null, viaVar: via?.viaVar ?? null,
            viaVars: via?.viaVars ?? null,
            viaTernary: via?.viaTernary ?? null,
            param, forEach: fe, struct: currentClass,
          };
          // P3-b：动态路由名 —— pushPathByName(cond ? 'A' : 'B', param)
          // 拆成两条边（真假分支），复用条件路由枚举。
          if (ts.isConditionalExpression(args[0])) {
            const cond = args[0];
            const whenTrue = cond.whenTrue, whenFalse = cond.whenFalse;
            if (ts.isStringLiteral(whenTrue)) {
              edges.push({ ...baseEdge, to: whenTrue.text,
                routeCond: { condition: cond.condition, inElse: false } });
            }
            if (ts.isStringLiteral(whenFalse)) {
              edges.push({ ...baseEdge, to: whenFalse.text,
                routeCond: { condition: cond.condition, inElse: true } });
            }
          } else if (ts.isStringLiteral(args[0])) {
            edges.push({ ...baseEdge, to: args[0].text, routeCond });
          }
        }
        // pushPath({name:'Name', param:{...}}) 或 pushPath('Name', param)：兼容两种形态
        if (apiName === 'pushPath' && args.length > 0) {
          const via = climbTriggerLabel(node, src);
          let to = null, param = null;
          if (ts.isObjectLiteralExpression(args[0])) {
            for (const prop of args[0].properties) {
              if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && prop.name.text === 'name'
                  && ts.isStringLiteral(prop.initializer)) to = prop.initializer.text;
              if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && prop.name.text === 'param') {
                param = extractParam(prop.initializer, rec);
              }
            }
          } else if (ts.isStringLiteral(args[0])) {
            to = args[0].text; param = extractParam(args[1], rec);
          }
          if (to) edges.push({ from: rec.file, to, api: 'pushPath',
            viaKey: via?.viaKey ?? null, viaText: via?.viaText ?? null, param });
        }
      }
      ts.forEachChild(node, walk);
      exit(node);
    };
    walk(src);
  }

  // import 图与 key→页面 归属
  const importers = new Map(); // file -> Set(importers)
  for (const p of parsed) {
    for (const [, imp] of p.rec.imports) {
      const target = resolveImport(root, p.rec.file, imp.spec);
      if (target) {
        if (!importers.has(target)) importers.set(target, new Set());
        importers.get(target).add(p.rec.file);
      }
    }
  }
  const owningPagesCache = new Map();
  function owningPages(file) {
    // 该文件被哪些页面（直接或经 import 链）使用；结果 memoize：
    // 被多个 key 共用的公共组件文件只做一次 BFS。
    const cached = owningPagesCache.get(file);
    if (cached !== undefined) return cached;
    const seen = new Set(); const result = new Set(); const queue = [file];
    let qi = 0;   // 用索引推进替代 Array.shift()，避免大图下 O(n) 退化
    while (qi < queue.length) {
      const f = queue[qi++];
      if (seen.has(f)) continue;
      seen.add(f);
      if (pageByFile.has(f)) result.add(pageByFile.get(f));
      for (const im of importers.get(f) || []) if (!seen.has(im)) queue.push(im);
    }
    const out = [...result];
    owningPagesCache.set(file, out);
    return out;
  }
  // 页面可达深度（BFS，入口 = pages[0]）
  const pageNames = pages.map(p => p.name);
  const depth = new Map(); const entry = pageNames[0];
  if (entry != null) {
    depth.set(entry, 0);
    // 正向 BFS：边来源文件 → 所在页面
    const pageOfFile = new Map(pages.filter(p => p.exists).map(p => [p.file, p.name]));
    const adj = new Map();
    for (const e of edges) {
      if (!pageNames.includes(e.to)) continue;
      for (const srcPage of pageOfFile.has(e.from) ? [pageOfFile.get(e.from)] : owningPages(e.from)) {
        if (!adj.has(srcPage)) adj.set(srcPage, new Set());
        adj.get(srcPage).add(e.to);
      }
    }
    const q = [entry];
    while (q.length) {
      const cur = q.shift();
      for (const nxt of adj.get(cur) || []) {
        if (!depth.has(nxt)) { depth.set(nxt, depth.get(cur) + 1); q.push(nxt); }
      }
    }
  }
  const keyPages = {};
  const byKeyFiles = new Map();
  for (const u of usages) {
    if (!byKeyFiles.has(u.key)) byKeyFiles.set(u.key, new Set());
    byKeyFiles.get(u.key).add(u.file);
  }
  for (const [key, fsSet] of byKeyFiles) {
    const s = new Set();
    for (const f of fsSet) for (const pg of owningPages(f)) s.add(pg);
    if (s.size === 0) continue;   // 无页面归属（工程片段/非页面文件）不产出
    keyPages[key] = [...s].map(pg => ({ page: pg, depth: depth.has(pg) ? depth.get(pg) : null })).sort((a, b) => (a.depth ?? 99) - (b.depth ?? 99));
  }

  // ---- 触发信息富集（弹窗触发按钮 / 条件变量对应的开关按钮 / 路由参数条件）----
  for (const u of usages) {
    if (u._dialogNode && u._src) {
      const label = climbTriggerLabel(u._dialogNode, u._src);
      if (label) { u.triggerViaKey = label.viaKey ?? null; u.triggerViaText = label.viaText ?? null; }
    }
    if (u.conditionText) {
      const vars = [...u.conditionText.matchAll(/this\.([A-Za-z_]\w*)/g)].map(m => m[1]);
      for (const v of new Set(vars)) {
        const t = toggles.find(t => t.file === u.file && t.varName === v);
        if (t) { u.toggleVar = v; u.toggleViaKey = t.viaKey; u.toggleViaText = t.viaText; break; }
      }
      // 路由参数条件：if (this.index === N) —— 记录 paramVar 与 paramValue，
      // 供自动场景生成时结合导航边的 param 推导"点击哪个列表项带这个参数"。
      const pm = /this\.([A-Za-z_]\w*)\s*===?\s*(-?\d+)/.exec(u.conditionText);
      if (pm) { u.paramVar = pm[1]; u.paramValue = pm[2]; }
    }
    delete u._src; delete u._dialogNode;
  }

  // ---- 条件路由 → 点击文案枚举（不同条件进不同路由）----
  // 对每条含 routeCond 的边，用谓词求值器对 ForEach 数据源逐项求值，
  // 解析出"点哪个列表项能进这个路由"。
  // 链路：if 条件（含函数调用）+ ForEach 数据源 + 列表项文案模板 + 边 viaVars。
  for (const e of edges) {
    const rc = e.routeCond, fe = e.forEach;
    if (!rc || !fe) continue;
    if (!e.viaVars && !e.viaTernary) continue;   // 无文案模板也无三元文案，跳过
    const evalCtx = {
      file: e.from, cls: e.struct, root,
      fileIndex, resolveImport,
      checker,   // P2：注入 checker，求值器跨文件符号解析/常量求值走语义分析
      resolveSymbol, constantValue,
    };
    const items = evaluateRouteItems({
      conditionNode: rc.condition,
      sourceNode: fe.sourceNode,
      paramNames: fe.paramNames,
      inElse: rc.inElse,
      viaTemplate: e.viaTemplate,
      viaVars: e.viaVars,
      viaTernary: e.viaTernary,
    }, evalCtx);
    if (items) e.viaItems = items;
  }

  // ---- 页面级邻接表（含触发标签 + 路由参数 + 条件路由点击文案，供自动场景生成 BFS 用）----
  const pageAdj = {};
  {
    const pageOfFile = new Map(pages.filter(p => p.exists).map(p => [p.file, p.name]));
    for (const e of edges) {
      if (!pages.some(p => p.name === e.to)) continue;
      const srcPages = pageOfFile.has(e.from) ? [pageOfFile.get(e.from)] : owningPages(e.from);
      for (const sp of srcPages) {
        (pageAdj[sp] = pageAdj[sp] || []).push({
          to: e.to, viaKey: e.viaKey ?? null, viaText: e.viaText ?? null,
          viaTemplate: e.viaTemplate ?? null, viaVar: e.viaVar ?? null,
          viaItems: e.viaItems ?? null,
          param: e.param ?? null,
        });
      }
    }
  }

  // 清理 edges 里的 AST 节点（求值已用完），避免 JSON 循环引用；
  // 保留 routeCond 的文本摘要（condition 源码 + inElse）供调试/测试。
  const cleanEdges = edges.map(e => {
    const c = { ...e };
    if (c.routeCond) {
      c.routeCond = { conditionText: c.routeCond.condition.getText(), inElse: c.routeCond.inElse };
    }
    delete c.forEach;       // 内含 sourceNode AST 节点
    delete c.struct;        // 仅求值用
    return c;
  });

  const result = {
    backend: 'ast', projectRoot: path.resolve(root), files: parsed.length,
    usages, dynamicRefs,
    accessors: accessors.map(a => ({ name: a.name, className: a.className, paramIndex: a.paramIndex, file: a.file })),
    pages: pages.map(p => ({ name: p.name, file: p.file, exists: p.exists, depth: depth.has(p.name) ? depth.get(p.name) : null, via: p.via ?? null })),
    edges: cleanEdges, keyPages, pageAdj, toggles,
  };
  // P5 增量缓存：写入结果供下次复用
  writeCache(root, files, result);
  const json = JSON.stringify(result, null, 1);
  if (outPath) fs.writeFileSync(outPath, json); else process.stdout.write(json);
}

if (isMain) main();
