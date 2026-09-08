import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from saas.service import Actor, BusinessError, SaaSService


class SaaSTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = SaaSService(Path(self.temp.name) / "saas.db")
        self.owner = Actor("aurora_owner", "org_aurora", "owner")
        self.dev = Actor("aurora_developer", "org_aurora", "developer")

    def fails(self, code, fn, *args):
        with self.assertRaises(BusinessError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)

    def test_identity_and_isolation(self):
        token = self.s.login(self.dev.user_id, self.dev.org_id)["token"]
        self.assertEqual(self.s.actor(token), self.dev)
        self.fails("denied", self.s.login, "aurora_owner", "org_cedar")
        forged = Actor(self.dev.user_id, self.dev.org_id, "owner")
        self.fails("denied", self.s.read, forged, "invoices")
        self.fails("not_found", self.s.read, self.dev, "requests", "int_beacon")

    def test_preview_confirmation_and_idempotent_apply(self):
        op = self.s.preview(self.owner, "growth_v1")["operation_id"]
        self.fails("confirmation_required", self.s.apply, self.owner, op)
        self.s.confirm(self.owner, op)
        receipt = self.s.apply(self.owner, op)
        self.assertEqual(receipt, self.s.apply(self.owner, op))
        self.assertEqual(receipt, self.s.confirm(self.owner, op))
        sub = self.s.read(self.owner, "subscription")["data"]
        self.assertEqual((sub["plan_id"], sub["scheduled_plan_id"]), ("starter_v1", "growth_v1"))
        with self.s.tx() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 1)
        self.s.advance_clock("2026-10-01T00:00:00+00:00")
        self.s.advance_clock("2026-10-01T00:00:00+00:00")
        self.assertEqual(self.s.read(self.owner, "subscription")["data"]["plan_id"], "growth_v1")
        self.assertEqual(len(self.s.read(self.owner, "invoices")["data"]), 2)

    def test_stale_and_expired_preview(self):
        first = self.s.preview(self.owner, "growth_v1")["operation_id"]
        second = self.s.preview(self.owner, "growth_v1")["operation_id"]
        self.s.confirm(self.owner, first)
        self.s.apply(self.owner, first)
        self.fails("conflict", self.s.confirm, self.owner, second)
        third = self.s.preview(self.owner, "growth_v1")["operation_id"]
        self.s.advance_clock("2026-09-08T03:00:00+00:00")
        self.fails("expired", self.s.confirm, self.owner, third)

    def test_invites_reserve_seats_and_deduplicate(self):
        receipt = self.s.invite(self.owner, "new@aurora.example", "one")
        self.assertEqual(receipt, self.s.invite(self.owner, "NEW@aurora.example", "one"))
        self.assertTrue(self.s.invite(self.owner, "new@aurora.example", "two")["duplicate"])
        self.fails("conflict", self.s.invite, self.owner, "other@aurora.example", "one")
        self.fails("seats_full", self.s.invite, self.owner, "other@aurora.example", "three")
        self.fails("denied", self.s.invite, self.dev, "other@aurora.example", "four")

    def test_concurrent_invites_do_not_overbook(self):
        def invite(i):
            try:
                return self.s.invite(self.owner, f"person{i}@aurora.example", str(i))["status"]
            except BusinessError as ex:
                return ex.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(invite, range(2))), ["pending", "seats_full"])

    def test_other_actor_cannot_confirm(self):
        op = self.s.preview(self.owner, "growth_v1")["operation_id"]
        self.fails("not_found", self.s.confirm, self.dev, op)


if __name__ == "__main__":
    unittest.main()
