"""谓词求值器单测：多语句函数 / 函数返回数组 / 跨文件 import / 对象数组。

直接驱动 tools/predicate_eval.mjs 的求值能力，验证条件路由枚举的各种形态。
"""
import json
import os
import subprocess

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
TESTAPP = os.path.join(os.path.dirname(__file__), "..", "TestApp")


def run_node(code: str) -> str:
    """在 tools 目录下跑一段 node 代码，返回 stdout。"""
    proc = subprocess.run(["node", "--input-type=module", "-e", code],
                          capture_output=True, timeout=60,
                          cwd=os.path.join(os.path.dirname(__file__), ".."))
    return proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")


def _eval_scenario(code):
    """通用：加载 TestApp 两个文件，对指定场景跑 evaluateRouteItems，返回 viaItems。"""
    return run_node(f"""
import fs from 'fs';
import {{ loadTypescript }} from './tools/ts_loader.mjs';
const ts = loadTypescript().ts;
import {{ preprocess, analyzeFile }} from './tools/ast_scan.mjs';
import {{ evaluateRouteItems }} from './tools/predicate_eval.mjs';
const root = 'TestApp';
const doPreprocess = !ts.isStructDeclaration;   // fork 原生 struct 无需预处理
function load(f) {{
  const raw = fs.readFileSync(f,'utf-8');
  const sf = ts.createSourceFile(f, doPreprocess ? preprocess(raw) : raw, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  return {{ sf, rec: analyzeFile(root, f, sf) }};
}}
const entry = load('TestApp/entry/src/main/ets/pages/NavEntryPage.ets');
const util = load('TestApp/entry/src/main/ets/common/RouteUtil.ets');
const fileIndex = new Map([[entry.rec.file, entry.rec], [util.rec.file, util.rec]]);
import path from 'path';
function rel(r, p) {{ return path.relative(r, p).split(path.sep).join('/'); }}
function resolveImport(root, fromFile, spec) {{
  if (!spec.startsWith('.')) return null;
  const base = path.resolve(path.dirname(path.join(root, fromFile)), spec);
  for (const cand of [base, base+'.ets', base+'.ts', path.join(base,'index.ets')]) {{
    const relp = rel(root, cand);
    if (fs.existsSync(cand) && !relp.startsWith('..')) return relp;
  }}
  return null;
}}
const ctx = {{ file: entry.rec.file, cls: 'NavEntryPage', root, fileIndex, resolveImport }};
{code}
""")


@pytest.mark.skipif(not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "node_modules")),
                    reason="需要 npm install")
def test_multi_statement_function_condition():
    """多语句函数（isPremiumItem）+ 对象数组（getRouteItems）+ 跨文件 import。"""
    out = _eval_scenario("""
// 找场景3 的 ForEach（getRouteItems），并在其回调内找 NavRouteAPage 的 pushPathByName
let fe = null, target = null;
(function walk(n) {
  if (!fe && ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'ForEach'
      && n.arguments[0].getText(entry.sf) === 'getRouteItems()') fe = n;
  if (fe && !target && ts.isCallExpression(n) && n.expression.getText(entry.sf).includes('pushPathByName')
      && n.getText(entry.sf).includes('NavRouteAPage')) {
    // 只在场景3 的 ForEach 回调内找（target 是 fe 的后代）
    let isDesc = false, p = n.parent;
    while (p) { if (p === fe) { isDesc = true; break; } p = p.parent; }
    if (isDesc) target = n;
  }
  ts.forEachChild(n, walk);
})(entry.sf);
const cb = fe.arguments[1];
const paramNames = cb.parameters.map(p => p.name.text);
// 找 target 的 if 祖先
let inElse = false;
let p = target.parent; let ifNode;
while (p) { if (ts.isIfStatement(p)) { ifNode = p; inElse = false; break; } if (ts.isArrowFunction(p)) break; p = p.parent; }
const items = evaluateRouteItems({
  conditionNode: ifNode.expression, sourceNode: fe.arguments[0], paramNames,
  inElse: false, viaTemplate: '高级项', viaVars: ['item.kind','idx'],
}, ctx);
console.log('RESULT ' + JSON.stringify(items));
""")
    assert "高级项premium0" in out and "高级项premium1" in out


@pytest.mark.skipif(not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "node_modules")),
                    reason="需要 npm install")
def test_function_returning_array():
    """数据源是函数返回数组（getRouteItems，含 for 循环 + push）。"""
    out = run_node("""
import fs from 'fs';
import { loadTypescript } from './tools/ts_loader.mjs';
const ts = loadTypescript().ts;
import { preprocess, analyzeFile } from './tools/ast_scan.mjs';
import { evalExpr } from './tools/predicate_eval.mjs';
const root = 'TestApp';
function load(f) {
  const raw = fs.readFileSync(f,'utf-8');
  const sf = ts.createSourceFile(f, ts.isStructDeclaration ? raw : preprocess(raw), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  return { sf, rec: analyzeFile(root, f, sf) };
}
const entry = load('TestApp/entry/src/main/ets/pages/NavEntryPage.ets');
const util = load('TestApp/entry/src/main/ets/common/RouteUtil.ets');
const fileIndex = new Map([[entry.rec.file, entry.rec], [util.rec.file, util.rec]]);
import path from 'path';
function rel(r,p){return path.relative(r,p).split(path.sep).join('/');}
function resolveImport(root,fromFile,spec){if(!spec.startsWith('.'))return null;const base=path.resolve(path.dirname(path.join(root,fromFile)),spec);for(const c of [base,base+'.ets',base+'.ts',path.join(base,'index.ets')]){const rp=rel(root,c);if(fs.existsSync(c)&&!rp.startsWith('..'))return rp;}return null;}
const ctx = { file: entry.rec.file, cls: 'NavEntryPage', root, fileIndex, resolveImport };
let call;
(function walk(n){ if(call)return; if(ts.isCallExpression(n)&&n.getText(entry.sf)==='getRouteItems()')call=n; ts.forEachChild(n,walk);})(entry.sf);
const v = evalExpr(call, null, ctx);
console.log('RESULT ' + v.k + ':' + v.items.length);
""")
    assert "RESULT arr:4" in out


@pytest.mark.skipif(not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "node_modules")),
                    reason="需要 npm install")
def test_string_array_still_works():
    """回归：字符串数组 + 等值条件（场景2，this.routes）仍正确。"""
    out = run_node("""
import fs from 'fs';
import { loadTypescript } from './tools/ts_loader.mjs';
const ts = loadTypescript().ts;
import { preprocess, analyzeFile } from './tools/ast_scan.mjs';
import { evaluateRouteItems } from './tools/predicate_eval.mjs';
const root = 'TestApp';
function load(f) {
  const raw = fs.readFileSync(f,'utf-8');
  const sf = ts.createSourceFile(f, ts.isStructDeclaration ? raw : preprocess(raw), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  return { sf, rec: analyzeFile(root, f, sf) };
}
const entry = load('TestApp/entry/src/main/ets/pages/NavEntryPage.ets');
const fileIndex = new Map([[entry.rec.file, entry.rec]]);
import path from 'path';
function rel(r,p){return path.relative(r,p).split(path.sep).join('/');}
function resolveImport(root,fromFile,spec){if(!spec.startsWith('.'))return null;const base=path.resolve(path.dirname(path.join(root,fromFile)),spec);for(const c of [base,base+'.ets',base+'.ts',path.join(base,'index.ets')]){const rp=rel(root,c);if(fs.existsSync(c)&&!rp.startsWith('..'))return rp;}return null;}
const ctx = { file: entry.rec.file, cls: 'NavEntryPage', root, fileIndex, resolveImport };
let fe, targetB;
(function walk(n) {
  if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'ForEach'
      && n.arguments[0].getText(entry.sf) === 'this.routes') fe = n;
  if (!targetB && ts.isCallExpression(n) && n.expression.getText(entry.sf).includes('pushPathByName')
      && n.getText(entry.sf).includes('NavRouteBPage')) targetB = n;
  ts.forEachChild(n, walk);
})(entry.sf);
const cb = fe.arguments[1];
const paramNames = cb.parameters.map(p => p.name.text);
let p = targetB.parent; let ifNode; let inElse=false;
while (p) { if (ts.isIfStatement(p)) { ifNode = p; inElse = true; break; } if (ts.isArrowFunction(p)) break; p = p.parent; }
const items = evaluateRouteItems({
  conditionNode: ifNode.expression, sourceNode: fe.arguments[0], paramNames,
  inElse, viaTemplate: '路由项', viaVars: ['type','idx'],
}, ctx);
console.log('RESULT ' + JSON.stringify(items));
""")
    assert "路由项B1" in out and "路由项B3" in out


@pytest.mark.skipif(not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "node_modules")),
                    reason="需要 npm install")
def test_builtin_methods_string_and_array():
    """求值器内建方法：字符串 startsWith/includes/indexOf + 数组 some/includes + 算术。"""
    out = run_node("""
import { loadTypescript } from './tools/ts_loader.mjs';
const ts = loadTypescript().ts;
import { evalExpr } from './tools/predicate_eval.mjs';

// 抽象值用 evalExpr 求值：构造表达式，env 里注入字符串/数组
function ev(code, env = {}) {
  const sf = ts.createSourceFile('x.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const stmt = sf.statements[0];
  const expr = ts.isExpressionStatement(stmt) ? stmt.expression : stmt;
  const v = evalExpr(expr, env, { depth: 0 });
  return v ? (v.v !== undefined ? v.k + ':' + v.v : v.k) : 'null';
}

const env = { s: { k: 'str', v: 'premium' }, arr: { k: 'arr', items: [{ k: 'str', v: 'premium' }, { k: 'str', v: 'basic' }] } };
console.log('startsWith', ev("s.startsWith('pre')", env));   // bool:true
console.log('includes', ev("s.includes('rem')", env));        // bool:true
console.log('indexOf', ev("s.indexOf('ium')", env));          // num:4
console.log('modulo', ev("7 % 2", {}));                        // num:1
console.log('multiply', ev("3 * 4", {}));                      // num:12
console.log('arr.includes', ev("arr.includes('basic')", env)); // bool:true
console.log('arr.length', ev("arr.length", env));              // num:2
""")
    assert "startsWith bool:true" in out
    assert "includes bool:true" in out
    assert "indexOf num:4" in out
    assert "modulo num:1" in out
    assert "multiply num:12" in out
    assert "arr.includes bool:true" in out
    assert "arr.length num:2" in out


@pytest.mark.skipif(not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "node_modules")),
                    reason="需要 npm install")
def test_array_callback_methods():
    """数组高阶方法：some / find / filter / map 带箭头回调。"""
    out = run_node("""
import { loadTypescript } from './tools/ts_loader.mjs';
const ts = loadTypescript().ts;
import { evalExpr } from './tools/predicate_eval.mjs';
function ev(code, env = {}) {
  const sf = ts.createSourceFile('x.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const stmt = sf.statements[0];
  const expr = ts.isExpressionStatement(stmt) ? stmt.expression : stmt;
  const v = evalExpr(expr, env, { depth: 0 });
  if (!v) return 'null';
  if (v.k === 'arr') return 'arr:' + v.items.map(i => i.v).join(',');
  return v.k + ':' + v.v;
}
const env = { items: { k: 'arr', items: [{ k: 'str', v: 'premium' }, { k: 'str', v: 'basic' }, { k: 'str', v: 'premium' }] } };
console.log('some', ev("items.some((x) => x === 'premium')", env));       // bool:true
console.log('find', ev("items.find((x) => x === 'basic')", env));          // str:basic
console.log('filter', ev("items.filter((x) => x === 'premium')", env));    // arr:premium,premium
console.log('map_len', ev("items.map((x) => x.length)", env));             // arr:7,5,7
""")
    assert "some bool:true" in out
    assert "find str:basic" in out
    assert "filter arr:premium,premium" in out
    assert "map_len arr:7,5,7" in out
