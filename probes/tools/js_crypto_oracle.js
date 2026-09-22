#!/usr/bin/env node
/**
 * js_crypto_oracle.js —— 把**站点自己的 JS** 当加解密预言机（通用件）。
 *
 * 为什么需要它：前端「自定义加密」几乎都不是真的自定义 —— 绝大多数是 CryptoJS /
 * JSEncrypt / sm-crypto 的薄封装，密钥就硬编码在里面。与其照着混淆代码猜参数，
 * 不如**把它的 JS 原样加载进一个 vm 沙箱，直接调它的函数** ——
 * 这样得到的不是「我认为它在干什么」，而是「它确实在干什么」。
 *
 * 用法：
 *   # 看这份 JS 往全局挂了哪些可调用的东西
 *   node js_crypto_oracle.js --files aes.js,conscript.js --list
 *
 *   # 调它的 decrypt，把 base64 密文解回来
 *   node js_crypto_oracle.js --files aes.js,conscript.js --call decrypt '<base64>'
 *
 *   # 从文件读参数（密文太长时用；文件里若有换行会被自动去掉）
 *   node js_crypto_oracle.js --files aes.js,conscript.js --call decrypt --arg-file body.txt
 *
 *   # 多参数
 *   node js_crypto_oracle.js --files a.js --call encrypt foo bar
 *
 *   # 顺手把返回值当 JSON 打印（若可解析）
 *   … --json
 *
 * 🔴 两个坑：
 *  1. **顺序**：先加载基础库（aes.js / crypto-js.min.js），再加载业务封装
 *     （conscript.js 之类），否则后者的 `CryptoJS` 引用会 undefined。
 *  2. **UMD 判定**：这些文件常带 `"object"==typeof exports ? module.exports=… `。
 *     在 vm 沙箱里**不要**注入 `module`/`exports`，否则它会走 CommonJS 分支，
 *     全局上就什么都不挂 —— 你会以为"函数没导出"，其实是自己把路堵了。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function parseArgv(argv) {
  const o = { files: [], call: null, args: [], list: false, json: false, argFile: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--files') o.files = argv[++i].split(',').map(s => s.trim()).filter(Boolean);
    else if (a === '--call') o.call = argv[++i];
    else if (a === '--arg-file') o.argFile = argv[++i];
    else if (a === '--list') o.list = true;
    else if (a === '--json') o.json = true;
    else o.args.push(a);
  }
  return o;
}

const opt = parseArgv(process.argv.slice(2));
if (!opt.files.length) {
  console.error('用法：node js_crypto_oracle.js --files a.js,b.js [--list | --call 函数名 参数…]');
  process.exit(2);
}

// —— 沙箱：给「像浏览器」的最小环境；**故意不给 module/exports**（见文件头注释 2）
const sandbox = {
  console,
  navigator: { userAgent: 'Mozilla/5.0', platform: 'Linux' },
  location: { href: 'https://example.invalid/', protocol: 'https:', host: 'example.invalid' },
  document: {
    createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
    getElementsByTagName: () => [],
    addEventListener() {}, cookie: '',
  },
  setTimeout, clearTimeout, setInterval, clearInterval,
  atob: (s) => Buffer.from(s, 'base64').toString('binary'),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.global = sandbox;
const ctx = vm.createContext(sandbox);
ctx.globalThis = ctx;

for (const f of opt.files) {
  const p = path.resolve(f);
  if (!fs.existsSync(p)) { console.error('找不到文件：' + p); process.exit(2); }
  try {
    vm.runInContext(fs.readFileSync(p, 'utf8'), ctx, { filename: path.basename(f) });
    console.error('[loaded] ' + path.basename(f) + ' (' + fs.statSync(p).size + ' 字节)');
  } catch (e) {
    console.error('[!] ' + path.basename(f) + ' 执行报错：' + e.message);
  }
}

const isFn = (v) => typeof v === 'function';
const names = Object.keys(ctx).filter(k => !/^(console|globalThis|global|window|self|navigator|location|document|setTimeout|clearTimeout|setInterval|clearInterval|atob|btoa)$/.test(k));

if (opt.list || !opt.call) {
  const fns = names.filter(k => isFn(ctx[k]));
  const objs = names.filter(k => ctx[k] && typeof ctx[k] === 'object');
  console.log('可调用函数（' + fns.length + '）：');
  for (const k of fns) console.log('  ' + k + '  (arity ' + (ctx[k].length || 0) + ')');
  console.log('全局对象（' + objs.length + '）：' + objs.join(', '));
  if (objs.includes('CryptoJS')) {
    const C = ctx.CryptoJS;
    const modes = C.mode ? Object.keys(C.mode) : [];
    const pads = C.pad ? Object.keys(C.pad) : [];
    const enc = C.enc ? Object.keys(C.enc) : [];
    console.log('  ├ CryptoJS.mode: ' + modes.join(', '));
    console.log('  ├ CryptoJS.pad : ' + pads.join(', '));
    console.log('  └ CryptoJS.enc : ' + enc.join(', '));
    console.log('  ⇒ 看 mode/pad 列表就能一眼看出它用了什么模式（含 ECB 就基本没链接模式）');
  }
  if (!opt.call) process.exit(0);
}

let callArgs = opt.args.slice();
if (opt.argFile) {
  callArgs = [fs.readFileSync(path.resolve(opt.argFile), 'utf8').replace(/\s+/g, '')].concat(callArgs);
}

if (!isFn(ctx[opt.call])) {
  console.error('沙箱里没有可调用的 ' + opt.call + '；用 --list 看看有什么。');
  process.exit(2);
}
try {
  const out = ctx[opt.call].apply(null, callArgs);
  if (opt.json) {
    try { console.log(JSON.stringify(JSON.parse(out), null, 1)); }
    catch (_) { console.log(String(out)); }
  } else {
    console.log(typeof out === 'string' ? out : JSON.stringify(out));
  }
} catch (e) {
  console.error('调用 ' + opt.call + ' 失败：' + e.message);
  process.exit(3);
}
