import json
import unittest
from pathlib import Path
from treasure.ocr import parse,result_lines

class ScreenshotParserTests(unittest.TestCase):
    def scan(self,suffix):
        path=next((Path(__file__).parent/'fixtures').glob('*'+suffix+').json'))
        data=json.loads(path.read_text(encoding='utf-8'))
        return parse(result_lines([(d['box'],d['text'],d['score']) for d in data]))
    def test_comparison_columns_and_multiline(self):
        s=self.scan('061238');self.assertEqual(s.mode,'compare');self.assertEqual(len(s.items),2)
        self.assertEqual([len(i.stats) for i in s.items],[5,5])
        self.assertEqual(s.items[0].stats[1].name,'降低太学院开发消耗')
        self.assertEqual(s.items[1].stats[0].value,'16.82')
        self.assertTrue(s.items[0].equipped);self.assertFalse(s.items[1].equipped)
        self.assertFalse(s.items[0].warnings)
    def test_low_confidence_never_silent(self):
        s=self.scan('061259');self.assertEqual(len(s.items),2)
        self.assertTrue(all(i.warnings for i in s.items))
        self.assertEqual(s.items[1].stats[0].unit,'')
        self.assertEqual(s.items[1].stats[0].value,'15')
    def test_overview_does_not_invent_items(self):self.assertEqual(self.scan('061311').items,[])
    def test_detail_equipped_and_scoped_attribute(self):
        s=self.scan('061327');self.assertEqual(s.items[0].category,'面具');self.assertTrue(s.items[0].equipped)
        self.assertEqual(s.items[0].stats[-1].name,'[全阵营]士兵生命值')
    def test_fixed_capacity(self):
        s=self.scan('061338');self.assertEqual(s.items[0].category,'松石')
        self.assertEqual([(v.value,v.unit) for v in s.items[0].stats[:2]],[('15',''),('2.03','%')])
    def test_recycle_only_preview(self):
        s=self.scan('065029');self.assertEqual(s.mode,'recycle');self.assertEqual(len(s.items),1)
        self.assertEqual(s.items[0].power,'146625');self.assertEqual(s.items[0].stats[0].value,'1.59')
        self.assertEqual(s.items[0].stats[0].name,'[全阵营]士兵攻击力')
        self.assertEqual(s.items[0].stats[-1].name,'降低太学院开发消耗')

if __name__=='__main__':unittest.main()
