"""Synthetic business facts; prices are CNY minor units, never real charges."""
from copy import deepcopy

NOW = "2026-09-08T02:00:00+00:00"
POLICY_VERSION = "flowforge-v1"
PLANS = {
    "starter_v1": {"name": "Starter", "price_minor": 99000, "seats": 5, "api_limit": 1000000, "rate_limit": 60, "retention_days": 7},
    "growth_v1": {"name": "Growth", "price_minor": 299000, "seats": 20, "api_limit": 10000000, "rate_limit": 300, "retention_days": 30},
}


def scenarios():
    result = {}
    for name, plan, seats, used, code, reason in [
        ("aurora", "starter_v1", 4, 800000, 401, "auth_environment_mismatch"),
        ("beacon", "growth_v1", 19, 2500000, 429, "rate_limit"),
        ("cedar", "starter_v1", 5, 1000000, 429, "quota_exhausted"),
    ]:
        members = [{"user_id": f"{name}_{role}", "role": role, "email": f"{role}@{name}.example"}
                   for role in ("owner", "admin", "billing_admin", "developer")]
        members += [{"user_id": f"{name}_dev_{i}", "role": "developer", "email": f"dev{i}@{name}.example"}
                    for i in range(seats - 4)]
        result[f"org_{name}"] = {
            "name": name.title(), "members": members,
            "invitations": ([{"id": "invite_beacon", "email": "pending@beacon.example", "role": "developer", "status": "pending", "expires_at": "2026-10-02T00:00:00+00:00"}] if name == "beacon" else []),
            "subscription": {"id": f"sub_{name}", "plan_id": plan, "scheduled_plan_id": None, "version": 1,
                             "status": "active", "period_start": "2026-09-01T00:00:00+00:00", "period_end": "2026-10-01T00:00:00+00:00"},
            "invoices": [{"id": f"inv_{name}_202609", "period_start": "2026-09-01T00:00:00+00:00",
                          "total_minor": PLANS[plan]["price_minor"], "currency": "CNY", "status": "open" if name == "beacon" else "paid",
                          "lines": [{"description": PLANS[plan]["name"], "amount_minor": PLANS[plan]["price_minor"]}], "simulated": True}],
            "usage": {"metric": "api_requests", "used": used, "period": "2026-09"},
            "integrations": [{"id": f"int_{name}", "project_id": f"proj_{name}", "type": "webhook", "environment": "production"}],
            "requests": [{"request_id": f"req_{name}_001", "integration_id": f"int_{name}", "status_code": code,
                          "reason_code": reason, "timestamp": NOW, "credential_label": "test_fixture" if code == 401 else "masked_fixture"}],
        }
    return deepcopy(result)
