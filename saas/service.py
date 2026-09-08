"""Small transactional sandbox. All UI and Agent writes use this service."""
import calendar
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from saas.fixtures import NOW, PLANS, POLICY_VERSION, scenarios


class BusinessError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Actor:
    user_id: str
    org_id: str
    role: str


PERMISSIONS = {
    "owner": {"integration.read", "billing.read", "subscription.change", "member.invite"},
    "admin": {"integration.read", "member.invite"},
    "billing_admin": {"billing.read", "subscription.change"},
    "developer": {"integration.read"},
}


class SaaSService:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.lock = threading.RLock()
        with self.tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT, org_id TEXT, expires_at TEXT);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, org_id TEXT, actor_id TEXT,
                    kind TEXT, fingerprint TEXT, status TEXT, payload TEXT, receipt TEXT,
                    idem TEXT, UNIQUE(org_id, actor_id, kind, idem));
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, org_id TEXT, actor_id TEXT,
                    operation_id TEXT UNIQUE, kind TEXT, created_at TEXT);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('clock', ?)", (NOW,))
            for org, state in scenarios().items():
                db.execute("INSERT OR IGNORE INTO organizations VALUES (?,?)", (org, json.dumps(state)))

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _state(db, org):
        row = db.execute("SELECT state FROM organizations WHERE id=?", (org,)).fetchone()
        if not row:
            raise BusinessError("not_found", "组织不存在", 404)
        return json.loads(row[0])

    @staticmethod
    def _save(db, org, state):
        db.execute("UPDATE organizations SET state=? WHERE id=?", (json.dumps(state), org))

    @staticmethod
    def _now(db):
        return datetime.fromisoformat(db.execute("SELECT value FROM settings WHERE key='clock'").fetchone()[0])

    @staticmethod
    def _authorize(db, actor, permission=None):
        state = SaaSService._state(db, actor.org_id)
        member = next((m for m in state["members"] if m["user_id"] == actor.user_id), None)
        if not member or (permission and permission not in PERMISSIONS.get(member["role"], set())):
            raise BusinessError("denied", "当前组织身份无权执行此操作", 403)
        return state

    def login(self, user_id, org_id):
        with self.tx() as db:
            state = self._state(db, org_id)
            if not any(m["user_id"] == user_id for m in state["members"]):
                raise BusinessError("denied", "演示账号不属于该组织", 403)
            token = secrets.token_urlsafe(32)
            expires = (datetime.now().astimezone() + timedelta(hours=12)).isoformat()
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (self._hash(token), user_id, org_id, expires))
            return {"token": token, "user_id": user_id, "org_id": org_id, "demo": True}

    def actor(self, token):
        with self.tx() as db:
            row = db.execute("SELECT * FROM sessions WHERE token=?", (self._hash(token),)).fetchone()
            if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now().astimezone():
                raise BusinessError("unauthorized", "请登录演示账号", 401)
            state = self._state(db, row["org_id"])
            member = next((m for m in state["members"] if m["user_id"] == row["user_id"]), None)
            if not member:
                raise BusinessError("unauthorized", "成员身份已失效", 401)
            return Actor(row["user_id"], row["org_id"], member["role"])

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def read(self, actor, resource, integration_id=None):
        permission = {"subscription": "billing.read", "invoices": "billing.read", "requests": "integration.read", "integrations": "integration.read"}.get(resource)
        with self.tx() as db:
            state = self._authorize(db, actor, permission)
            plan = PLANS[state["subscription"]["plan_id"]]
            if resource == "plans":
                data = PLANS
            elif resource == "entitlements":
                data = {"plan_id": state["subscription"]["plan_id"], **{k: v for k, v in plan.items() if k != "price_minor"}}
            elif resource == "usage":
                data = {**state["usage"], "limit": plan["api_limit"]}
            elif resource == "members":
                data = {"members": [{"user_id": m["user_id"], "role": m["role"]} for m in state["members"]],
                        "invitations": [{"id": i["id"], "status": i["status"], "role": i["role"]} for i in state["invitations"]], "seats": plan["seats"]}
            elif resource == "requests":
                if not any(i["id"] == integration_id for i in state["integrations"]):
                    raise BusinessError("not_found", "集成不存在", 404)
                data = [r for r in state["requests"] if r["integration_id"] == integration_id]
            elif resource in {"subscription", "invoices", "integrations"}:
                data = state[resource]
            else:
                raise BusinessError("not_found", "资源不存在", 404)
            return {"status": "ok", "org_id": actor.org_id, "resource": resource,
                    "as_of": self._now(db).isoformat(), "version": state["subscription"]["version"], "data": data}

    def preview(self, actor, target_plan):
        with self.tx() as db:
            state = self._authorize(db, actor, "subscription.change")
            if target_plan not in PLANS:
                raise BusinessError("invalid_plan", "未知套餐")
            self._check_seats(state, target_plan)
            sub = state["subscription"]
            if target_plan == sub["plan_id"]:
                raise BusinessError("unchanged", "目标与当前套餐相同")
            payload = {"target_plan": target_plan, "current_plan": sub["plan_id"], "resource_version": sub["version"],
                       "effective_at": sub["period_end"], "current_period_charge_minor": 0,
                       "next_period_price_minor": PLANS[target_plan]["price_minor"], "currency": "CNY",
                       "policy_version": POLICY_VERSION, "expires_at": (self._now(db) + timedelta(minutes=15)).isoformat()}
            op = "op_" + secrets.token_hex(12)
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)",
                       (op, actor.org_id, actor.user_id, "subscription", self._hash(json.dumps(payload, sort_keys=True)), "prepared", json.dumps(payload), None, op))
            return {"status": "requires_confirmation", "operation_id": op, "preview": payload}

    def _operation(self, db, actor, op):
        self._authorize(db, actor)
        row = db.execute("SELECT * FROM operations WHERE id=? AND org_id=? AND actor_id=?", (op, actor.org_id, actor.user_id)).fetchone()
        if not row:
            raise BusinessError("not_found", "操作不存在", 404)
        return row

    def confirm(self, actor, op):
        with self.tx() as db:
            row = self._operation(db, actor, op)
            self._authorize(db, actor, "subscription.change")
            if row["kind"] == "subscription" and row["receipt"]:
                return json.loads(row["receipt"])
            self._validate_preview(db, actor, row)
            if row["status"] in {"prepared", "confirmed"}:
                db.execute("UPDATE operations SET status='confirmed' WHERE id=?", (op,))
            return {"status": "confirmed", "operation_id": op}

    def _validate_preview(self, db, actor, row):
        state = self._authorize(db, actor, "subscription.change")
        if row["kind"] != "subscription":
            raise BusinessError("invalid_operation", "不是订阅操作")
        payload = json.loads(row["payload"])
        if datetime.fromisoformat(payload["expires_at"]) <= self._now(db):
            raise BusinessError("expired", "预览已过期，请重新生成", 409)
        if payload["policy_version"] != POLICY_VERSION or payload["resource_version"] != state["subscription"]["version"]:
            raise BusinessError("conflict", "资源或规则已变化，请重新预览", 409)
        return state, payload

    def apply(self, actor, op):
        with self.tx() as db:
            row = self._operation(db, actor, op)
            self._authorize(db, actor, "subscription.change")
            if row["receipt"]:
                return json.loads(row["receipt"])
            state, payload = self._validate_preview(db, actor, row)
            if row["status"] != "confirmed":
                raise BusinessError("confirmation_required", "请先确认具体变更", 409)
            self._check_seats(state, payload["target_plan"])
            state["subscription"]["scheduled_plan_id"] = payload["target_plan"]
            state["subscription"]["version"] += 1
            receipt = {"status": "scheduled", "operation_id": op, "resource_id": state["subscription"]["id"],
                       "current_plan": state["subscription"]["plan_id"], "target_plan": payload["target_plan"],
                       "effective_at": payload["effective_at"], "version": state["subscription"]["version"]}
            self._save(db, actor.org_id, state)
            self._finish(db, actor, op, "subscription", receipt)
            return receipt

    @staticmethod
    def _check_seats(state, plan, extra=0):
        count = len(state["members"]) + sum(i["status"] == "pending" for i in state["invitations"])
        if count + extra > PLANS[plan]["seats"]:
            raise BusinessError("seats_full", "当前席位不足（待接受邀请也占席位）", 409)

    def invite(self, actor, email, idempotency_key):
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise BusinessError("invalid_email", "请输入有效邮箱")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise BusinessError("invalid_key", "需要有效幂等键")
        fingerprint = self._hash(email)
        with self.tx() as db:
            state = self._authorize(db, actor, "member.invite")
            row = db.execute("SELECT * FROM operations WHERE org_id=? AND actor_id=? AND kind='invite' AND idem=?", (actor.org_id, actor.user_id, idempotency_key)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise BusinessError("conflict", "幂等键已用于不同参数", 409)
                return json.loads(row["receipt"])
            if any(m["email"] == email for m in state["members"]):
                raise BusinessError("already_member", "该用户已是成员", 409)
            existing = next((i for i in state["invitations"] if i["email"] == email and i["status"] == "pending"), None)
            if existing:
                return {"status": "pending", "invitation": existing, "duplicate": True, "simulated": True}
            self._check_seats(state, state["subscription"]["plan_id"], extra=1)
            op = "op_" + secrets.token_hex(12)
            invitation = {"id": "inv_" + secrets.token_hex(8), "email": email, "role": "developer", "status": "pending",
                          "expires_at": (self._now(db) + timedelta(days=7)).isoformat()}
            state["invitations"].append(invitation)
            receipt = {"status": "pending", "operation_id": op, "invitation": invitation, "simulated": True, "email_sent": False}
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)", (op, actor.org_id, actor.user_id, "invite", fingerprint, "prepared", json.dumps({"email": email}), None, idempotency_key))
            self._save(db, actor.org_id, state)
            self._finish(db, actor, op, "invite", receipt)
            return receipt

    def _finish(self, db, actor, op, kind, receipt):
        db.execute("UPDATE operations SET status='succeeded', receipt=? WHERE id=?", (json.dumps(receipt), op))
        db.execute("INSERT INTO audit(org_id,actor_id,operation_id,kind,created_at) VALUES (?,?,?,?,?)", (actor.org_id, actor.user_id, op, kind, self._now(db).isoformat()))

    def operation(self, actor, op):
        with self.tx() as db:
            row = self._operation(db, actor, op)
            self._authorize(db, actor, "subscription.change" if row["kind"] == "subscription" else "member.invite")
            return json.loads(row["receipt"]) if row["receipt"] else {"status": row["status"], "operation_id": op, "preview": json.loads(row["payload"])}

    def advance_clock(self, timestamp):
        try:
            target = datetime.fromisoformat(timestamp)
            if target.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise BusinessError("invalid_time", "需要带时区的 ISO 时间")
        with self.tx() as db:
            if target < self._now(db) or target > self._now(db) + timedelta(days=366):
                raise BusinessError("invalid_time", "只能向前推进，且一次最多一年")
            for row in db.execute("SELECT id,state FROM organizations").fetchall():
                state = json.loads(row["state"])
                for invite in state["invitations"]:
                    if invite["status"] == "pending" and datetime.fromisoformat(invite["expires_at"]) <= target:
                        invite["status"] = "expired"
                sub = state["subscription"]
                while datetime.fromisoformat(sub["period_end"]) <= target:
                    start = datetime.fromisoformat(sub["period_end"])
                    if sub["scheduled_plan_id"]:
                        try:
                            self._check_seats(state, sub["scheduled_plan_id"])
                            sub["plan_id"] = sub["scheduled_plan_id"]
                            sub["change_status"] = "applied"
                        except BusinessError:
                            sub["change_status"] = "blocked"
                        sub["scheduled_plan_id"] = None
                    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
                    end = start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
                    sub.update(period_start=start.isoformat(), period_end=end.isoformat(), version=sub["version"] + 1)
                    amount = PLANS[sub["plan_id"]]["price_minor"]
                    state["invoices"].append({"id": f"inv_{row['id']}_{start:%Y%m}", "period_start": start.isoformat(), "total_minor": amount, "currency": "CNY", "status": "open", "lines": [{"description": sub["plan_id"], "amount_minor": amount}], "simulated": True})
                    state["usage"].update(used=0, period=start.strftime("%Y-%m"))
                self._save(db, row["id"], state)
            db.execute("UPDATE settings SET value=? WHERE key='clock'", (target.isoformat(),))
            return {"status": "ok", "as_of": target.isoformat()}
