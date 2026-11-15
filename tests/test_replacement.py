import json,tempfile,unittest
from pathlib import Path
from treasure.ocr import parse,result_lines
from treasure.core import Store

class ReplacementTests(unittest.TestCase):
    def lines(self):
        data=json.loads((Path(__file__).parent/'fixtures/replacement-new-name.json').read_text())
        return result_lines([(v['box'],v['text'],v['score']) for v in data])
    def test_unknown_name_inherits_known_same_slot(self):
        scan=parse(self.lines());self.assertEqual(scan.mode,'compare');self.assertEqual(len(scan.items),2)
        left,right=scan.items
        self.assertEqual(left.name,'玉勾云纹灯');self.assertEqual(left.category,'汉灯');self.assertEqual(right.category,'汉灯')
        self.assertEqual([s.value for s in left.stats],['7.97','6.71','2.62','8.61'])
        self.assertEqual([s.value for s in right.stats],['11.31','11.65','2.29','12.89','1.71'])
        self.assertTrue(left.equipped);self.assertFalse(right.equipped)
        self.assertEqual(scan.diagnostics['columns'][0]['category_source'],'same_replacement_slot')
    def test_both_unknown_remain_pending_without_guessing_type(self):
        scan=parse(self.lines(),lambda _:None)
        # Replace known right name too; no same-slot anchor remains.
        lines=self.lines()
        for line in lines:
            if line.text.startswith('传奇鹿形'):line.text='传奇新藏品'
        scan=parse(lines)
        self.assertTrue(all(not i.category for i in scan.items))
    def test_no_replacement_title_does_not_transfer_category(self):
        scan=parse([l for l in self.lines() if l.text!='替换藏品'])
        self.assertEqual(scan.items[0].category,'')
    def test_conflicting_categories_cannot_be_auto_confirmed(self):
        scan=parse(self.lines(),lambda name:'偶人' if name=='玉勾云纹灯' else None)
        self.assertTrue(all(any('類別不一致' in w for w in i.warnings) for i in scan.items))
    def test_two_previews_persist_once_and_do_not_increase_quantity(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'inventory.sqlite3')
            for _ in range(3):
                for item in parse(self.lines()).items:store.observe(item,raw_text='replacement')
            self.assertEqual(len(store.drafts()),2);self.assertTrue(all(i.quantity==1 for i in store.drafts()))
            for item in store.drafts():store.confirm_visible(item)
            self.assertEqual(len(store.items(confirmed_only=True)),2);self.assertEqual(store.drafts(),[])
            store.close()
    def test_learned_name_supports_future_both_new_comparison(self):
        lines=self.lines()
        for line in lines:
            if line.text.startswith('传奇鹿形'):line.text='传奇另一个新藏品'
        scan=parse(lines,lambda name:'汉灯' if name=='玉勾云纹灯' else None)
        self.assertTrue(all(i.category=='汉灯' for i in scan.items))

if __name__=='__main__':unittest.main()
