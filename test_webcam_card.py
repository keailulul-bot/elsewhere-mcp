import base64
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("WINDY_API_KEY", "test-key")
os.environ.setdefault("TRANSPORT", "stdio")

import server  # 放回仓库时改为 import server


class WebcamCardTests(unittest.IsolatedAsyncioTestCase):
    def test_widget_metadata_and_minimal_html(self):
        self.assertEqual(server.WEBCAM_WIDGET_URI, "ui://elsewhere/webcam.html")
        self.assertFalse(server.WEBCAM_WIDGET_META["ui"]["prefersBorder"])
        self.assertFalse(server.WEBCAM_WIDGET_META["openai/widgetPrefersBorder"])
        self.assertNotIn("csp", server.WEBCAM_WIDGET_META["ui"])
        self.assertNotIn("openai/widgetCSP", server.WEBCAM_WIDGET_META)

        html = server.webcam_widget()
        self.assertIn("ui/notifications/tool-result", html)
        self.assertIn("openai:set_globals", html)
        self.assertIn("safeDataImageUri", html)
        self.assertNotIn("images-webcams.windy.com", html)
        self.assertNotIn("Windy.com", html)
        self.assertNotIn("<a ", html)

    def test_tool_metadata(self):
        self.assertEqual(
            server.SHOW_TOOL_META["ui"]["resourceUri"],
            server.WEBCAM_WIDGET_URI,
        )
        self.assertEqual(
            server.SHOW_TOOL_META["openai/outputTemplate"],
            server.WEBCAM_WIDGET_URI,
        )

    async def test_show_returns_data_uri_card(self):
        fake_info = {
            "id": "1450843353",
            "title": "Cradle Mountain",
            "city": "Cradle Mountain",
            "region": "Tasmania",
            "country": "Australia",
            "last_updated": "2026-11-19T08:00:00Z",
        }
        fake_bytes = b"\xff\xd8\xff\xe0test-jpeg"
        expected_b64 = base64.b64encode(fake_bytes).decode("ascii")

        with patch.object(
            server,
            "_snapshot_bytes",
            AsyncMock(return_value=(fake_info, fake_bytes, "jpeg")),
        ):
            card = await server.show("1450843353")

        self.assertEqual(card.webcam_id, "1450843353")
        self.assertEqual(card.title, "Cradle Mountain")
        self.assertEqual(card.location, "Cradle Mountain, Tasmania, Australia")
        self.assertEqual(card.snapshot, f"data:image/jpeg;base64,{expected_b64}")

    async def test_look_still_returns_mcp_image(self):
        fake_bytes = b"\x89PNG\r\n\x1a\ntest-png"
        with patch.object(
            server,
            "_snapshot_bytes",
            AsyncMock(return_value=({}, fake_bytes, "png")),
        ):
            img = await server.look("1450843353")

        self.assertIsInstance(img, server.Image)
        self.assertEqual(img.data, fake_bytes)


if __name__ == "__main__":
    unittest.main()