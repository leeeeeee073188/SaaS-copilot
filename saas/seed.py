"""Run: python -m saas.seed --output data/flowforge"""
import argparse
import json
from pathlib import Path
from saas.fixtures import NOW, POLICY_VERSION, scenarios
from saas.knowledge import SandboxKnowledge, documents
from saas.service import SaaSService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/flowforge")
    parser.add_argument("--embedding", choices=["lexical", "minilm"], default="lexical")
    args = parser.parse_args()
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=True)
    SaaSService(path / "flowforge.db")
    kb = SandboxKnowledge(path / "chroma", embedding=args.embedding)
    count = kb.seed()
    (path / "documents.json").write_text(json.dumps(documents(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"version": POLICY_VERSION, "clock": NOW, "organizations": list(scenarios()), "chunks": count,
              "chroma_path": str((path / "chroma").resolve()), "collection": kb.collection.name,
              "embedding": args.embedding, "retrieval": kb.search("401 认证环境", "org_aurora", 3)}
    (path / "seed-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "retrieval"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
