#!/usr/bin/env python3
"""Xiaoniu TTS server: tiny HTTP wrapper around edge-tts for Operit's HTTP TTS.

GET /            -> health text
GET /tts?text=<urlencoded>&voice=<name>&k=<token>  -> audio/mpeg (mp3)

Env:
  PORT / SERVER_PORT  listen port (default 8080)
  TTS_TOKEN           if set, /tts requires &k=<token>
  TTS_VOICE           default voice (default zh-TW-HsiaoChenNeural)
"""
import asyncio
import os

import edge_tts
from aiohttp import web

DEFAULT_VOICE = os.environ.get("TTS_VOICE", "zh-TW-HsiaoChenNeural")
TOKEN = os.environ.get("TTS_TOKEN", "")
MAX_CHARS = 300
_sem = asyncio.Semaphore(2)


async def health(_request):
    return web.Response(text="xiaoniu-tts ok\n")


async def tts(request):
    if TOKEN and request.query.get("k") != TOKEN:
        return web.Response(status=403, text="forbidden")
    text = (request.query.get("text") or "").strip()
    if not text:
        return web.Response(status=400, text="missing text")
    text = text[:MAX_CHARS]
    voice = request.query.get("voice") or DEFAULT_VOICE
    rate = request.query.get("rate") or "+0%"
    pitch = request.query.get("pitch") or "+0Hz"
    try:
        async with _sem:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            audio = bytearray()
            async with asyncio.timeout(30):
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio.extend(chunk["data"])
    except Exception as exc:  # upstream failure -> 502 so the app can fall back
        return web.Response(status=502, text=f"tts upstream error: {exc}")
    if not audio:
        return web.Response(status=502, text="empty audio")
    return web.Response(body=bytes(audio), content_type="audio/mpeg")


def main():
    port = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "8080")
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/tts", tts)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
