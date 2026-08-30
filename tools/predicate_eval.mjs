/**
 * 谓词求值器：对"同步可求值"的 ArkTS 表达式/函数体做抽象解释。
 *
 * 用途：条件路由识别（if (isXxx(item)) push(A) else push(B)）与 ForEach 数据源解析
 * （this.getRoutes() / this.items = [{type:'A'},...]），替代早期的正则模式匹配。
 *
 * 设计要点：
 *  - 抽象值：str/num/bool/null/obj/arr，外加 UNKNOWN（求不出 → 调用方回退手写场景）。
 *  - 多语句函数体解释执行：if/else、多 return、局部变量、块作用域、有界 for 循环、arr.push。
 *  - 跨文件：函数/常量经 ctx.resolveImport 定位后求值。
 *  - 深度/迭代上限防递归与死循环。
 *
 * 本文件不 import ast_scan（避免循环），resolveImport 由调用方经 ctx 注入。
 */
import { loadTypescript } from './ts_loader.mjs';
const ts = loadTypescript().ts;

// ---- 抽象值 ----
const STR = (v) => ({ k: 'str', v });
const NUM = (v) => ({ k: 'num', v });
const BOOL = (v) => ({ k: 'bool', v });
const NULL = () => ({ k: 'null' });
const OBJ = (fields) => ({ k: 'obj', fields });   // fields: Map<name, value>
const ARR = (items) => ({ k: 'arr', items });     // items: value[]（可变，供 push）
const UNKNOWN = { k: 'unknown' };
const isUnknown = (v) => !v || v.k === 'unknown';
const truthy = (v) => {
  switch (v?.k) {
    case 'bool': return v.v;
    case 'null': case 'undef': return false;
    case 'num': return v.v !== 0;
    case 'str': return v.v.length > 0;
    case 'arr': case 'obj': return true;
    default: return false;
  }
};
function eq(l, r) {
  if (l.k === 'null' || r.k === 'null') return l.k === r.k;
  if (l.k === 'bool' || r.k === 'bool') return l.k === r.k && l.v === r.v;
  if (l.k !== r.k) return false;
  if (l.k === 'num' || l.k === 'str') return l.v === r.v;
  if (l.k === 'arr' || l.k === 'obj') return l === r;
  return false;
}
function cmp(l, r, fn) {
  if (l.k === 'num' && r.k === 'num') return BOOL(fn(l.v, r.v));
  if (l.k === 'str' && r.k === 'str') return BOOL(fn(l.v, r.v));
  return UNKNOWN;
}

// ---- 表达式求值 ----
export function evalExpr(node, env, ctx) {
  if (!node) return UNKNOWN;
  // 剥断言/括号
  if (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) ||
      ts.isParenthesizedExpression(node) || ts.isNonNullExpression(node)) {
    return evalExpr(node.expression, env, ctx);
  }
  // 字面量
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return STR(node.text);
  if (ts.isNumericLiteral(node)) return NUM(parseFloat(node.text));
  if (node.kind === ts.SyntaxKind.TrueKeyword) return BOOL(true);
  if (node.kind === ts.SyntaxKind.FalseKeyword) return BOOL(false);
  if (node.kind === ts.SyntaxKind.NullKeyword) return NULL();
  // 标识符
  if (ts.isIdentifier(node)) {
    if (env && Object.prototype.hasOwnProperty.call(env, node.text)) return env[node.text];
    const rec = ctx.fileIndex?.get(ctx.file);
    if (rec) {
      if (rec.consts?.has(node.text)) return STR(rec.consts.get(node.text));
      if (rec.constArrays?.has(node.text)) return evalExpr(rec.constArrays.get(node.text), env, ctx);
      // import 常量
      const imp = rec.imports?.get(node.text);
      if (imp && ctx.resolveImport) {
        const target = ctx.resolveImport(ctx.root, rec.file, imp.spec);
        const t = ctx.fileIndex?.get(target);
        if (t?.consts?.has(imp.imported)) return STR(t.consts.get(imp.imported));
        if (t?.constArrays?.has(imp.imported)) return evalExpr(t.constArrays.get(imp.imported), env, { ...ctx, file: target });
      }
    }
    return UNKNOWN;
  }
  // this.xxx（TS AST 里 this 是 ThisKeyword，不是 Identifier）
  // 或 obj.field / arr.length / 枚举成员 Enum.Member
  if (ts.isPropertyAccessExpression(node)) {
    if (node.expression.kind === ts.SyntaxKind.ThisKeyword) {
      const rec = ctx.fileIndex?.get(ctx.file);
      const cls = rec?.classes?.get(ctx.cls);
      if (cls?.props?.has(node.name.text)) return STR(cls.props.get(node.name.text));
      if (cls?.arrayProps?.has(node.name.text)) return evalExpr(cls.arrayProps.get(node.name.text), env, ctx);
      return UNKNOWN;
    }
    // P2：枚举成员（ItemLevel.Premium）→ checker.getConstantValue 求值
    if (ctx.checker && ctx.constantValue) {
      const cv = ctx.constantValue(ctx.checker, node);
      if (cv) return cv.kind === 'str' ? STR(cv.value) : cv.kind === 'num' ? NUM(cv.value) : cv.kind === 'bool' ? BOOL(cv.value) : NULL();
    }
    const objV = evalExpr(node.expression, env, ctx);
    if (isUnknown(objV)) return UNKNOWN;
    if (objV.k === 'obj') return objV.fields.get(node.name.text) ?? UNKNOWN;
    if (objV.k === 'arr' && node.name.text === 'length') return NUM(objV.items.length);
    if (objV.k === 'str' && node.name.text === 'length') return NUM(objV.v.length);
    return UNKNOWN;
  }
  // 元素访问 arr[i] / obj['key']
  if (ts.isElementAccessExpression(node)) {
    const objV = evalExpr(node.expression, env, ctx);
    const idxV = evalExpr(node.argumentExpression, env, ctx);
    if (isUnknown(objV) || isUnknown(idxV)) return UNKNOWN;
    if (objV.k === 'arr' && idxV.k === 'num') return objV.items[idxV.v] ?? UNKNOWN;
    if (objV.k === 'obj' && idxV.k === 'str') return objV.fields.get(idxV.v) ?? UNKNOWN;
    return UNKNOWN;
  }
  // 数组字面量
  if (ts.isArrayLiteralExpression(node)) {
    const items = [];
    for (const el of node.elements) {
      if (ts.isSpreadElement(el)) return UNKNOWN;
      const v = evalExpr(el, env, ctx);
      if (isUnknown(v)) return UNKNOWN;
      items.push(v);
    }
    return ARR(items);
  }
  // 对象字面量
  if (ts.isObjectLiteralExpression(node)) {
    const fields = new Map();
    for (const p of node.properties) {
      if (!ts.isPropertyAssignment(p)) return UNKNOWN;
      const name = ts.isIdentifier(p.name) ? p.name.text : ts.isStringLiteral(p.name) ? p.name.text : null;
      if (!name) return UNKNOWN;
      const v = evalExpr(p.initializer, env, ctx);
      if (isUnknown(v)) return UNKNOWN;
      fields.set(name, v);
    }
    return OBJ(fields);
  }
  // 二元表达式
  if (ts.isBinaryExpression(node)) {
    const l = evalExpr(node.left, env, ctx);
    const r = evalExpr(node.right, env, ctx);
    if (isUnknown(l) || isUnknown(r)) return UNKNOWN;
    return evalBinary(node.operatorToken.kind, l, r);
  }
  // 前缀一元
  if (ts.isPrefixUnaryExpression(node)) {
    if (node.operator === ts.SyntaxKind.ExclamationToken) {
      const v = evalExpr(node.operand, env, ctx);
      return isUnknown(v) ? UNKNOWN : BOOL(!truthy(v));
    }
    if (node.operator === ts.SyntaxKind.MinusToken) {
      const v = evalExpr(node.operand, env, ctx);
      return v.k === 'num' ? NUM(-v.v) : UNKNOWN;
    }
    return UNKNOWN;
  }
  // 条件表达式 ?:
  if (ts.isConditionalExpression(node)) {
    const c = evalExpr(node.condition, env, ctx);
    if (isUnknown(c)) return UNKNOWN;
    return evalExpr(truthy(c) ? node.whenTrue : node.whenFalse, env, ctx);
  }
  // 函数调用
  if (ts.isCallExpression(node)) return evalCall(node, env, ctx);
  return UNKNOWN;
}

function evalBinary(op, l, r) {
  switch (op) {
    case ts.SyntaxKind.EqualsEqualsEqualsToken:
    case ts.SyntaxKind.EqualsEqualsToken:
      return BOOL(eq(l, r));
    case ts.SyntaxKind.ExclamationEqualsEqualsToken:
    case ts.SyntaxKind.ExclamationEqualsToken:
      return BOOL(!eq(l, r));
    case ts.SyntaxKind.AmpersandAmpersandToken:
      return BOOL(truthy(l) && truthy(r));
    case ts.SyntaxKind.BarBarToken:
      return BOOL(truthy(l) || truthy(r));
    case ts.SyntaxKind.GreaterThanToken: return cmp(l, r, (a, b) => a > b);
    case ts.SyntaxKind.GreaterThanEqualsToken: return cmp(l, r, (a, b) => a >= b);
    case ts.SyntaxKind.LessThanToken: return cmp(l, r, (a, b) => a < b);
    case ts.SyntaxKind.LessThanEqualsToken: return cmp(l, r, (a, b) => a <= b);
    case ts.SyntaxKind.PlusToken:
      if (l.k === 'num' && r.k === 'num') return NUM(l.v + r.v);
      if (l.k === 'str' && r.k === 'str') return STR(l.v + r.v);
      return UNKNOWN;
    case ts.SyntaxKind.MinusToken:
      return (l.k === 'num' && r.k === 'num') ? NUM(l.v - r.v) : UNKNOWN;
    case ts.SyntaxKind.AsteriskToken:
      return (l.k === 'num' && r.k === 'num') ? NUM(l.v * r.v) : UNKNOWN;
    case ts.SyntaxKind.SlashToken:
      return (l.k === 'num' && r.k === 'num' && r.v !== 0) ? NUM(l.v / r.v) : UNKNOWN;
    case ts.SyntaxKind.PercentToken:
      return (l.k === 'num' && r.k === 'num' && r.v !== 0) ? NUM(l.v % r.v) : UNKNOWN;
    default: return UNKNOWN;
  }
}

// ---- 函数定位与内联 ----
function paramNames(fn) {
  const names = [];
  for (const p of fn.parameters || []) {
    if (ts.isIdentifier(p.name)) names.push(p.name.text);
    else return null;
  }
  return names;
}
function fnDefOf(fn) {
  if (!fn) return null;
  const names = paramNames(fn);
  if (!names) return null;
  const body = fn.body;
  if (!body) return null;
  // body 是 Block（多语句）或表达式（箭头函数简写）
  return { params: names, body: ts.isBlock(body) ? { statements: body.statements } : { expression: body } };
}

// 定位一个 CallExpression 对应的用户函数定义（本地 class 方法 / 顶层函数 / 箭头函数 / import）
export function findFunction(callNode, ctx) {
  const callee = callNode.expression;
  // P2：checker 优先 —— 跨文件符号解析（含 import alias 解开）
  if (ctx.checker) {
    const { resolveSymbol } = ctx.resolveSymbol ?? {};
    if (resolveSymbol) {
      const r = resolveSymbol(ctx.checker, ts.isIdentifier(callee) ? callee : callee.name, ctx.root);
      const decl = r?.declaration;
      if (decl && (ts.isFunctionDeclaration(decl) || ts.isMethodDeclaration(decl) ||
                   ts.isArrowFunction(decl) || ts.isFunctionExpression(decl))) {
        return fnDefOf(decl);
      }
    }
  }
  const rec = ctx.fileIndex?.get(ctx.file);
  if (!rec) return null;
  // this.method(...)
  if (ts.isPropertyAccessExpression(callee) && callee.expression.kind === ts.SyntaxKind.ThisKeyword) {
    const cls = rec.classes?.get(ctx.cls);
    const m = cls?.methods?.get(callee.name.text);
    return m ? fnDefOf(m) : null;
  }
  if (ts.isIdentifier(callee)) {
    const name = callee.text;
    if (rec.functions?.has(name)) return fnDefOf(rec.functions.get(name));
    if (rec.constFns?.has(name)) return fnDefOf(rec.constFns.get(name));
    const imp = rec.imports?.get(name);
    if (imp && ctx.resolveImport) {
      const target = ctx.resolveImport(ctx.root, rec.file, imp.spec);
      const t = ctx.fileIndex?.get(target);
      if (t) {
        if (t.functions?.has(imp.imported)) return fnDefOf(t.functions.get(imp.imported));
        if (t.constFns?.has(imp.imported)) return fnDefOf(t.constFns.get(imp.imported));
      }
    }
  }
  return null;
}

function evalCall(node, env, ctx) {
  // ---- 内建方法（字符串 / 数组 / 通用）----
  if (ts.isPropertyAccessExpression(node.expression)) {
    const method = node.expression.name.text;
    const recv = evalExpr(node.expression.expression, env, ctx);
    if (!isUnknown(recv)) {
      const args = node.arguments.map(a => evalExpr(a, env, ctx));
      const builtin = evalBuiltinMethod(method, recv, args, node, env, ctx);
      if (builtin !== null) return builtin;
    }
  }
  // 用户函数
  if ((ctx.depth ?? 0) > 8) return UNKNOWN;
  const fn = findFunction(node, ctx);
  if (!fn) return UNKNOWN;
  const newEnv = Object.create(env || null);
  for (let i = 0; i < fn.params.length; i++) {
    const argV = i < node.arguments.length ? evalExpr(node.arguments[i], env, ctx) : UNKNOWN;
    if (isUnknown(argV)) return UNKNOWN;
    newEnv[fn.params[i]] = argV;
  }
  const res = fn.body.statements
    ? evalStatements(fn.body.statements, newEnv, { ...ctx, depth: (ctx.depth ?? 0) + 1 })
    : { flow: 'return', value: evalExpr(fn.body.expression, newEnv, { ...ctx, depth: (ctx.depth ?? 0) + 1 }) };
  return res.flow === 'return' ? res.value : UNKNOWN;
}

// 内建方法求值：返回抽象值；不认该方法时返回 null（交由用户函数/UNKNOWN 兜底）
function evalBuiltinMethod(method, recv, args, node, env, ctx) {
  const anyUnknown = args.some(isUnknown);
  // ---- 字符串方法 ----
  if (recv.k === 'str') {
    const s = recv.v;
    if (anyUnknown) return UNKNOWN;
    switch (method) {
      case 'startsWith': return BOOL(s.startsWith(strOf(args[0])));
      case 'endsWith': return BOOL(s.endsWith(strOf(args[0])));
      case 'includes': return BOOL(s.includes(strOf(args[0])));
      case 'indexOf': return NUM(s.indexOf(strOf(args[0])));
      case 'lastIndexOf': return NUM(s.lastIndexOf(strOf(args[0])));
      case 'toLowerCase': return STR(s.toLowerCase());
      case 'toUpperCase': return STR(s.toUpperCase());
      case 'trim': return STR(s.trim());
      case 'slice': {
        if (args.length === 0 || args[0].k !== 'num') return UNKNOWN;
        const start = args[0].v;
        const end = args.length >= 2 && args[1].k === 'num' ? args[1].v : undefined;
        return STR(end === undefined ? s.slice(start) : s.slice(start, end));
      }
      case 'split': {
        if (args.length === 0 || args[0].k !== 'str') return UNKNOWN;
        const sep = args[0].v;
        const parts = s.split(sep);
        return ARR(parts.map(p => STR(p)));
      }
      case 'length': return NUM(s.length);
      case 'charAt': return (args[0]?.k === 'num') ? STR(s.charAt(args[0].v)) : UNKNOWN;
      default: return null;
    }
  }
  // ---- 数组方法 ----
  if (recv.k === 'arr') {
    const arr = recv.items;
    switch (method) {
      case 'length': return NUM(arr.length);
      case 'includes': return anyUnknown ? UNKNOWN : BOOL(arr.some(x => eq(x, args[0])));
      case 'indexOf': return anyUnknown ? UNKNOWN : NUM(arr.findIndex(x => eq(x, args[0])));
      case 'join': {
        const sep = args.length === 0 || args[0].k === 'str' ? (args[0]?.v ?? ',') : null;
        if (sep === null) return UNKNOWN;
        if (arr.some(x => x.k !== 'str')) return UNKNOWN;
        return STR(arr.map(x => x.v).join(sep));
      }
      case 'some':
      case 'every':
      case 'find':
      case 'findIndex':
      case 'filter':
      case 'map':
        return evalArrayCallback(method, arr, node, env, ctx);
      case 'push': {
        // 副作用：arr.push(...) 会改 recv，但 recv 是值拷贝，语义上等价返回新长度
        for (const a of args) { if (isUnknown(a)) return UNKNOWN; arr.push(a); }
        return NUM(arr.length);
      }
      case 'concat': {
        if (anyUnknown) return UNKNOWN;
        const out = [...arr];
        for (const a of args) { if (a.k === 'arr') out.push(...a.items); else out.push(a); }
        return ARR(out);
      }
      case 'slice': {
        if (args.length === 0 || args[0].k !== 'num') return UNKNOWN;
        const start = args[0].v;
        const end = args.length >= 2 && args[1].k === 'num' ? args[1].v : undefined;
        return ARR(end === undefined ? arr.slice(start) : arr.slice(start, end));
      }
      default: return null;
    }
  }
  return null;
}

// 数组高阶方法（some/every/find/findIndex/filter/map）：回调是箭头函数，逐元素求值
function evalArrayCallback(method, arr, node, env, ctx) {
  const cb = node.arguments[0];
  if (!ts.isArrowFunction(cb) && !ts.isFunctionExpression(cb)) return UNKNOWN;
  const params = cb.parameters.map(p => ts.isIdentifier(p.name) ? p.name.text : null);
  if (params.some(p => p === null)) return UNKNOWN;
  const results = [];
  for (let i = 0; i < arr.length; i++) {
    const cbEnv = Object.create(env || null);
    // 回调参数：第 1 个=元素，第 2 个=下标
    if (params[0]) cbEnv[params[0]] = arr[i];
    if (params[1]) cbEnv[params[1]] = NUM(i);
    let ret;
    if (cb.body) {
      const res = ts.isBlock(cb.body)
        ? evalStatements(cb.body.statements, cbEnv, { ...ctx, depth: (ctx.depth ?? 0) + 1 })
        : { flow: 'return', value: evalExpr(cb.body, cbEnv, { ...ctx, depth: (ctx.depth ?? 0) + 1 }) };
      ret = res.flow === 'return' ? res.value : UNKNOWN;
    } else {
      ret = UNKNOWN;
    }
    if (isUnknown(ret)) return UNKNOWN;
    results.push({ i, elem: arr[i], v: ret });
  }
  switch (method) {
    case 'some': return BOOL(results.some(r => truthy(r.v)));
    case 'every': return BOOL(results.every(r => truthy(r.v)));
    // find/findIndex/filter 语义：返回"满足条件的元素"（而非回调返回值）
    case 'find': {
      const hit = results.find(r => truthy(r.v));
      return hit ? hit.elem : NULL();
    }
    case 'findIndex': return NUM(results.find(r => truthy(r.v))?.i ?? -1);
    case 'filter': return ARR(results.filter(r => truthy(r.v)).map(r => r.elem));
    // map 语义：返回"回调返回值"
    case 'map': return ARR(results.map(r => r.v));
    default: return UNKNOWN;
  }
}

function strOf(v) {
  return v?.k === 'str' ? v.v : v?.k === 'num' ? String(v.v) : '';
}

// ---- 语句解释执行 ----
export function evalStatements(stmts, env, ctx) {
  for (const s of stmts) {
    const r = evalStatement(s, env, ctx);
    if (r.flow !== 'fallthrough') return r;
  }
  return { flow: 'fallthrough' };
}

function evalStatement(node, env, ctx) {
  if (ts.isBlock(node)) return evalStatements(node.statements, env, ctx);
  if (ts.isVariableStatement(node)) {
    for (const d of node.declarationList.declarations) {
      if (!ts.isIdentifier(d.name)) continue;
      const v = d.initializer ? evalExpr(d.initializer, env, ctx) : UNKNOWN;
      if (isUnknown(v)) return { flow: 'fallthrough', unknown: true };
      env[d.name.text] = v;
    }
    return { flow: 'fallthrough' };
  }
  if (ts.isIfStatement(node)) {
    const c = evalExpr(node.expression, env, ctx);
    if (isUnknown(c)) return { flow: 'fallthrough', unknown: true };
    const branch = truthy(c) ? node.thenStatement : node.elseStatement;
    if (!branch) return { flow: 'fallthrough' };   // 无 else 且条件为假
    return evalStatement(branch, env, ctx);
  }
  if (ts.isReturnStatement(node)) {
    const v = node.expression ? evalExpr(node.expression, env, ctx) : NULL();
    return { flow: 'return', value: v };
  }
  if (ts.isExpressionStatement(node)) {
    const v = evalExpr(node.expression, env, ctx);
    return { flow: 'fallthrough', unknown: isUnknown(v) };
  }
  if (ts.isForStatement(node)) return evalForLoop(node, env, ctx);
  if (ts.isBreakStatement(node)) return { flow: 'break' };
  if (ts.isContinueStatement(node)) return { flow: 'continue' };
  return { flow: 'fallthrough', unknown: true };
}

// 有界 for：for (let i = <num>; i < <num>; i++) { ... }，仅覆盖构造数组/计数类常见形态
function evalForLoop(node, env, ctx) {
  const init = node.initializer;
  if (!ts.isVariableDeclarationList(init) || init.declarations.length !== 1) {
    return { flow: 'fallthrough', unknown: true };
  }
  const d = init.declarations[0];
  if (!ts.isIdentifier(d.name) || !d.initializer) return { flow: 'fallthrough', unknown: true };
  const startV = evalExpr(d.initializer, env, ctx);
  if (startV.k !== 'num') return { flow: 'fallthrough', unknown: true };
  const loopVar = d.name.text;
  let i = startV.v;
  let guard = 10000;
  while (guard-- > 0) {
    env[loopVar] = NUM(i);
    if (node.condition) {
      const c = evalExpr(node.condition, env, ctx);
      if (c.k === 'bool' && !c.v) break;
      if (isUnknown(c)) return { flow: 'fallthrough', unknown: true };
    }
    const r = evalStatement(node.statement, env, ctx);
    if (r.flow === 'return') return r;
    if (r.flow === 'break') break;
    // 增量
    const inc = node.incrementor;
    if (ts.isPostfixUnaryExpression(inc) && inc.operator === ts.SyntaxKind.PlusPlusToken) i++;
    else if (ts.isPrefixUnaryExpression(inc) && inc.operator === ts.SyntaxKind.PlusPlusToken) i++;
    else if (ts.isBinaryExpression(inc) && inc.operatorToken.kind === ts.SyntaxKind.PlusEqualsToken) {
      const step = evalExpr(inc.right, env, ctx);
      if (step.k !== 'num') return { flow: 'fallthrough', unknown: true };
      i += step.v;
    } else return { flow: 'fallthrough', unknown: true };
  }
  return { flow: 'fallthrough' };
}

// ---- 数据源解析：求值为数组 ----
export function resolveArray(node, env, ctx) {
  const v = evalExpr(node, env, ctx);
  return v.k === 'arr' ? v.items : null;
}

function valueToText(v) {
  switch (v?.k) {
    case 'str': return v.v;
    case 'num': return String(v.v);
    case 'bool': return String(v.v);
    default: return null;
  }
}

/**
 * 条件路由枚举：给定 if 条件表达式 + ForEach 数据源 + 列表项文案模板，枚举出
 * "点哪个列表项能命中该路由分支"的文案列表。
 *
 * 参数：
 *  - conditionNode：if 的条件表达式 AST（含函数调用，会内联求值）
 *  - sourceNode：ForEach 数据源表达式 AST（数组字面量 / this.xxx / 函数调用）
 *  - paramNames：ForEach 回调参数名，如 ['type','idx']（第0个=数据项，第1个=下标）
 *  - inElse：当前 push 调用是否在 else 分支
 *  - viaTemplate / viaVars：列表项文案模板 `` `路由项${type}${idx}` `` 的前缀与变量序列
 *
 * 返回 string[]（可点击的列表项文案）；求不出返回 null（调用方回退手写场景）。
 */
export function evaluateRouteItems({ conditionNode, sourceNode, paramNames, inElse, viaTemplate, viaVars, viaTernary }, ctx) {
  if (!conditionNode || !sourceNode || !paramNames?.length) return null;
  const items = resolveArray(sourceNode, null, ctx);
  if (!items) return null;
  const out = [];
  for (let di = 0; di < items.length; di++) {
    const item = items[di];
    const env = Object.create(null);
    paramNames.forEach((pn, pi) => {
      if (pi === 0) env[pn] = item;
      else if (pi === 1) env[pn] = NUM(di);
    });
    const condV = evalExpr(conditionNode, env, ctx);
    if (isUnknown(condV)) return null;
    const hit = inElse ? !truthy(condV) : truthy(condV);
    if (!hit) continue;
    // 三元条件文案：Text(cond ? 'A' : 'B')，按条件真假选文案
    if (viaTernary) {
      const condExpr = parseTernaryCondition(viaTernary.condition);
      const tcv = condExpr ? evalExpr(condExpr, env, ctx) : UNKNOWN;
      if (isUnknown(tcv)) return null;
      out.push(truthy(tcv) ? viaTernary.whenTrue : viaTernary.whenFalse);
      continue;
    }
    // 生成列表项文案：viaTemplate + 各 viaVar 表达式在 env 下求值
    let text = viaTemplate;
    if (viaVars && viaVars.length) {
      for (const v of viaVars) {
        // viaVar 是标识符（type/idx）或成员访问（item.kind），构造对应 AST 求值
        const exprNode = parseVarExpr(v);
        if (!exprNode) return null;
        const vv = evalExpr(exprNode, env, ctx);
        if (isUnknown(vv)) return null;
        const t = valueToText(vv);
        if (t == null) return null;
        text += t;
      }
    }
    out.push(text);
  }
  return out.length ? out : null;
}

// 解析三元条件表达式文本（如 "type==='A'"）为可求值的 AST
function parseTernaryCondition(text) {
  const sf = ts.createSourceFile('cond.ts', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const stmt = sf.statements[0];
  if (ts.isExpressionStatement(stmt)) return stmt.expression;
  return null;
}

// 把 viaVar 表达式文本（'type' / 'idx' / 'item.kind'）解析成 AST 节点
function parseVarExpr(text) {
  if (/^[A-Za-z_]\w*$/.test(text)) return ts.factory.createIdentifier(text);
  if (/^[A-Za-z_]\w*\.[A-Za-z_]\w*$/.test(text)) {
    const [a, b] = text.split('.');
    return ts.factory.createPropertyAccessExpression(ts.factory.createIdentifier(a), b);
  }
  return null;
}
