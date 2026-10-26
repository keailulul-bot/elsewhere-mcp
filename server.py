import os
import math
import base64
import asyncio
import time
import datetime as dt
import logging
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.auth.provider import AccessToken
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from mcp.server.auth.settings import AuthSettings

# Windy snapshot URLs may carry short-lived image tokens in their query string.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_KEY = os.environ["WINDY_API_KEY"]
BASE = "https://api.windy.com/webcams/api/v3"
INCLUDE = "location,images,player,urls,categories"
class WorkOSTokenVerifier:
    def __init__(self, issuer: str, resource: str, allowed_users: set[str]):
        self.issuer = issuer
        self.resource = resource
        self.allowed_users = allowed_users
        self.keys = PyJWKClient(f"{issuer}/oauth2/jwks", cache_jwk_set=True, timeout=5)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await asyncio.to_thread(self.keys.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], issuer=self.issuer,
                audience=self.resource,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "scope", "client_id", "sid"]},
            )
            scopes = claims["scope"].split()
            if (claims["sub"] not in self.allowed_users or "openid" not in scopes
                    or not isinstance(claims["client_id"], str)
                    or not claims["client_id"] or not isinstance(claims["sid"], str)
                    or not claims["sid"]):
                return None
            return AccessToken(
                token=token, client_id=claims["client_id"], scopes=scopes,
                expires_at=claims["exp"], resource=self.resource,
                subject=claims["sub"], claims=claims,
            )
        except Exception:
            # Invalid tokens and unavailable signing keys both fail closed.
            return None


auth_options = {}
if os.getenv("TRANSPORT", "streamable-http") != "stdio":
    issuer = os.getenv("WORKOS_ISSUER", "").rstrip("/")
    resource = os.getenv("MCP_PUBLIC_URL", "").rstrip("/")
    allowed_users = {x.strip() for x in os.getenv("WORKOS_ALLOWED_USER_IDS", "").split(",") if x.strip()}
    if not issuer.startswith("https://") or not resource.startswith("https://") or not resource.endswith("/mcp") or not allowed_users:
        raise RuntimeError("HTTP mode requires WORKOS_ISSUER, MCP_PUBLIC_URL and WORKOS_ALLOWED_USER_IDS")
    auth_options = {
        "auth": AuthSettings(issuer_url=issuer, resource_server_url=resource,
                             required_scopes=["openid"], validate_token_resource=True),
        "token_verifier": WorkOSTokenVerifier(issuer, resource, allowed_users),
    }

mcp = FastMCP(
    "webcam",
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8787")),
    **auth_options,
)


async def _get(path, params=None):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{BASE}{path}", params=params,
                        headers={"x-windy-api-key": API_KEY})
        r.raise_for_status()
        return r.json()


def _slim(w):
    loc = w.get("location") or {}
    img = (w.get("images") or {}).get("current") or {}
    player = w.get("player") or {}
    return {
        "id": w.get("webcamId"),
        "title": w.get("title"),
        "status": w.get("status"),
        "city": loc.get("city"),
        "region": loc.get("region"),
        "country": loc.get("country"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "snapshot": img.get("preview") or img.get("thumbnail"),
        "live_player": player.get("live"),
        "day_timelapse": player.get("day"),
        "page": (w.get("urls") or {}).get("detail"),
        "last_updated": w.get("lastUpdatedOn"),
    }


MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
SNAPSHOT_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}

WEBCAM_WIDGET_URI = "ui://elsewhere/webcam.html"
WINDY_IMAGE_ORIGIN = "https://images-webcams.windy.com"
WEBCAM_WIDGET_META = {
    "ui": {
        "prefersBorder": False,
        "csp": {
            "resourceDomains": [WINDY_IMAGE_ORIGIN],
        },
    },
    "openai/ui": {"availableDisplayModes": ["inline"]},
    "openai/widgetDescription": "显示一张来自 Windy.com 的实时公共摄像头画面。",
    "openai/widgetPrefersBorder": False,
    "openai/widgetCSP": {
        "resource_domains": [WINDY_IMAGE_ORIGIN],
        "redirect_domains": ["https://www.windy.com"],
    },
}
SHOW_TOOL_META = {
    "ui": {"resourceUri": WEBCAM_WIDGET_URI},
    "openai/outputTemplate": WEBCAM_WIDGET_URI,
    "openai/toolInvocation/invoking": "正在看向远方…",
    "openai/toolInvocation/invoked": "拍到了",
}


class WebcamCard(BaseModel):
    webcam_id: str
    title: str | None = None
    location: str | None = None
    snapshot: str
    page: str | None = None
    last_updated: str | None = None
    provider: str = "Windy.com"


WEBCAM_WIDGET_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>此刻的远方</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; width: 100%; background: transparent; }
    body { padding: 2px; }
    #status {
      min-height: 180px;
      display: grid;
      place-items: center;
      color: #6b7280;
      font-size: 14px;
    }
    #card { width: 100%; }
    #picture {
      display: block;
      position: relative;
      overflow: hidden;
      width: 100%;
      min-height: 180px;
      border-radius: 16px;
      background: #111827;
      color: inherit;
      text-decoration: none;
    }
    #image {
      display: block;
      width: 100%;
      height: auto;
      max-height: 72vh;
      object-fit: contain;
      background: #111827;
    }
    #credit {
      position: absolute;
      right: 9px;
      bottom: 8px;
      padding: 4px 7px;
      border-radius: 999px;
      color: white;
      background: rgba(0, 0, 0, .52);
      font-size: 11px;
      line-height: 1;
      backdrop-filter: blur(4px);
    }
    #caption {
      padding: 10px 3px 2px;
    }
    #title {
      margin: 0;
      font-size: 15px;
      font-weight: 650;
      line-height: 1.35;
    }
    #location {
      margin: 3px 0 0;
      color: #6b7280;
      font-size: 12px;
      line-height: 1.35;
    }
    @media (prefers-color-scheme: dark) {
      #status, #location { color: #9ca3af; }
    }
  </style>
</head>
<body>
  <div id="status">正在把远处的光带过来…</div>
  <main id="card" hidden>
    <a id="picture" target="_blank" rel="noopener noreferrer">
      <img id="image" alt="">
      <span id="credit">Webcam · Windy.com</span>
    </a>
    <section id="caption">
      <p id="title"></p>
      <p id="location"></p>
    </section>
  </main>
  <script>
    const statusEl = document.getElementById("status");
    const cardEl = document.getElementById("card");
    const pictureEl = document.getElementById("picture");
    const imageEl = document.getElementById("image");
    const titleEl = document.getElementById("title");
    const locationEl = document.getElementById("location");

    function safeUrl(value, allowedHost) {
      try {
        const url = new URL(String(value || ""));
        if (url.protocol !== "https:" || url.hostname !== allowedHost) return null;
        return url.href;
      } catch (_) {
        return null;
      }
    }

    function render(response) {
      const output = response?.structuredContent || response;
      if (!output) return;

      const snapshot = safeUrl(output.snapshot, "images-webcams.windy.com");
      if (!snapshot) {
        statusEl.textContent = "这台摄像头暂时没有可显示的画面";
        return;
      }

      const page = safeUrl(output.page, "www.windy.com");
      imageEl.src = snapshot;
      imageEl.alt = output.title ? String(output.title) : "实时公共摄像头画面";
      titleEl.textContent = output.title ? String(output.title) : "此刻的远方";
      locationEl.textContent = output.location ? String(output.location) : "";
      pictureEl.href = page || snapshot;
      statusEl.hidden = true;
      cardEl.hidden = false;
    }

    imageEl.addEventListener("error", () => {
      cardEl.hidden = true;
      statusEl.hidden = false;
      statusEl.textContent = "画面刚刚过期了，再叫我看一次就好";
    });

    let nextRequestId = 1;
    const pending = new Map();

    function request(method, params) {
      const id = nextRequestId++;
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    }

    function notify(method, params) {
      window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
    }

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;

      if (message.id !== undefined && pending.has(message.id)) {
        const task = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) task.reject(message.error);
        else task.resolve(message.result);
        return;
      }

      if (message.method === "ui/notifications/tool-result") {
        render(message.params);
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", (event) => {
      render(event.detail?.globals?.toolOutput);
    }, { passive: true });

    if (window.openai?.toolOutput) render(window.openai.toolOutput);

    request("ui/initialize", {
      appInfo: { name: "elsewhere-webcam", version: "1.0.0" },
      appCapabilities: {},
      protocolVersion: "2026-01-26"
    }).then(() => {
      notify("ui/notifications/initialized", {});
    }).catch(() => {
      if (window.openai?.toolOutput) render(window.openai.toolOutput);
    });
  </script>
</body>
</html>"""


@mcp.resource(
    WEBCAM_WIDGET_URI,
    name="webcam-view",
    title="此刻的远方",
    description="在聊天中直接显示一张实时公共摄像头画面。",
    mime_type="text/html;profile=mcp-app",
    meta=WEBCAM_WIDGET_META,
)
def webcam_widget() -> str:
    return WEBCAM_WIDGET_HTML


async def _snapshot_bytes(webcam_id):
    info = await get_webcam(webcam_id)
    url = info.get("snapshot")
    if not url:
        raise ValueError("这个摄像头现在没有可用截图")
    if not url.startswith("https://"):
        raise ValueError("摄像头截图地址不是安全的 HTTPS 地址")
    try:
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise ValueError(f"摄像头截图请求失败（HTTP {r.status_code}）")
                if r.url.scheme != "https":
                    raise ValueError("摄像头截图重定向到非 HTTPS 地址")
                mime_type = r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                image_format = SNAPSHOT_TYPES.get(mime_type)
                if image_format is None:
                    raise ValueError("摄像头截图返回了不支持的图片类型")
                length = r.headers.get("content-length")
                if length:
                    try:
                        size = int(length)
                    except ValueError:
                        raise ValueError("摄像头截图响应长度无效") from None
                    if size > MAX_SNAPSHOT_BYTES:
                        raise ValueError("摄像头截图超过大小限制")
                data = bytearray()
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    if len(data) + len(chunk) > MAX_SNAPSHOT_BYTES:
                        raise ValueError("摄像头截图超过大小限制")
                    data.extend(chunk)
        return info, bytes(data), image_format
    except ValueError:
        raise
    except httpx.TimeoutException:
        raise ValueError("获取摄像头截图超时") from None
    except httpx.HTTPError:
        raise ValueError("获取摄像头截图失败") from None


@mcp.tool()
async def search_nearby(lat: float, lon: float, radius_km: int = 50,
                        category: str | None = None, limit: int = 10) -> list[dict]:
    """按经纬度搜索附近的实时摄像头。radius_km 最大 250。
    category 可选，如 beach, mountain, city, harbor, lake, landscape, sunset 等（用 list_categories 查全部）。"""
    params = {"nearby": f"{lat},{lon},{min(radius_km, 250)}",
              "include": INCLUDE, "limit": min(limit, 50)}
    if category:
        params["categories"] = category
    data = await _get("/webcams", params)
    return [_slim(w) for w in data.get("webcams", [])]


@mcp.tool()
async def search_by_country(country: str, category: str | None = None,
                            limit: int = 10, offset: int = 0) -> list[dict]:
    """按国家搜索摄像头。country 用两位国家代码，如 CN, JP, IS（冰岛）, NO, AU, NZ。"""
    params = {"countries": country.upper(), "include": INCLUDE,
              "limit": min(limit, 50), "offset": offset}
    if category:
        params["categories"] = category
    data = await _get("/webcams", params)
    return [_slim(w) for w in data.get("webcams", [])]


@mcp.tool()
async def list_categories() -> list[dict]:
    """列出所有摄像头类别。"""
    data = await _get("/categories")
    return data if isinstance(data, list) else data.get("categories", data)


@mcp.tool()
async def get_webcam(webcam_id: str) -> dict:
    """查看某个摄像头的详细信息、最新截图和直播播放器链接。"""
    data = await _get(f"/webcams/{webcam_id}", {"include": INCLUDE})
    return _slim(data)


@mcp.tool()
async def look(webcam_id: str) -> Image:
    """直接返回某个摄像头此刻的截图图片内容。"""
    _, data, image_format = await _snapshot_bytes(webcam_id)
    return Image(data=data, format=image_format)


@mcp.tool(
    meta=SHOW_TOOL_META,
    structured_output=True,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def show(webcam_id: str) -> WebcamCard:
    """把某个摄像头此刻的画面作为聊天内联图片卡片显示给用户。"""
    info = await get_webcam(webcam_id)
    snapshot = info.get("snapshot")
    parsed = urlparse(snapshot or "")
    if parsed.scheme != "https" or parsed.hostname != "images-webcams.windy.com":
        raise ValueError("这个摄像头现在没有可安全显示的画面")

    location_parts = []
    for value in (info.get("city"), info.get("region"), info.get("country")):
        if value and value not in location_parts:
            location_parts.append(value)

    page = info.get("page")
    page_url = urlparse(page or "")
    if page and (page_url.scheme != "https" or page_url.hostname != "www.windy.com"):
        page = None

    return WebcamCard(
        webcam_id=str(info.get("id") or webcam_id),
        title=info.get("title"),
        location=", ".join(location_parts) or None,
        snapshot=snapshot,
        page=page,
        last_updated=info.get("last_updated"),
    )


def _sun_points(event, now):
    doy = now.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (doy - 81)))
    utc_h = now.hour + now.minute / 60
    pts = []
    for lat in range(-45, 66, 10):
        x = -math.tan(math.radians(lat)) * math.tan(decl)
        if abs(x) > 1:
            continue
        h0 = math.degrees(math.acos(x)) / 15
        local = 12 - h0 if event == "sunrise" else 12 + h0
        lon = ((local - utc_h) * 15 + 540) % 360 - 180
        pts.append((lat, lon))
    return pts


@mcp.tool()
async def where_sun_is(event: str = "sunrise", limit: int = 5) -> list[dict]:
    """找世界上此刻正在日出（sunrise）或日落（sunset）的地方的摄像头。"""
    now = dt.datetime.now(dt.timezone.utc)
    pts = _sun_points(event, now)
    results = await asyncio.gather(
        *(search_nearby(lat, lon, radius_km=250, limit=3) for lat, lon in pts),
        return_exceptions=True,
    )
    found = []
    for cams in results:
        if isinstance(cams, Exception):
            continue
        found += [c for c in cams if c.get("status") == "active"]
    return found[:limit]


# ---------------- Movebank 动物追踪（官方 REST API，用你自己的 Movebank 账号） ----------------
# 文档：github.com/movebank/movebank-api-doc
# 账号从环境变量 MOVEBANK_USER_B64 / MOVEBANK_PASS_B64 读（base64，避开引号问题）。
# 需要接受条款的研究，不在代码里替用户接受，请用户去网页上用自己账号接受。

import csv
import io
import re

MB_BASE = "https://www.movebank.org/movebank/service/direct-read"
MB_SOURCE = "Movebank (movebank.org)，数据归各研究所有，使用遵守该研究的条款"


def _b64env(k):
    v = os.getenv(k)
    return base64.b64decode(v).decode() if v else None


MB_USER = _b64env("MOVEBANK_USER_B64")
MB_PASS = _b64env("MOVEBANK_PASS_B64")
_mb_cache: dict = {}
_mb_lock = asyncio.Lock()
_mb_last = 0.0


def _looks_like_csv(text):
    """正常返回的第一行是列名（全是小写字母下划线）；条款页、错误页不是。"""
    lines = text.lstrip().splitlines()
    return bool(lines) and re.fullmatch(r'"?[a-z_]+"?(,"?[a-z_]+"?)*', lines[0].strip()) is not None


async def _mb_get(params, ttl=1800):
    """带缓存和限速的请求，返回 CSV 行。出错时有旧缓存就用旧的。"""
    global _mb_last
    if not MB_USER or not MB_PASS:
        raise ValueError("服务器上没配置 Movebank 账号")
    key = tuple(sorted(params.items()))
    hit = _mb_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    async with _mb_lock:
        hit = _mb_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        wait = 1.0 - (time.time() - _mb_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                r = await c.get(MB_BASE, params=params, auth=(MB_USER, MB_PASS))
            _mb_last = time.time()
            if r.status_code == 401:
                raise ValueError("Movebank 账号或密码不对")
            r.raise_for_status()
            text = r.text
            if not _looks_like_csv(text):
                sid = params.get("study_id", "")
                raise ValueError(
                    f"研究 {sid} 需要先在 movebank.org 网页上用你的账号"
                    "接受数据使用条款，接受后再试")
            rows = list(csv.DictReader(io.StringIO(text)))
        except Exception:
            if hit:
                return hit[1]
            raise
        _mb_cache[key] = (time.time(), rows)
        return rows


def _mbts(s):
    if not s:
        return None
    s = s.strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


def _mbiso(s):
    t = _mbts(s)
    return t.isoformat().replace("+00:00", "Z") if t else None


def _iso(t):
    return t.isoformat(timespec="minutes") if t else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _mb_points(study_id, individual_id, days):
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days))
    rows = await _mb_get({
        "entity_type": "event",
        "study_id": str(study_id),
        "individual_id": str(individual_id),
        "attributes": "timestamp,location_lat,location_long",
        "timestamp_start": start.strftime("%Y%m%d%H%M%S") + "000",
    }, ttl=1800)
    pts = []
    for r in rows:
        lat, lon = _num(r.get("location_lat")), _num(r.get("location_long"))
        t = _mbts(r.get("timestamp"))
        if lat is None or lon is None or t is None:
            continue
        pts.append({"time": t, "lat": lat, "lon": lon})
    pts.sort(key=lambda p: p["time"])
    return pts


@mcp.tool()
async def list_studies(days: int = 30, taxon: str | None = None,
                       limit: int = 30) -> list[dict]:
    """Movebank：列出你的账号能下载、且最近 N 天还有新定位的动物追踪研究。
    taxon 按物种名筛，比如 Ciconia（鹳）、Chelonia（海龟）、Carcharodon（大白鲨）。"""
    rows = await _mb_get({"entity_type": "study",
                          "i_have_download_access": "true"}, ttl=21600)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    out = []
    for r in rows:
        if r.get("i_have_download_access", "true").lower() != "true":
            continue
        last = _mbts(r.get("timestamp_last_deployed_location"))
        if not last or last < since:
            continue
        tx = r.get("taxon_ids") or ""
        if taxon and taxon.lower() not in tx.lower():
            continue
        out.append({
            "study_id": r.get("id"),
            "name": r.get("name"),
            "taxon": tx,
            "last_location": last,
            "individuals": r.get("number_of_individuals"),
            "license": r.get("license_type"),
        })
    out.sort(key=lambda s: s["last_location"], reverse=True)
    for s in out:
        s["last_location"] = s["last_location"].isoformat().replace("+00:00", "Z")
    return out[:limit]


@mcp.tool()
async def list_animals(study_id: str, limit: int = 50) -> list[dict]:
    """Movebank：某个研究里的动物个体（名字、物种、性别、最后记录时间）。"""
    rows = await _mb_get({"entity_type": "individual",
                          "study_id": str(study_id)}, ttl=21600)
    out = [{
        "individual_id": r.get("id"),
        "name": r.get("nick_name") or r.get("local_identifier"),
        "local_identifier": r.get("local_identifier"),
        "taxon": r.get("taxon_canonical_name"),
        "sex": r.get("sex"),
        "last_seen": _mbiso(r.get("timestamp_end")),
    } for r in rows]
    out.sort(key=lambda a: a["last_seen"] or "", reverse=True)
    return out[:limit]


async def _animal_info(study_id, individual_id):
    try:
        for a in await list_animals(study_id, limit=100000):
            if str(a["individual_id"]) == str(individual_id):
                return {k: a[k] for k in ("name", "taxon", "sex")}
    except Exception:
        pass
    return {}


@mcp.tool()
async def animal_track(study_id: str, individual_id: str,
                       days: int = 30) -> dict:
    """Movebank：某只动物最近 N 天的定位点（按时间排序，最多 500 个，多了均匀抽样）。"""
    pts = await _mb_points(study_id, individual_id, days)
    if len(pts) > 500:
        step = len(pts) / 500
        pts = [pts[int(i * step)] for i in range(499)] + [pts[-1]]
    return {
        "study_id": study_id,
        "individual_id": individual_id,
        **await _animal_info(study_id, individual_id),
        "days": days,
        "count": len(pts),
        "points": [{"time": _iso(p["time"]), "lat": p["lat"], "lon": p["lon"]}
                   for p in pts],
        "source": MB_SOURCE,
    }


@mcp.tool()
async def animal_latest(study_id: str, individual_id: str) -> dict:
    """Movebank：某只动物最新一次定位（最近 60 天内）。"""
    pts = await _mb_points(study_id, individual_id, 60)
    last = pts[-1] if pts else None
    return {
        "study_id": study_id,
        "individual_id": individual_id,
        **await _animal_info(study_id, individual_id),
        "time": _iso(last["time"]) if last else None,
        "lat": last["lat"] if last else None,
        "lon": last["lon"] if last else None,
        "note": None if last else "最近 60 天没有定位",
        "source": MB_SOURCE,
    }


# ---------------- 天气（Open-Meteo 免费接口，不用钥匙） ----------------

OM_URL = "https://api.open-meteo.com/v1/forecast"
OM_CURRENT = ("temperature_2m,apparent_temperature,wind_speed_10m,"
              "wind_direction_10m,cloud_cover,precipitation,weather_code,is_day")
_DIRS = ["北", "东北偏北", "东北", "东北偏东", "东", "东南偏东", "东南", "东南偏南",
         "南", "西南偏南", "西南", "西南偏西", "西", "西北偏西", "西北", "西北偏北"]
_WMO = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}
_om_cache: dict = {}


def _wind_dir(deg):
    if deg is None:
        return None
    return _DIRS[int((deg % 360) / 22.5 + 0.5) % 16]


@mcp.tool()
async def weather_at(lat: float, lon: float) -> dict:
    """某个经纬度此刻的天气：温度、体感、风速风向、云量、降水，和当地时间。
    数据来自 Open-Meteo，10 分钟内同一地点不重复请求。"""
    key = (round(lat, 2), round(lon, 2))
    hit = _om_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        data = hit[1]
    else:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(OM_URL, params={
                "latitude": lat, "longitude": lon, "current": OM_CURRENT,
                "timezone": "auto", "wind_speed_unit": "kmh",
            })
            r.raise_for_status()
            data = r.json()
        _om_cache[key] = (time.time(), data)
    cur = data.get("current") or {}
    off = data.get("utc_offset_seconds") or 0
    now_local = (dt.datetime.now(dt.timezone.utc)
                 + dt.timedelta(seconds=off)).replace(tzinfo=None)
    wd = cur.get("wind_direction_10m")
    code = cur.get("weather_code")
    return {
        "lat": data.get("latitude", lat),
        "lon": data.get("longitude", lon),
        "local_time": now_local.isoformat(timespec="minutes"),
        "timezone": data.get("timezone"),
        "utc_offset_hours": off / 3600,
        "observed_at": cur.get("time"),
        "is_day": bool(cur.get("is_day")) if cur.get("is_day") is not None else None,
        "weather": _WMO.get(code, f"代码 {code}") if code is not None else None,
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "wind_speed_kmh": cur.get("wind_speed_10m"),
        "wind_direction_deg": wd,
        "wind_from": _wind_dir(wd),
        "cloud_cover_pct": cur.get("cloud_cover"),
        "precipitation_mm": cur.get("precipitation"),
        "source": "Open-Meteo (open-meteo.com)",
    }


if __name__ == "__main__":
    # 默认跑成网络服务；Claude Desktop 这类本地客户端用 TRANSPORT=stdio
    mcp.run(transport=os.getenv("TRANSPORT", "streamable-http"))
