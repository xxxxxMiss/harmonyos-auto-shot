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
// 找场景3 的 ForEach（getRouteItems）与 if（isPremiumItem）
let fe, cond, inElse = false, target;
(function walk(n) {
  if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'ForEach'
      && n.arguments[0].getText(entry.sf) === 'getRouteItems()') fe = n;
  if (ts.isCallExpression(n) && n.expression.getText(entry.sf).includes('pushPathByName') && n.getText(entry.sf).includes('NavRouteAPage')) target = n;
  ts.forEachChild(n, walk);
})(entry.sf);
const cb = fe.arguments[1];
const paramNames = cb.parameters.map(p => p.name.text);
// 找 target 的 if 祖先
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
