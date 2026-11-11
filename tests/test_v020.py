import unittest
import json
import threading
import queue
from pathlib import Path
from treasure.ocr import parse,result_lines,Line
from treasure.capture import overlaps,safe_position,request_frame

class RobustParsingTests(unittest.TestCase):
    def fixture(self):
        p=next((Path(__file__).parent/'fixtures').glob('*061327).json'))
        return result_lines([(r['box'],r['text'],r['score']) for r in json.loads(p.read_text())])
    def test_missing_quality_and_name_still_retains_all_attributes(self):
        lines=[l for l in self.fixture() if not l.text.startswith('传奇')]
        result=parse(lines)
        self.assertEqual(len(result.items),1);item=result.items[0]
        self.assertEqual(item.category,'面具');self.assertEqual(item.quality,'')
        self.assertEqual(len(item.stats),5);self.assertTrue(item.warnings)
    def test_numbers_without_labels_are_not_guessed(self):
        lines=[l for l in self.fixture() if l.text not in ('铁矿产量','骑兵生命值','弓兵防御力','弓兵生命值','[全阵营]士兵生命值')]
        result=parse(lines);self.assertEqual(result.items,[])
        self.assertIn('沒有完整屬性名稱',result.messages[0]);self.assertEqual(result.diagnostics['columns'][0]['numeric_rows'],5)
    def test_partial_labels_keep_paired_attributes_but_warn(self):
        lines=[l for l in self.fixture() if l.text!='铁矿产量'];result=parse(lines)
        self.assertEqual(len(result.items[0].stats),4)
        self.assertTrue(any('缺少對應屬性名稱' in w for w in result.items[0].warnings))
    def test_secondary_fields_can_all_be_missing(self):
        lines=[l for l in self.fixture() if not l.text.startswith('传奇') and l.text!='X1096747']
        result=parse(lines);self.assertEqual(len(result.items[0].stats),5)
        self.assertTrue(result.items[0].warnings)
    def test_self_overlay_never_enters_inventory(self):
        lines=self.fixture()+[Line('即时判断',1,0,0,50,12),Line('其他最高',1,60,0,110,12)]
        result=parse(lines);self.assertEqual(result.mode,'blocked');self.assertFalse(result.items)
        self.assertIn('助手介面遮擋',result.messages[0])
    def test_long_unique_overlay_message_is_enough(self):
        result=parse([Line('只在自己的藏品详情／替换／回收页开启',1,0,0,300,16)])
        self.assertEqual(result.mode,'blocked')
    def test_missing_header_reports_numeric_content(self):
        result=parse([Line('+1.59%',1,0,100,70,115)])
        self.assertIn('讀到數值',result.messages[0])

class CaptureTests(unittest.TestCase):
    def test_safe_dock_avoids_center_panel(self):
        region={'left':778,'top':132,'width':489,'height':865}
        screen={'left':0,'top':0,'width':1920,'height':1080}
        x,y=safe_position(region,490,682,screen)
        self.assertFalse(overlaps(region,{'left':x,'top':y,'width':490,'height':682}))
    def test_fallback_when_no_room(self):
        self.assertIsNone(safe_position({'left':0,'top':0,'width':1000,'height':800},490,682,{'left':0,'top':0,'width':1000,'height':800}))
    def test_edge_touch_is_not_overlap(self):
        self.assertFalse(overlaps({'left':0,'top':0,'width':100,'height':100},{'left':100,'top':0,'width':100,'height':100}))
    def test_capture_ticket_returns_frame(self):
        q=queue.Queue();cancel=threading.Event();result=[]
        worker=threading.Thread(target=lambda:result.append(request_frame(q,{'left':0},cancel)))
        worker.start();event,ticket=q.get(timeout=1);self.assertEqual(event,'capture');ticket.finish('pixels');worker.join(1)
        self.assertEqual(result,['pixels'])
    def test_cancelled_capture_unblocks_worker(self):
        q=queue.Queue();cancel=threading.Event();errors=[]
        def run():
            try:request_frame(q,{},cancel)
            except RuntimeError as e:errors.append(str(e))
        worker=threading.Thread(target=run);worker.start();_,ticket=q.get(timeout=1);cancel.set();worker.join(1)
        self.assertFalse(worker.is_alive());self.assertTrue(ticket.cancelled);self.assertTrue(errors)

if __name__=='__main__':unittest.main()
