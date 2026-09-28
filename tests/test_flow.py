import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import CollationDB, DomainError

class CollationFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=CollationDB(self.path)
        self.owner=self.db.add_user("负责人","owner"); self.editor=self.db.add_user("编辑","editor"); self.reviewer=self.db.add_user("审阅","reviewer"); self.outsider=self.db.add_user("外部","reviewer")
        self.work=self.db.create_work("残卷","异文比较",self.owner)
        self.w1=self.db.add_witness(self.work,"甲本","version"); self.w2=self.db.add_witness(self.work,"乙本","fragment","馆藏残片","中段缺页")
        self.db.grant_witness_editor(self.w2,self.editor,self.owner); self.db.grant_work_access(self.work,self.reviewer,"view",self.owner)
        self.passage=self.db.add_passage(self.work,"第一节","春水东流，故人南去。",self.owner)
        self.db.align_passage(self.passage,self.w1,"春水东流，故人南去。",1,self.owner)
        self.db.align_passage(self.passage,self.w2,"春水东流，[缺页]",2,self.editor)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def test_multilayer_revision_snapshot_export_and_lock(self):
        variant=self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按语义补足",self.editor,0)
        rev=self.db.update_variant(variant,"春水东流，[不可辨]人南去。","墨迹受损，不再直接补写",self.editor,1)
        self.assertEqual(2,rev)
        snap=self.db.get_snapshot(self.passage,2,self.owner)
        self.assertEqual(2,snap["layer"])
        exported=self.db.export_collation(self.work,self.reviewer)
        self.assertEqual(1,exported["gap_count"])
        self.assertTrue(exported["passages"][0]["variants"][0]["notes"] == [])
        self.db.lock_passage(self.passage,self.owner,"定稿")
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.update_variant(variant,"另一文本","无意义修改",self.editor,2)
    def test_optimistic_lock_permission_and_mark_validation(self):
        first=self.db.create_variant(self.passage,self.w2,"补足一","理由一",self.editor,0)
        with self.assertRaisesRegex(DomainError,"版本冲突"):
            self.db.create_variant(self.passage,self.w2,"补足二","理由二",self.editor,0)
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.create_variant(self.passage,self.w2,"补足三","理由三",self.reviewer,1)
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.export_collation(self.work,self.outsider)
        with self.assertRaisesRegex(DomainError,"括号"):
            self.db.align_passage(self.passage,self.w1,"文本[未闭合",9,self.owner)
    def test_evidence_register_verify_export(self):
        variant=self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按甲本补足",self.editor,0)
        ev=self.db.add_evidence(variant,self.passage,self.w1,"春水东流，故人南去。",self.editor)
        exported=self.db.export_collation(self.work,self.reviewer)
        item=exported["passages"][0]["variants"][0]
        self.assertEqual(1,item["pending_evidence_count"])
        self.assertEqual(1,exported["pending_evidence_count"])
        self.assertIsNone(item["evidences"][0]["verified_by_name"])
        # 提交者不能自审
        with self.assertRaisesRegex(DomainError,"不能核对"):
            self.db.verify_evidence(ev,self.editor)
        # 无查看权限的外部用户不能核对
        with self.assertRaisesRegex(DomainError,"无权核对"):
            self.db.verify_evidence(ev,self.outsider)
        self.db.verify_evidence(ev,self.reviewer)
        exported=self.db.export_collation(self.work,self.reviewer)
        evrow=exported["passages"][0]["variants"][0]["evidences"][0]
        self.assertEqual("verified",evrow["status"])
        self.assertEqual("审阅",evrow["verified_by_name"])
        self.assertEqual("编辑",evrow["submitted_by_name"])
        self.assertEqual(0,exported["pending_evidence_count"])
    def test_evidence_quotation_must_be_verbatim_in_same_work(self):
        variant=self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按甲本补足",self.editor,0)
        with self.assertRaisesRegex(DomainError,"逐字"):
            self.db.add_evidence(variant,self.passage,self.w1,"春水流，故人去。",self.editor)
        w3=self.db.add_witness(self.work,"丙本","transcription")
        with self.assertRaisesRegex(DomainError,"尚未对齐"):
            self.db.add_evidence(variant,self.passage,w3,"春水",self.editor)
        with self.assertRaisesRegex(DomainError,"无权登记"):
            self.db.add_evidence(variant,self.passage,self.w1,"春水东流，故人南去。",self.outsider)
        # 另一部作品的版本与段落不能作为旁证出处
        work2=self.db.create_work("他书","另一作品",self.owner)
        w3=self.db.add_witness(work2,"丙本","version")
        p2=self.db.add_passage(work2,"第一节","春水东流，故人南去。",self.owner)
        self.db.align_passage(p2,w3,"春水东流，故人南去。",1,self.owner)
        with self.assertRaisesRegex(DomainError,"同属一部作品"):
            self.db.add_evidence(variant,p2,w3,"春水东流，故人南去。",self.editor)
        self.db.grant_work_access(self.work,self.outsider,"view",self.owner)
        with self.assertRaisesRegex(DomainError,"同属一部作品"):
            self.db.add_evidence(variant,p2,w3,"春水东流，故人南去。",self.outsider)
    def test_new_layer_invalidates_evidence_and_blocks_lock(self):
        variant=self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按甲本补足",self.editor,0)
        ev=self.db.add_evidence(variant,self.passage,self.w1,"春水东流，故人南去。",self.editor)
        self.db.verify_evidence(ev,self.reviewer)
        # 异文改出新层，旁证失效，锁定被挡
        self.db.update_variant(variant,"春水东流，[不可辨]人南去。","重新考虑墨痕",self.editor,1)
        with self.assertRaisesRegex(DomainError,"旁证待核"):
            self.db.lock_passage(self.passage,self.owner,"定稿")
        # 重新核对后才可以锁定
        self.db.verify_evidence(ev,self.reviewer)
        self.db.lock_passage(self.passage,self.owner,"定稿")
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.add_evidence(variant,self.passage,self.w1,"春水",self.editor)
    def test_variant_without_evidence_does_not_block_lock(self):
        self.db.create_variant(self.passage,self.w2,"春水东流，故人南去。","按义补足",self.editor,0)
        self.db.lock_passage(self.passage,self.owner,"定稿")

if __name__=="__main__": unittest.main()
