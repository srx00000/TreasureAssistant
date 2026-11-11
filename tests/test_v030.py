import json
import tempfile
import unittest
from pathlib import Path
from treasure.core import Store,Item,Stat
from treasure.ocr import parse,result_lines,Line

class WarehouseRegressionTests(unittest.TestCase):
    def lines(self):
        data=json.loads((Path(__file__).parent/'fixtures/warehouse-upright.json').read_text())
        return result_lines([(v['box'],v['text'],v['score']) for v in data])
    def test_all_four_warehouse_attributes_are_separate(self):
        scan=parse(self.lines());self.assertEqual(scan.mode,'detail');item=scan.items[0]
        self.assertEqual([(s.name,s.value) for s in item.stats],[('[全阵营]士兵破坏力','2.74'),('骑兵攻击力','9.88'),('[全阵营]士兵防御力','2.91'),('食物产量','9.99')])
        self.assertTrue(item.complete)
    def test_footer_and_background_icons_never_become_attributes(self):
        scan=parse(self.lines());self.assertEqual(len(scan.items[0].stats),4)
        self.assertFalse(any('展示' in a.name or a.value=='0' for a in scan.items[0].stats))
        self.assertEqual(scan.diagnostics['columns'][0]['unpaired_values'],[])
    def test_missing_middle_number_does_not_merge_neighbors(self):
        scan=parse([l for l in self.lines() if l.text!='+9.88%'])
        self.assertEqual([a.name for a in scan.items[0].stats],['[全阵营]士兵破坏力','[全阵营]士兵防御力','食物产量'])
        self.assertFalse(scan.items[0].complete)
        self.assertIn('骑兵攻击力',scan.diagnostics['columns'][0]['unpaired_labels'])
    def test_upside_down_numeric_text_does_not_join_food_to_defense(self):
        lines=self.lines()
        for l in lines:
            if l.text=='+9.99%':l.text="%66'6+"
        item=parse(lines).items[0]
        self.assertEqual(len(item.stats),3);self.assertEqual(item.stats[-1].name,'[全阵营]士兵防御力');self.assertFalse(item.complete)
        self.assertTrue(any('食物产量' in w for w in item.warnings))
    def test_low_confidence_full_read_is_persisted_as_one_draft(self):
        scan=parse(self.lines())
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'test.sqlite3')
            for _ in range(5):store.observe(scan.items[0],confirmed=False,raw_text=scan.text)
            self.assertEqual(len(store.drafts()),1);self.assertEqual(len(store.drafts()[0].stats),4);self.assertEqual(store.items(),[])
            store.close()
    def test_manual_draft_quantity_not_discarded_by_auto_observation(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'test.sqlite3')
            draft=Item('偶人','a','史诗','460249',[Stat('木材采集速度','3')],quantity=3,source='manual-draft',complete=False)
            did=store.stage(draft)
            observed=Item('偶人','a','史诗','460249',[Stat('木材采集速度','3')])
            store.observe(observed,confirmed=True)
            self.assertEqual(len(store.drafts()),1)
            reviewed=store.drafts()[0];reviewed.complete=True
            store.resolve_draft(did,reviewed)
            self.assertEqual(store.items()[0].quantity,3);self.assertEqual(len(store.items()),1);self.assertEqual(store.drafts(),[])
            store.close()

if __name__=='__main__':unittest.main()
