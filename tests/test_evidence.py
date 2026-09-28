import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import CollationDB, DomainError


class EvidenceFlowTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.db = CollationDB(self.path)
        self.owner = self.db.add_user("负责人", "owner")
        self.editor = self.db.add_user("编辑", "editor")
        self.reviewer = self.db.add_user("审阅", "reviewer")
        self.outsider = self.db.add_user("外部", "reviewer")
        self.work = self.db.create_work("残卷", "异文比较", self.owner)
        self.other_work = self.db.create_work("另一部", "不同作品", self.owner)
        self.w1 = self.db.add_witness(self.work, "甲本", "version")
        self.w2 = self.db.add_witness(self.work, "乙本", "fragment", "馆藏残片", "中段缺页")
        self.w3 = self.db.add_witness(self.work, "丁本", "transcription")
        self.ow = self.db.add_witness(self.other_work, "丙本", "version")
        self.db.grant_witness_editor(self.w2, self.editor, self.owner)
        self.db.grant_work_access(self.work, self.reviewer, "view", self.owner)
        self.passage = self.db.add_passage(self.work, "第一节", "春水东流，故人南去。", self.owner)
        self.opassage = self.db.add_passage(self.other_work, "第一节", "他书文字。", self.owner)
        self.db.align_passage(self.passage, self.w1, "春水东流，故人南去。", 1, self.owner)
        self.db.align_passage(self.passage, self.w2, "春水东流，[缺页]", 2, self.editor)
        self.db.align_passage(self.opassage, self.ow, "他书文字。", 1, self.owner)
        self.variant = self.db.create_variant(self.passage, self.w2, "春水东流，故人南去。", "按语义补足", self.editor, 0)

    def tearDown(self):
        self.db.close(); os.unlink(self.path)

    def _pending(self):
        return self.db.export_collation(self.work, self.owner)["evidence_pending_count"]

    def test_quote_must_match_aligned_text_and_same_work(self):
        with self.assertRaisesRegex(DomainError, "同属一部作品"):
            self.db.add_evidence(self.variant, self.opassage, self.ow, "他书", self.editor)
        with self.assertRaisesRegex(DomainError, "逐字找到"):
            self.db.add_evidence(self.variant, self.passage, self.w1, "逐字没有的文字", self.editor)
        with self.assertRaisesRegex(DomainError, "尚未对齐"):
            self.db.add_evidence(self.variant, self.passage, self.w3, "春水", self.editor)
        with self.assertRaisesRegex(DomainError, "无权"):
            self.db.add_evidence(self.variant, self.passage, self.w1, "春水东流", self.outsider)
        eid = self.db.add_evidence(self.variant, self.passage, self.w1, "故人南去", self.editor)
        self.assertEqual(1, self._pending())
        row = self.db.conn.execute("SELECT * FROM variant_evidence WHERE id=?", (eid,)).fetchone()
        self.assertEqual("pending", row["status"])

    def test_viewer_can_verify_but_submitter_cannot_self_review(self):
        eid = self.db.add_evidence(self.variant, self.passage, self.w1, "故人南去", self.editor)
        with self.assertRaisesRegex(DomainError, "自审"):
            self.db.verify_evidence(eid, self.editor)
        with self.assertRaisesRegex(DomainError, "无权"):
            self.db.verify_evidence(eid, self.outsider)
        self.db.verify_evidence(eid, self.reviewer)
        with self.assertRaisesRegex(DomainError, "已核对"):
            self.db.verify_evidence(eid, self.owner)
        exported = self.db.export_collation(self.work, self.reviewer)
        ev = exported["passages"][0]["variants"][0]["evidence"][0]
        self.assertEqual("verified", ev["status"])
        self.assertEqual("审阅", ev["verified_by_name"])
        self.assertEqual("编辑", ev["created_by_name"])
        self.assertEqual(0, exported["evidence_pending_count"])
        self.assertEqual("甲本", ev["siglum"])
        self.assertEqual("第一节", ev["passage_label"])

    def test_new_layer_invalidates_evidence_and_pending_blocks_lock(self):
        eid = self.db.add_evidence(self.variant, self.passage, self.w1, "故人南去", self.editor)
        self.db.verify_evidence(eid, self.reviewer)
        self.db.lock_passage(self.passage, self.owner, "定稿")
        # 解锁以模拟定稿前又出新层的情况
        with self.db.transaction():
            self.db.conn.execute("DELETE FROM passage_locks WHERE passage_id=?", (self.passage,))
            self.db.conn.execute("UPDATE passages SET status='open' WHERE id=?", (self.passage,))
        self.db.update_variant(self.variant, "春水东流，[不可辨]人南去。", "墨迹受损，改作缺字", self.editor, 1)
        self.assertEqual(1, self._pending())
        row = self.db.conn.execute("SELECT * FROM variant_evidence WHERE id=?", (eid,)).fetchone()
        self.assertEqual("pending", row["status"])
        self.assertIsNone(row["verified_by"])
        with self.assertRaisesRegex(DomainError, "旁证未核对"):
            self.db.lock_passage(self.passage, self.owner, "再次定稿")
        self.db.verify_evidence(eid, self.reviewer)
        self.db.lock_passage(self.passage, self.owner, "再次定稿")

    def test_pending_evidence_blocks_lock_even_without_revision(self):
        self.db.add_evidence(self.variant, self.passage, self.w1, "春水东流", self.reviewer)
        with self.assertRaisesRegex(DomainError, "1 条旁证未核对"):
            self.db.lock_passage(self.passage, self.owner, "定稿")
        # 无旁证负担的段落可正常锁定
        clean = self.db.add_passage(self.work, "第二节", "风清月白。", self.owner)
        self.db.lock_passage(clean, self.owner, "定稿")

    def test_snapshot_carries_evidence_state(self):
        eid = self.db.add_evidence(self.variant, self.passage, self.w1, "故人南去", self.editor)
        self.db.verify_evidence(eid, self.reviewer)
        self.db.update_variant(self.variant, "春水东流，[不可辨]人南去。", "改作缺字", self.editor, 1)
        snap = self.db.get_snapshot(self.passage, 2, self.owner)
        state = snap["snapshot"]["evidence"][0]
        self.assertEqual("pending", state["status"])
        self.assertIsNone(state["verified_by"])
        self.assertEqual("故人南去", state["quote"])


if __name__ == "__main__":
    unittest.main()
