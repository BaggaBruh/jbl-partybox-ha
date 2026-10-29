"""Web UI + API for the PartyBox app, served through Home Assistant ingress."""
import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

import protocol as p
from mqtt_bridge import MqttBridge
from speaker import SpeakerManager

OPTIONS_FILE = Path("/data/options.json")
STATIC = Path(__file__).parent / "static"
PORT = 8099
ALLOWED_CLIENTS = {"172.30.32.2", "127.0.0.1", "::1"}  # HA ingress gateway, local development

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")


def load_options():
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except (OSError, ValueError):
        return {}


@web.middleware
async def ingress_only(request, handler):
    # With host_network the server is reachable on the host's LAN address too. Only accept the
    # Home Assistant ingress gateway (which handles login) and local connections.
    if request.remote not in ALLOWED_CLIENTS:
        raise web.HTTPForbidden(text="Open the PartyBox panel from the Home Assistant sidebar.")
    return await handler(request)


async def index(_request):
    return web.FileResponse(STATIC / "index.html")


async def get_state(request):
    mgr = request.app["mgr"]
    bridge = request.app.get("bridge")
    meta = {**META, "mqtt": bridge.status if bridge else "disabled"}
    return web.json_response({**mgr.snapshot(), "log": list(mgr.log)[-100:], "meta": meta})


async def post_scan(request):
    mgr = request.app["mgr"]
    try:
        return web.json_response({"devices": await mgr.scan()})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def post_connect(request):
    mgr = request.app["mgr"]
    body = await request.json()
    try:
        await mgr.connect(body["address"])
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def post_disconnect(request):
    await request.app["mgr"].disconnect()
    return web.json_response({"ok": True})


async def post_action(request):
    mgr = request.app["mgr"]
    body = await request.json()
    try:
        await mgr.action(body["action"], body.get("value"))
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def websocket(request):
    mgr = request.app["mgr"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    async def push(event):
        await ws.send_str(json.dumps(event))

    mgr.listeners.add(push)
    try:
        await push({"type": "state", **mgr.snapshot()})
        async for msg in ws:
            if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        mgr.listeners.discard(push)
    return ws


META = {
    "volume_max": p.VOLUME_MAX,
    "light_patterns": p.LIGHT_PATTERNS,
    "bass_boost_levels": p.BASS_BOOST_LEVELS,
}


async def on_startup(app):
    mgr = app["mgr"]
    if app["opts"].get("mqtt_discovery", True):
        app["bridge"] = MqttBridge(mgr, app["opts"])
        app["bridge_task"] = asyncio.create_task(app["bridge"].run())
    last = SpeakerManager.load_last()
    if mgr.auto_connect and last:
        log.info("auto-connecting to %s", last)
        mgr.auto_reconnect(last)


async def on_cleanup(app):
    await app["mgr"].disconnect()


def main():
    opts = load_options()
    mgr = SpeakerManager(
        heartbeat_interval=opts.get("heartbeat_interval", 5),
        scan_timeout=opts.get("scan_timeout", 8),
        auto_connect=opts.get("auto_connect", True),
    )
    app = web.Application(middlewares=[ingress_only])
    app["mgr"] = mgr
    app["opts"] = opts
    app.router.add_get("/", index)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/scan", post_scan)
    app.router.add_post("/api/connect", post_connect)
    app.router.add_post("/api/disconnect", post_disconnect)
    app.router.add_post("/api/action", post_action)
    app.router.add_get("/api/ws", websocket)
    app.router.add_static("/static", STATIC)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
