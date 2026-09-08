"""Conservative routing; recognizing a request never grants permission."""
import re


def classify_business(message, history=None):
    text = message.lower()
    groups = {
        "integration": ("api", "webhook", "401", "403", "429", "接口", "限流", "认证", "集成"),
        "billing": ("账单", "订阅", "套餐", "升级", "降级", "growth", "starter", "invoice", "subscription"),
        "account": ("成员", "邀请", "席位", "invite", "member", "管理员"),
    }
    domains = [domain for domain, words in groups.items() if any(word in text for word in words)]
    change = bool(re.search(r"(请|帮我|我要|给我|下周期|下个月).*(升级|降级|改为|邀请)|^(邀请|invite|升级|降级)", text))
    question = any(word in text for word in ("怎么", "如何", "是否", "能否", "多少钱", "介绍", "支持吗", "how", "can i"))
    negated = any(word in text for word in ("不要", "不需要", "暂不", "别邀请", "do not", "don't"))
    action = "change" if change and not question and not negated else "read"
    if any(word in text for word in ("预览", "报价", "preview")) and not negated:
        action = "preview"
    if not domains and history and any(word in text for word in ("那", "它", "现在", "还是", "继续")):
        prior = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        domains = classify_business(prior)["domains"]
    domains = domains or ["product"]
    primary = domains[0]
    if action in {"change", "preview"}:
        primary = "account" if "account" in domains and "邀请" in text else "billing" if "billing" in domains else primary
    return {"domain": primary, "domains": domains, "action": action}
