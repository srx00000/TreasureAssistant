import tempfile
import unittest
from pathlib import Path
import sqlite3
from treasure.core import Store,Item,Stat,Advisor

class QuickConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'inventory.sqlite3')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def gear(self,value='2'):
        return Item('偶人','测试藏品','史诗','460249',[Stat('木材采集速度',value)],confidence=.7,warnings=['部分文字不清楚'],complete=False,source='detail')
    def test_explicit_confirmation_promotes_displayed_draft_without_mutating_scan(self):
        item=self.gear();item.draft_id=self.store.stage(item)
        iid=self.store.confirm_visible(item)
        self.assertEqual(self.store.drafts(),[])
        actual=self.store.items()[0];self.assertTrue(actual.confirmed);self.assertEqual(actual.id,iid)
        self.assertFalse(item.complete);self.assertFalse(item.confirmed);self.assertTrue(item.warnings)
        self.assertEqual(Advisor(self.store.items(),self.store.definitions()).plan('木材采集速度','%')['偶人'][0],'2')
    def test_reread_and_repeated_confirmation_preserve_count_and_id(self):
        original=self.gear();original.complete=True;original.quantity=5
        iid=self.store.save(original,confirmed=True)[0]
        scan=self.gear()
        for _ in range(3):self.assertEqual(self.store.confirm_visible(scan),iid)
        self.assertEqual(len(self.store.items()),1);self.assertEqual(self.store.items()[0].quantity,5)
        self.store.observe(self.gear(),confirmed=False)
        self.assertTrue(self.store.items()[0].confirmed);self.assertEqual(self.store.drafts(),[])
    def test_new_rules_default_higher_existing_lower_rule_is_preserved(self):
        self.store.define('测试消耗','%',-1)
        item=self.gear();item.stats=[Stat('测试加成','3'),Stat('测试消耗','4')]
        self.store.confirm_visible(item)
        self.assertEqual(self.store.definitions()[('测试加成','%')],1)
        self.assertEqual(self.store.definitions()[('测试消耗','%')],-1)
    def test_missing_or_duplicate_required_data_rejected_without_writes(self):
        for category,stats in [('',[Stat('新属性','2')]),('偶人',[]),('偶人',[Stat('新属性','2'),Stat('新属性','3')])]:
            with self.subTest(category=category,stats=stats):
                with self.assertRaises(ValueError):self.store.confirm_visible(Item(category,'','','',stats))
        self.assertEqual(self.store.items(),[]);self.assertNotIn(('新属性','%'),self.store.definitions())
    def test_changed_draft_is_not_confirmed_using_stale_display(self):
        snapshot=self.gear();snapshot.draft_id=self.store.stage(snapshot)
        self.store.stage(self.gear('3'),update_id=snapshot.draft_id)
        with self.assertRaises(ValueError):self.store.confirm_visible(snapshot)
        self.assertEqual(self.store.items(),[]);self.assertEqual(self.store.drafts()[0].stats[0].value,'3')
    def test_deleted_snapshot_does_not_recreate_a_removed_record(self):
        snapshot=self.gear();snapshot.draft_id=self.store.stage(snapshot)
        self.store.remove_records(draft_ids=[snapshot.draft_id])
        with self.assertRaises(ValueError):self.store.confirm_visible(snapshot)
        item=self.gear();item.complete=True;iid=self.store.save(item,confirmed=True)[0]
        snapshot=self.store.items()[0];self.store.remove_records([iid])
        with self.assertRaises(ValueError):self.store.confirm_visible(snapshot)
        self.assertEqual(self.store.items(),[])
    def test_auto_promoted_draft_can_still_be_confirmed(self):
        snapshot=self.gear();snapshot.draft_id=self.store.stage(snapshot)
        complete=self.gear();complete.complete=True;complete.warnings=[]
        _,iid=self.store.observe(complete,confirmed=True)
        self.assertEqual(self.store.confirm_visible(snapshot),iid)
        self.assertEqual(len(self.store.items()),1);self.assertEqual(self.store.drafts(),[])
    def test_manual_edit_draft_updates_original_and_retains_reviewed_quantity(self):
        item=self.gear();item.complete=True;iid=self.store.save(item,confirmed=True)[0]
        edit=self.gear('3');edit.id=iid;edit.quantity=4;edit.source='manual-draft'
        edit.draft_id=self.store.stage(edit)
        self.assertEqual(self.store.confirm_visible(edit),iid)
        self.assertEqual(len(self.store.items()),1);self.assertEqual(self.store.items()[0].quantity,4)
        self.assertEqual(self.store.items()[0].stats[0].value,'3');self.assertEqual(self.store.drafts(),[])
    def test_failed_draft_promotion_rolls_back_item_and_rule_changes(self):
        item=self.gear();item.stats=[Stat('未知属性','3')];item.draft_id=self.store.stage(item)
        self.store.conn.execute("CREATE TRIGGER reject_quick_delete BEFORE DELETE ON drafts BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.confirm_visible(item)
        self.assertEqual(self.store.items(),[]);self.assertEqual(len(self.store.drafts()),1)
        self.assertEqual(self.store.definitions()[('未知属性','%')],0)

if __name__=='__main__':unittest.main()
