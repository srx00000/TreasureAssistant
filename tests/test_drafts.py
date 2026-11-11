import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from treasure.core import Store,Advisor,Item,Stat

class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'inventory.sqlite3';self.store=Store(self.path)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def gear(self,value='2',complete=False,extra=False):
        stats=[Stat('木材采集速度',value)]
        if extra:stats.append(Stat('建造速度','1.5'))
        return Item('偶人','测试藏品','史诗','460249',stats,complete=complete,warnings=[] if complete else ['屬性未讀全'])
    def test_partial_survives_restart_with_warnings_and_raw_text(self):
        did=self.store.stage(self.gear(),'unparsed OCR text')
        self.store.close();self.store=Store(self.path)
        d=self.store.drafts()[0];self.assertEqual(d.draft_id,did);self.assertFalse(d.complete);self.assertTrue(d.warnings)
        self.assertEqual(self.store.draft_text(did),'unparsed OCR text');self.assertEqual(self.store.items(),[])
    def test_repeated_ocr_does_not_grow_drafts_or_quantities(self):
        for _ in range(20):self.store.observe(self.gear(),raw_text='same frame')
        self.assertEqual(len(self.store.drafts()),1);self.assertEqual(self.store.drafts()[0].quantity,1)
    def test_automatic_staging_preserves_manual_draft(self):
        item=self.gear();item.source='manual-draft';item.quantity=3
        did=self.store.stage(item,'manual text')
        self.store.observe(self.gear(complete=True,extra=True),raw_text='new scan')
        draft=self.store.drafts()[0]
        self.assertEqual(draft.draft_id,did);self.assertEqual(draft.source,'manual-draft')
        self.assertEqual(draft.quantity,3);self.assertEqual(len(draft.stats),1)
        self.assertEqual(self.store.draft_text(did),'manual text')
    def test_v020_database_upgrade_preserves_inventory_and_settings(self):
        iid=self.store.save(self.gear(complete=True),confirmed=True)[0]
        self.store.setting('region',{'left':100});self.store.define('自定义','%',-1)
        self.store.conn.execute('DROP TABLE drafts');self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.items()[0].id,iid);self.assertTrue(self.store.items()[0].confirmed)
        self.assertEqual(self.store.get_setting('region'),{'left':100})
        self.assertEqual(self.store.definitions()[('自定义','%')],-1)
        self.store.stage(self.gear('3'));self.assertEqual(len(self.store.drafts()),1)
    def test_more_complete_compatible_read_updates_same_draft(self):
        a=self.store.stage(self.gear());b=self.store.stage(self.gear(extra=True))
        self.assertEqual(a,b);self.assertEqual(len(self.store.drafts()[0].stats),2)
        self.store.stage(self.gear());self.assertEqual(len(self.store.drafts()[0].stats),2)
    def test_different_values_are_not_merged(self):
        self.store.stage(self.gear('2'));self.store.stage(self.gear('3'))
        self.assertEqual(len(self.store.drafts()),2)
    def test_empty_attributes_preserve_identified_item(self):
        d=Item('偶人','a','史诗','460249',[],complete=False)
        self.store.stage(d);self.assertEqual(len(self.store.drafts()),1)
        did=self.store.stage(self.gear());self.assertEqual(len(self.store.drafts()),1);self.assertEqual(did,self.store.drafts()[0].draft_id)
    def test_unknown_category_can_be_preserved(self):
        item=self.gear();item.category='';self.store.stage(item)
        self.assertEqual(self.store.drafts()[0].category,'')
    def test_drafts_do_not_participate_in_plans(self):
        self.store.stage(self.gear('100'))
        self.store.save(self.gear('2',True),confirmed=True)
        plan=Advisor(self.store.items(),self.store.definitions()).plan('木材采集速度','%')
        self.assertEqual(plan['偶人'][0],'2')
    def test_promotion_removes_draft_and_creates_confirmed_record(self):
        did=self.store.stage(self.gear());item=self.gear(complete=True)
        result=self.store.resolve_draft(did,item)
        self.assertEqual(self.store.drafts(),[]);self.assertTrue(self.store.items()[0].confirmed);self.assertEqual(self.store.items()[0].id,result)
    def test_failed_promotion_preserves_draft(self):
        did=self.store.stage(self.gear())
        with self.assertRaises(ValueError):self.store.resolve_draft(did,self.gear())
        self.assertEqual(len(self.store.drafts()),1);self.assertEqual(self.store.items(),[])
    def test_promotion_is_atomic_if_draft_delete_fails(self):
        did=self.store.stage(self.gear())
        self.store.conn.execute("CREATE TRIGGER reject_draft_delete BEFORE DELETE ON drafts BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.resolve_draft(did,self.gear(complete=True))
        self.assertEqual(len(self.store.drafts()),1);self.assertEqual(self.store.items(),[])
    def test_auto_confirmation_consumes_matching_draft(self):
        did=self.store.stage(self.gear())
        kind,item_id=self.store.observe(self.gear(complete=True),confirmed=True)
        self.assertEqual(kind,'item');self.assertEqual(self.store.drafts(),[])
        self.assertEqual(self.store.items()[0].id,item_id)
    def test_known_record_not_downgraded_by_new_uncertainty(self):
        self.store.save(self.gear(complete=True),confirmed=True)
        self.store.observe(self.gear())
        self.assertTrue(self.store.items()[0].confirmed);self.assertEqual(self.store.drafts(),[])
    def test_explicit_draft_edit_keeps_id_even_when_values_change(self):
        did=self.store.stage(self.gear());updated=self.store.stage(self.gear('3'),update_id=did)
        self.assertEqual(did,updated);self.assertEqual(len(self.store.drafts()),1);self.assertEqual(self.store.drafts()[0].stats[0].value,'3')
    def test_pending_manual_edit_preserves_original_until_confirmed(self):
        iid=self.store.save(self.gear(complete=True),confirmed=True)[0]
        draft=self.gear('3');draft.id=iid;did=self.store.stage(draft)
        self.assertEqual(self.store.items()[0].stats[0].value,'2')
        reviewed=self.store.drafts()[0];reviewed.complete=True
        self.store.resolve_draft(did,reviewed)
        self.assertEqual(len(self.store.items()),1);self.assertEqual(self.store.items()[0].id,iid);self.assertEqual(self.store.items()[0].stats[0].value,'3')
    def test_manual_delete_mixed_records_and_recalculate(self):
        low=self.store.save(self.gear('1',True),confirmed=True)[0]
        high=self.store.save(self.gear('3',True),confirmed=True)[0];did=self.store.stage(self.gear('5'))
        self.store.remove_records([high],[did]);self.assertEqual(self.store.drafts(),[])
        plan=Advisor(self.store.items(),self.store.definitions()).plan('木材采集速度','%')
        self.assertEqual(plan['偶人'][0],'1');self.assertEqual(plan['偶人'][1][0].id,low)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM stats WHERE item_id=?',(high,)).fetchone()[0],0)
    def test_manual_delete_is_atomic_on_invalid_id(self):
        iid=self.store.save(self.gear(complete=True),confirmed=True)[0]
        with self.assertRaises(ValueError):self.store.remove_records([iid],[999])
        self.assertEqual(len(self.store.items()),1)
    def test_clearing_preserves_settings_and_rules(self):
        self.store.setting('language','zh-CN');self.store.define('新属性','%',-1)
        self.store.stage(self.gear());self.store.save(self.gear(complete=True),confirmed=True)
        self.store.clear();self.assertEqual(self.store.items(),[]);self.assertEqual(self.store.drafts(),[])
        self.assertEqual(self.store.get_setting('language'),'zh-CN');self.assertEqual(self.store.definitions()[('新属性','%')],-1)
    def test_reset_restores_defaults_and_clears_settings(self):
        self.store.setting('region',{'left':3});self.store.define('新属性','%',-1);self.store.stage(self.gear())
        self.store.clear(reset_rules=True,reset_settings=True)
        self.assertIsNone(self.store.get_setting('region'));self.assertNotIn(('新属性','%'),self.store.definitions());self.assertEqual(self.store.drafts(),[])
        self.assertEqual(self.store.definitions()[('木材采集速度','%')],1)
    def test_full_clear_and_reset_restart_both_sequences_after_reopen(self):
        for reset in (False,True):
            with self.subTest(reset=reset):
                for value in ('1','2','3'):
                    self.store.save(self.gear(value,True),confirmed=True);self.store.stage(self.gear(value))
                self.store.clear(reset_rules=reset,reset_settings=reset)
                self.store.close();self.store=Store(self.path)
                self.assertEqual(self.store.items(),[]);self.assertEqual(self.store.drafts(),[])
                self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM stats').fetchone()[0],0)
                self.assertEqual(self.store.save(self.gear('4',True),confirmed=True)[0],1)
                self.assertEqual(self.store.stage(self.gear('4')),1)
    def test_ordinary_deletion_does_not_reuse_item_or_draft_ids(self):
        iid=self.store.save(self.gear('1',True),confirmed=True)[0];did=self.store.stage(self.gear('1'))
        self.store.remove_records([iid],[did])
        next_item=self.store.save(self.gear('2',True),confirmed=True)[0];next_draft=self.store.stage(self.gear('2'))
        self.assertGreater(next_item,iid);self.assertGreater(next_draft,did)
        self.store.delete({next_item:1})
        self.assertGreater(self.store.save(self.gear('3',True),confirmed=True)[0],next_item)
    def test_failed_reset_rolls_back_records_and_sequences(self):
        iid=self.store.save(self.gear('1',True),confirmed=True)[0];did=self.store.stage(self.gear('1'))
        self.store.conn.execute("CREATE TRIGGER reject_rule_delete BEFORE DELETE ON attributes BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.clear(reset_rules=True,reset_settings=True)
        self.assertEqual(self.store.items()[0].id,iid);self.assertEqual(self.store.drafts()[0].draft_id,did)
        self.assertGreater(self.store.save(self.gear('2',True),confirmed=True)[0],iid)
        self.assertGreater(self.store.stage(self.gear('2')),did)
    def test_deleted_original_detaches_existing_edit_draft(self):
        iid=self.store.save(self.gear(complete=True),confirmed=True)[0]
        d=self.gear('3');d.id=iid;did=self.store.stage(d)
        self.store.remove_records([iid]);self.assertIsNone(self.store.drafts()[0].id)
        d=self.store.drafts()[0];d.complete=True;self.store.resolve_draft(did,d)
        self.assertEqual(len(self.store.items()),1)
    def test_raw_ocr_text_is_bounded_and_no_images_are_saved(self):
        did=self.store.stage(self.gear(),'x'*50000)
        self.assertEqual(len(self.store.draft_text(did)),12000)
        self.assertEqual(list(Path(self.temp.name).glob('*.png')),[])

if __name__=='__main__':unittest.main()
