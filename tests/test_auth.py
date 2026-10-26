import os
import time
import unittest
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.update({
    "WINDY_API_KEY": "test-only",
    "WORKOS_ISSUER": "https://auth.example.test",
    "MCP_PUBLIC_URL": "https://demo.onrender.com/mcp",
    "WORKOS_ALLOWED_USER_IDS": "user_allowed",
    "HOST": "0.0.0.0",
    "PORT": "10000",
})

import server


class AuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server.auth_options["token_verifier"].keys.get_signing_key_from_jwt = (
            lambda _: SimpleNamespace(key=cls.key.public_key())
        )

    def token(self, **changes):
        claims = {
            "iss": "https://auth.example.test",
            "aud": "https://demo.onrender.com/mcp",
            "sub": "user_allowed",
            "client_id": "chatgpt-client",
            "sid": "consent-id",
            "scope": "openid profile",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256")

    async def test_token_policy(self):
        verifier = server.auth_options["token_verifier"]
        accepted = await verifier.verify_token(self.token())
        self.assertEqual(accepted.subject, "user_allowed")
        self.assertEqual(accepted.resource, "https://demo.onrender.com/mcp")
        for changes in (
            {"iss": "https://wrong.example.test"},
            {"aud": "https://other.onrender.com/mcp"},
            {"sub": "user_unapproved"},
            {"scope": "profile"},
            {"exp": int(time.time()) - 1},
            {"sid": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(await verifier.verify_token(self.token(**changes)))
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged_claims = jwt.decode(self.token(), options={"verify_signature": False})
        forged = jwt.encode(forged_claims, other_key, algorithm="RS256")
        self.assertIsNone(await verifier.verify_token(forged))

    async def test_http_auth_discovery_and_tools(self):
        self.assertEqual(server.mcp.settings.host, "0.0.0.0")
        self.assertEqual(server.mcp.settings.port, 10000)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.mcp.streamable_http_app()),
            base_url="https://demo.onrender.com",
        ) as client:
            blocked = await client.get("/mcp")
            self.assertEqual(blocked.status_code, 401)
            self.assertIn("resource_metadata=", blocked.headers["www-authenticate"])
            metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["resource"], "https://demo.onrender.com/mcp")
            self.assertEqual(metadata.json()["authorization_servers"],
                             ["https://auth.example.test/"])
        app = server.mcp.streamable_http_app()
        headers = {
            "Authorization": "Bearer " + self.token(),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://demo.onrender.com",
            ) as client:
                initialized = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "1"}},
                })
                self.assertEqual(initialized.status_code, 200)
                headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
                headers["Mcp-Protocol-Version"] = "2025-06-18"
                await client.post("/mcp", headers=headers,
                                  json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                listed = await client.post("/mcp", headers=headers,
                                           json={"jsonrpc": "2.0", "id": 2,
                                                 "method": "tools/list", "params": {}})
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.text.count('"name":'), 12)
        self.assertEqual(len(server.mcp._tool_manager.list_tools()), 12)


if __name__ == "__main__":
    unittest.main()
