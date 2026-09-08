import tempfile
import unittest
from saas.knowledge import SandboxKnowledge, documents


class KnowledgeTest(unittest.TestCase):
    def test_persist_seed_retrieval_and_scope(self):
        # Chroma keeps native file handles until process exit on Windows.
        path = tempfile.mkdtemp(prefix="flowforge-kb-")
        kb = SandboxKnowledge(path)
        self.assertEqual(kb.seed(), len(documents()))
        self.assertEqual(kb.seed(), len(documents()))
        reopened = SandboxKnowledge(path)
        results = reopened.search("401 认证环境", "org_aurora", 10)
        self.assertTrue(any("401" in r["content"] for r in results))
        self.assertTrue(all(r["org_id"] in {"global", "org_aurora"} for r in results))
        self.assertTrue(all(r["version"] != "obsolete" for r in results))
        self.assertTrue(all(r["source_id"] and r["chunk_id"] for r in results))
        foreign = reopened.search("Beacon 历史项目快照", "org_cedar", 10)
        self.assertFalse(any(r["org_id"] == "org_beacon" for r in foreign))
