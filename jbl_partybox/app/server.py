"""Web UI + API for the PartyBox app, served through Home Assistant ingress."""
import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

import diagnostics
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
    meta = {**META, "mqtt": meta_mqtt(request.app)}
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
        connected = await mgr.connect_to(body["address"], pair=bool(body.get("pair")))
        return web.json_response({"ok": True, "superseded": not connected})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def post_disconnect(request):
    await request.app["mgr"].disconnect()
    return web.json_response({"ok": True})


async def post_adapter(request):
    body = await request.json()
    try:
        await request.app["mgr"].set_adapter(body.get("adapter") or None)
        return web.json_response({"ok": True})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def post_adapter_power(request):
    body = await request.json()
    try:
        await request.app["mgr"].power_adapter(body["adapter"])
        return web.json_response({"ok": True})
    except (KeyError, ValueError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def get_diagnostics(request):
    report = diagnostics.report(request.app["mgr"], meta_mqtt(request.app))
    return web.json_response(report, dumps=lambda d: json.dumps(d, indent=2, ensure_ascii=False), headers={
        "Content-Disposition": 'attachment; filename="partybox-diagnostics.json"',
    })


def meta_mqtt(app):
    bridge = app.get("bridge")
    return bridge.status if bridge else "disabled"


async def post_autoconnect(request):
    body = await request.json()
    try:
        await request.app["mgr"].set_autoconnect(body["value"])
        return web.json_response({"ok": True})
    except (KeyError, ValueError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def post_autoconnect_resume(request):
    await request.app["mgr"].resume_auto()
    return web.json_response({"ok": True})


async def post_forget(request):
    body = await request.json()
    try:
        await request.app["mgr"].forget(body["address"])
        return web.json_response({"ok": True})
    except (KeyError, ValueError) as e:
        return web.json_response({"error": str(e)}, status=400)


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
    await mgr.refresh_health()
    app["health_task"] = asyncio.create_task(mgr.health_loop())
    await mgr.refresh_paired()
    target = mgr.autoconnect_target()
    if target:
        log.info("auto-connecting to %s", target)
        mgr.auto_reconnect(target)


async def on_cleanup(app):
    app["health_task"].cancel()
    await app["mgr"].disconnect()


def main():
    opts = load_options()
    mgr = SpeakerManager(
        heartbeat_interval=opts.get("heartbeat_interval", 5),
        scan_timeout=opts.get("scan_timeout", 8),
        auto_connect=opts.get("auto_connect", True),  # only the default until set in the panel
    )
    app = web.Application(middlewares=[ingress_only])
    app["mgr"] = mgr
    app["opts"] = opts
    app.router.add_get("/", index)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/scan", post_scan)
    app.router.add_post("/api/connect", post_connect)
    app.router.add_post("/api/disconnect", post_disconnect)
    app.router.add_post("/api/adapter", post_adapter)
    app.router.add_post("/api/adapter/power", post_adapter_power)
    app.router.add_get("/api/diagnostics", get_diagnostics)
    app.router.add_post("/api/autoconnect", post_autoconnect)
    app.router.add_post("/api/autoconnect/resume", post_autoconnect_resume)
    app.router.add_post("/api/forget", post_forget)
    app.router.add_post("/api/action", post_action)
    app.router.add_get("/api/ws", websocket)
    app.router.add_static("/static", STATIC)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
