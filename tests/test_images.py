import base64
import os
import struct
import unittest
import zlib
from unittest.mock import AsyncMock, patch

import httpx
from mcp.types import ImageContent

os.environ.update({
    "WINDY_API_KEY": "test-only",
    "WORKOS_ISSUER": "https://auth.example.test",
    "MCP_PUBLIC_URL": "https://demo.onrender.com/mcp",
    "WORKOS_ALLOWED_USER_IDS": "user_allowed",
})

import server


REAL_ASYNC_CLIENT = httpx.AsyncClient


def _png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


TEST_IMAGE = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
    + _png_chunk(b"IEND", b"")
)


class ImageTests(unittest.IsolatedAsyncioTestCase):
    async def _snapshot_response(self, *, body=TEST_IMAGE, mime="image/png",
                                headers=None):
        response_headers = {"content-type": mime, **(headers or {})}
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, headers=response_headers, content=body)
        )
        client_factory = lambda **kwargs: REAL_ASYNC_CLIENT(
            transport=transport, **kwargs
        )
        with (
            patch.object(server, "get_webcam", new=AsyncMock(return_value={
                "id": "camera-1", "snapshot": "https://images.example.test/latest.jpg"
            })),
            patch.object(server.httpx, "AsyncClient", side_effect=client_factory),
        ):
            return await server._snapshot_bytes("camera-1")

    async def test_show_returns_mcp_image_content_without_github_settings(self):
        with patch.object(server, "_snapshot_bytes", new=AsyncMock(return_value=(
            {"id": "camera-1"}, TEST_IMAGE, "png"
        ))):
            result = await server.mcp.call_tool("show", {"webcam_id": "camera-1"})
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ImageContent)
        self.assertEqual(result[0].mimeType, "image/png")
        self.assertEqual(base64.b64decode(result[0].data), TEST_IMAGE)
        self.assertFalse(hasattr(server, "GH_TOKEN"))
        self.assertFalse(hasattr(server, "GH_REPO"))

    async def test_search_by_country_keeps_existing_query(self):
        webcam = {"webcamId": "camera-1", "status": "active",
                  "images": {"current": {"preview": "https://images.example.test/a.png"}}}
        with patch.object(server, "_get", new=AsyncMock(return_value={"webcams": [webcam]})) as get:
            result = await server.search_by_country("AU", limit=1)
        self.assertEqual(result[0]["id"], "camera-1")
        self.assertEqual(result[0]["snapshot"], "https://images.example.test/a.png")
        get.assert_awaited_once_with("/webcams", {
            "countries": "AU", "include": server.INCLUDE, "limit": 1, "offset": 0
        })

    async def test_where_sun_is_keeps_active_camera_filter(self):
        cameras = [
            {"id": "active", "status": "active"},
            {"id": "offline", "status": "inactive"},
        ]
        with patch.object(server, "search_nearby", new=AsyncMock(return_value=cameras)):
            result = await server.where_sun_is(event="sunset", limit=5)
        self.assertTrue(result)
        self.assertEqual({camera["id"] for camera in result}, {"active"})
        self.assertTrue(all(camera["status"] == "active" for camera in result))

    async def test_snapshot_requires_supported_image_content_type(self):
        with self.assertRaisesRegex(ValueError, "不支持的图片类型"):
            await self._snapshot_response(mime="text/html")

    async def test_snapshot_rejects_oversized_response_from_header(self):
        with self.assertRaisesRegex(ValueError, "超过大小限制"):
            await self._snapshot_response(headers={
                "content-length": str(server.MAX_SNAPSHOT_BYTES + 1)
            })

    async def test_snapshot_rejects_oversized_stream(self):
        with self.assertRaisesRegex(ValueError, "超过大小限制"):
            await self._snapshot_response(body=b"x" * (server.MAX_SNAPSHOT_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
