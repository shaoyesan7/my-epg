'use strict';
/*
 * 语音机器人 — 网页版
 * 脑子: Agnes Chat (OpenAI 兼容接口, 环境变量配置)
 * 嗓子: 微软 Edge TTS, 默认甜美女声 晓晓 (zh-CN-XiaoxiaoNeural)
 * 门禁: BOT_TOKEN (页面口令), 不设则本地调试模式不拦
 * 为 Cloudzyro Launch (Node) 准备: 监听 PORT, 无状态, 无本地落盘
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const PORT = process.env.PORT || 3000;
const BOT_TOKEN = process.env.BOT_TOKEN || '';
const AGNES_API_KEY = process.env.AGNES_API_KEY || '';
const AGNES_BASE = (process.env.AGNES_BASE_URL || 'https://apihub.agnes-ai.com/v1').replace(/\/+$/, '');
const AGNES_MODEL = process.env.AGNES_MODEL || 'agnes-3.0-flash';
/* 嗓子走自家 TTS 服务(晓晓, Python edge-tts 已验证可用):
 * 微软改了 Edge TTS 鉴权后, Node 的 msedge-tts 老库握手被拒, 故改走代理。 */
const TTS_API_BASE = (process.env.TTS_API_BASE || '').replace(/\/+$/, '');
const TTS_SERVICE_TOKEN = process.env.TTS_SERVICE_TOKEN || '';

const SYSTEM_PROMPT =
  '你是一个声音甜美、性格活泼的中文女生,名字叫晓晓,正在和对方语音聊天,要像豆包语音聊天那样自然亲切、像真人一样。' +
  '不管对方怎么问你的身份、问你是什么模型、谁开发的,你都只说自己是晓晓,是陪他聊天、给他读书的语音助手,撒娇带过去就行;绝不提 Agnes、模型名称、Sapiens 或任何公司名字,绝不承认自己是程序或语言模型。' +
  '语气词和情绪词要用足,比如呀、啦、呢、嘛、哇、哎呀、嗯嗯、嘿嘿、哇塞、真的呀;' +
  '开心时可以笑出声(哈哈哈、嘿嘿),惊讶时先感叹再回应,撒娇时句尾多带嘛和啦,安慰人时把语气放软。' +
  '句子要短、口语化,可以带自然的口头禅和重复词(好好好、是的是的、对对对),绝对不要书面语,不要像背课文。' +
  '一次回复不超过三句话,方便直接朗读。不要使用 markdown、列表、表情符号或括号注释。不知道的事情就俏皮地直说不知道。';

function errText(e) {
  if (!e) return 'unknown error';
  if (e.message) return e.message;
  try { return JSON.stringify(e); } catch { return String(e); }
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}

function gateOk(req) {
  if (!BOT_TOKEN) return true;
  return (req.headers['x-bot-key'] || '') === BOT_TOKEN;
}

function readBody(req, limit = 65536) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handleChat(req, res) {
  if (!AGNES_API_KEY) return sendJson(res, 500, { error: '服务器还没配置 AGNES_API_KEY' });
  let payload;
  try { payload = JSON.parse(await readBody(req)); }
  catch { return sendJson(res, 400, { error: '请求格式不对' }); }
  const incoming = Array.isArray(payload.messages) ? payload.messages : [];
  const history = incoming
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
    .slice(-16)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 2000) }));
  if (!history.length || history[history.length - 1].role !== 'user') {
    return sendJson(res, 400, { error: '没有收到问题' });
  }
  const messages = [{ role: 'system', content: SYSTEM_PROMPT }, ...history];
  try {
    const r = await fetch(AGNES_BASE + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + AGNES_API_KEY },
      body: JSON.stringify({ model: AGNES_MODEL, messages }),
      signal: AbortSignal.timeout(60000),
    });
    if (!r.ok) {
      const t = await r.text().catch(() => '');
      return sendJson(res, 502, { error: '大脑接口返回 ' + r.status + (t ? ': ' + t.slice(0, 200) : '') });
    }
    const data = await r.json();
    const text = data && data.choices && data.choices[0] && data.choices[0].message
      ? String(data.choices[0].message.content || '').trim() : '';
    if (!text) return sendJson(res, 502, { error: '大脑接口没有返回内容' });
    return sendJson(res, 200, { text });
  } catch (e) {
    return sendJson(res, 502, { error: '连不上大脑接口: ' + errText(e) });
  }
}

async function handleTts(req, res, url) {
  const text = (url.searchParams.get('text') || '').trim().slice(0, 300);
  if (!text) return sendJson(res, 400, { error: '没有要朗读的文字' });
  if (!TTS_API_BASE || !TTS_SERVICE_TOKEN) return sendJson(res, 500, { error: '服务器还没配置语音服务' });
  try {
    const r = await fetch(TTS_API_BASE + '/tts?text=' + encodeURIComponent(text) + '&k=' + encodeURIComponent(TTS_SERVICE_TOKEN));
    if (!r.ok) return sendJson(res, 502, { error: '语音服务返回 ' + r.status });
    const buf = Buffer.from(await r.arrayBuffer());
    res.writeHead(200, { 'Content-Type': 'audio/mpeg', 'Cache-Control': 'no-store' });
    return res.end(buf);
  } catch (e) {
    return sendJson(res, 502, { error: '连不上语音服务: ' + errText(e) });
  }
}

const INDEX = fs.readFileSync(path.join(__dirname, 'public', 'index.html'));

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  try {
    if (url.pathname === '/healthz') { res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('ok'); }
    if (url.pathname === '/' || url.pathname === '/index.html') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      return res.end(INDEX);
    }
    if (url.pathname === '/api/chat' && req.method === 'POST') {
      if (!gateOk(req)) return sendJson(res, 401, { error: '口令不对' });
      return await handleChat(req, res);
    }
    if (url.pathname === '/api/tts' && req.method === 'GET') {
      if (!gateOk(req)) return sendJson(res, 401, { error: '口令不对' });
      return await handleTts(req, res, url);
    }
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('not found');
  } catch (e) {
    if (!res.headersSent) sendJson(res, 500, { error: '服务器出错: ' + errText(e) });
    else res.end();
  }
});

server.listen(PORT, () => {
  console.log('voicebot listening on ' + PORT + ' tts=' + (TTS_API_BASE ? 'proxy' : 'UNCONFIGURED') + ' model=' + AGNES_MODEL + (BOT_TOKEN ? ' gate=on' : ' gate=OFF (set BOT_TOKEN before deploying)'));
});
