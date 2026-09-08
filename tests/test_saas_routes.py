import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from saas.router import build_router
from saas.service import SaaSService


class RouteTest(unittest.TestCase):
    def test_http_contract_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SaaSService(Path(directory) / "state.db")
            app = FastAPI()
            app.include_router(build_router(service))
            with TestClient(app) as client:
                self.assertEqual(client.get("/saas/me/invoices").status_code, 401)
                login = client.post("/saas/demo/login", json={"user_id": "aurora_owner", "org_id": "org_aurora"})
                headers = {"Authorization": "Bearer " + login.json()["token"]}
                result = client.post("/saas/subscription/change-previews", json={"target_plan": "growth_v1"}, headers=headers)
                op = result.json()["operation_id"]
                self.assertEqual(client.post("/saas/subscription/changes", json={"operation_id": op, "confirmed": True}, headers=headers).status_code, 422)
                self.assertEqual(client.post("/saas/operations/" + op + "/confirm", headers=headers).status_code, 200)
                result = client.post("/saas/subscription/changes", json={"operation_id": op}, headers=headers)
                self.assertEqual(result.json()["status"], "scheduled")
                self.assertEqual(client.get("/saas/integrations/int_beacon/requests", headers=headers).status_code, 404)
                self.assertEqual(client.post("/saas/demo/advance-clock", json={"timestamp": "2026-10-01T00:00:00Z"}, headers=headers).status_code, 403)
