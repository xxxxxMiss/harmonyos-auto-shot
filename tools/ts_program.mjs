/**
 * TS Program + checker 封装（P2 阶段）。
 *
 * 用 TypeScript Compiler API 的语义分析能力，替代手写的跨文件符号解析与常量求值：
 *  - buildProgram：构建 Program（含 .ets 后缀模块解析、容忍 @kit 外部模块解析失败）
 *  - resolveSymbol：getSymbolAtLocation + getAliasedSymbol，跨文件拿到真正定义
 *  - constantValue：checker.getConstantValue，支持字面量/const/枚举成员
 *
 * 注意：ArkTS 工程的装饰器（@Component/@Entry/@State）与 @kit 模块在 TS 语义层
 * 无法解析（产生诊断但不影响符号解析），因此"语法层收集"（decorators 文本、属性
 * 初始值等）仍由 ast_scan 的 analyzeFile 负责，checker 只负责跨文件符号 + 常量。
 */
import fs from 'fs';
import path from 'path';
import { loadTypescript, needsStructPreprocess, preprocessStruct } from './ts_loader.mjs';
const ts = loadTypescript().ts;

const PRUNE = new Set(['oh_modules', 'node_modules', 'build', '.hvigor', '.preview',
  '.cxx', '.idea', '.git', 'dist', '.test', 'entry/build']);

export function collectSourceFiles(root) {
  const files = [];
  (function walk(dir) {
    let es; try { es = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of es) {
      if (e.isDirectory()) { if (!PRUNE.has(e.name)) walk(path.join(dir, e.name)); continue; }
      if (e.name.endsWith('.ets') || e.name.endsWith('.ts')) files.push(path.join(dir, e.name));
    }
  })(root);
  return files;
}

// 相对路径（统一 '/' 分隔）
export function rel(root, p) {
  return path.relative(root, p).split(path.sep).join('/');
}

/**
 * 构建 Program + checker。
 * 返回 { program, checker, files(绝对路径), sourceFiles, root }。
 * 注意：官方 TS 模式下会做 struct→class 预处理；但预处理会改变源文本，
 * 导致 checker 的源位置与原始文件不一致。因此当前 P2 仅支持 fork 模式
 * （原生 struct，无需预处理）。官方模式（AUTOSHOT_TS=official）回退纯语法扫描。
 */
export function buildProgram(root) {
  if (needsStructPreprocess()) {
    return null;   // 官方 TS 无 struct 原生支持，checker 语义分析不可靠，交由调用方回退
  }
  const files = collectSourceFiles(root);
  const host = ts.createCompilerHost({});
  const origFileExists = host.fileExists.bind(host);
  const origReadFile = host.readFile.bind(host);
  host.fileExists = (f) => origFileExists(f) || (f.endsWith('.ets') && fs.existsSync(f));
  host.readFile = (f) => f.endsWith('.ets') ? fs.readFileSync(f, 'utf-8') : origReadFile(f);
  // .ets 后缀模块解析：TS 默认只试 .ts/.tsx/.d.ts
  host.resolveModuleNames = (names, containingFile) => names.map((name) => {
    if (!name.startsWith('.')) return undefined;   // 外部 @kit 模块交给默认（解析失败也无妨）
    const base = path.resolve(path.dirname(containingFile), name);
    for (const cand of [base + '.ets', base + '.ts', base + '.d.ts',
      path.join(base, 'index.ets'), path.join(base, 'index.ts')]) {
      if (fs.existsSync(cand)) {
        return { resolvedFileName: cand, extension: path.extname(cand).slice(1), isExternalLibraryImport: false };
      }
    }
    return undefined;
  });

  const program = ts.createProgram(files.map((f) => path.resolve(f)), {
    target: ts.ScriptTarget.Latest,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.NodeJs,
    experimentalDecorators: true,
    noEmit: true,
    skipLibCheck: true,
  }, host);
  const checker = program.getTypeChecker();
  return { program, checker, files, sourceFiles: program.getSourceFiles(), root };
}

/**
 * 解析节点的符号，解 import alias，跨文件拿到真正定义。
 * 返回 { symbol, file(相对), declaration } 或 null。
 */
export function resolveSymbol(checker, node, root) {
  let sym = checker.getSymbolAtLocation(node);
  if (!sym) return null;
  // import { x } from '...' 的标识符是 alias，需解到真正定义
  if (sym.flags & ts.SymbolFlags.Alias) {
    sym = checker.getAliasedSymbol(sym);
  }
  const decl = sym.declarations?.[0] ?? null;
  const file = decl ? rel(root, decl.getSourceFile().fileName) : null;
  return { symbol: sym, file, declaration: decl };
}

/**
 * 常量求值：字面量/const 变量/枚举成员，返回 { value, kind } 或 null。
 * 抽象值 kind 与 predicate_eval 对齐：str/num/bool/null。
 * 枚举成员（ItemLevel.Premium）需走符号路径：resolveSymbol → EnumMember.initializer。
 */
export function constantValue(checker, node) {
  if (!node) return null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return { value: node.text, kind: 'str' };
  if (ts.isNumericLiteral(node)) return { value: parseFloat(node.text), kind: 'num' };
  if (node.kind === ts.SyntaxKind.TrueKeyword) return { value: true, kind: 'bool' };
  if (node.kind === ts.SyntaxKind.FalseKeyword) return { value: false, kind: 'bool' };
  if (node.kind === ts.SyntaxKind.NullKeyword) return { value: null, kind: 'null' };
  // const 变量 / const 断言：checker.getConstantValue
  try {
    const v = checker.getConstantValue(node);
    if (v !== undefined) {
      if (typeof v === 'string') return { value: v, kind: 'str' };
      if (typeof v === 'number') return { value: v, kind: 'num' };
      if (typeof v === 'boolean') return { value: v, kind: 'bool' };
    }
  } catch (_) { /* 继续走枚举符号路径 */ }
  // 枚举成员：ItemLevel.Premium → 符号路径拿 EnumMember.initializer
  if (ts.isPropertyAccessExpression(node)) {
    const sym = checker.getSymbolAtLocation(node.name);
    const target = sym && (sym.flags & ts.SymbolFlags.Alias) ? checker.getAliasedSymbol(sym) : sym;
    const decl = target?.declarations?.[0];
    if (decl && ts.isEnumMember(decl)) {
      if (decl.initializer) return constantValue(checker, decl.initializer);
      // 无 initializer 的枚举成员：按前一个成员 +1（简化：仅支持首个为 0 的递增）
      const parent = decl.parent;
      if (ts.isEnumDeclaration(parent)) {
        const idx = parent.members.indexOf(decl);
        // 求前一个成员的值
        let prev = 0;
        for (let i = 0; i <= idx; i++) {
          const m = parent.members[i];
          if (m.initializer) {
            const cv = constantValue(checker, m.initializer);
            if (cv && cv.kind === 'num') prev = cv.value;
          } else if (i > 0) {
            prev += 1;
          } else {
            prev = 0;
          }
        }
        return { value: prev, kind: 'num' };
      }
    }
  }
  return null;
}

/**
 * 枚举成员解析：node 是 enum 成员（如 Status.VIP）时，返回 { enumName, memberName, value }。
 */
export function enumMemberValue(checker, node, root) {
  const sym = resolveSymbol(checker, node, root);
  if (!sym?.symbol) return null;
  const decl = sym.declaration;
  if (decl && ts.isEnumMember(decl)) {
    const parent = decl.parent;
    if (ts.isEnumDeclaration(parent)) {
      const v = decl.initializer ? constantValue(checker, decl.initializer) : null;
      return { enumName: parent.name.text, memberName: decl.name.getText(), value: v };
    }
  }
  return null;
}
