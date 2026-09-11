"""The deployed import path must expose only the current support workspace."""
import importlib
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient


class EntrypointTests(unittest.TestCase):
    def test_default_entrypoint_login_chat_and_removed_routes(self):
        directory = tempfile.mkdtemp(prefix="ff-entrypoint-")
        with patch.dict("os.environ", {"ECHOMIND_DEMO": "0", "ECHOMIND_DEMO_LLM": "0",
                                      "ECHOMIND_DEMO_REDIS_URL": "", "ECHOMIND_DEMO_DATA": directory}):
            module = importlib.import_module("api.main")
            with TestClient(module.app) as client:
                self.assertEqual(client.get("/ready").status_code, 200)
                self.assertEqual(client.get("/health").json()["engine"], "deterministic_demo")
                token = client.post("/saas/demo/login", json={"org_id": "org_aurora", "user_id": "aurora_owner"}).json()["token"]
                headers = {"Authorization": "Bearer " + token}
                response = client.post("/chat", headers=headers, json={"message": "查询订阅"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["domain"], "billing")
                self.assertEqual(client.get("/monitor", headers=headers).status_code, 200)
                search = client.post("/search", params={"query": "401", "top_k": 3}, headers=headers)
                self.assertEqual(search.status_code, 200)
                self.assertTrue(all(item["org_id"] in {"global", "org_aurora"} for item in search.json()["results"]))
                for path in ("/skills/reload", "/knowledge/add", "/eval/run"):
                    self.assertEqual(client.post(path).status_code, 404)
