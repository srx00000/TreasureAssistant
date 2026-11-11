import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from treasure.core import Store, Advisor, Item, Stat, norm

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'inventory.sqlite3')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def add(self,category='偶人',value='1',name='木材采集速度',unit='%',confirmed=True,quantity=1):
        item=Item(category,'同名藏品','史诗','12345',[Stat(name,value,unit)],quantity=quantity)
        return self.store.save(item,confirmed=confirmed)[0]
    def advisor(self):return Advisor(self.store.items(),self.store.definitions())
    def test_decimal_quality_independent(self):
        low=self.add(value='16.26');high=self.add(value='16.82')
        plan=self.advisor().plan('木材采集速度','%')
        self.assertEqual(plan['偶人'][0],'16.82');self.assertEqual(plan['偶人'][1][0].id,high)
        self.assertEqual(Decimal(plan['偶人'][0])-Decimal('16.26'),Decimal('0.56'))
    def test_reobserve_does_not_increment_or_downgrade(self):
        i=self.add(quantity=3);j=self.add(confirmed=False)
        self.assertEqual(i,j);self.assertEqual(len(self.store.items()),1)
        self.assertEqual(self.store.items()[0].quantity,3);self.assertTrue(self.store.items()[0].confirmed)
    def test_traditional_simplified_same_identity(self):
        i=self.add(category='偶人',name='木材採集速度');j=self.add(name='木材采集速度')
        self.assertEqual(i,j);self.assertEqual(norm('【全陣營】士兵攻擊力'),'[全阵营]士兵攻击力')
    def test_current_item_not_compared_with_itself(self):
        i=self.add();item=self.store.items()[0]
        self.assertEqual(self.advisor().verdict(item),'keep')
    def test_per_attribute_multi_item_substitution(self):
        target=Item('金器','a','稀有','1000',[Stat('木材采集速度','2'),Stat('建造速度','1')])
        tid=self.store.save(target,confirmed=True)[0]
        self.add('金器','3');self.add('金器','2','建造速度')
        item=next(i for i in self.store.items() if i.id==tid)
        self.assertEqual(self.advisor().verdict(item),'candidate')
    def test_batch_cannot_remove_both_tied_best(self):
        i=self.add(value='5');j=self.store.save(Item('偶人','different','传奇','9999',[Stat('木材采集速度','5')]),confirmed=True)[0]
        self.assertFalse(self.advisor().recycle_risks({i:1})[i])
        risks=self.advisor().recycle_risks({i:1,j:1})
        self.assertIn('木材采集速度',risks[i]);self.assertIn('木材采集速度',risks[j])
    def test_delete_recomputes_and_cascades(self):
        a=self.add(value='2');b=self.add(value='4');self.store.delete({b:1})
        self.assertEqual(self.advisor().plan('木材采集速度','%')['偶人'][1][0].id,a)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM stats WHERE item_id=?',(b,)).fetchone()[0],0)
        c=self.add(value='6');self.assertGreater(c,b)
    def test_delete_is_atomic_and_quantities_retained(self):
        a=self.add(quantity=3);b=self.add(value='2')
        with self.assertRaises(ValueError):self.store.delete({a:2,b:5})
        self.assertEqual(self.store.items()[0].quantity,3)
        self.store.delete({a:2});self.assertEqual(self.store.items()[0].quantity,1)
    def test_unknown_new_category_and_attribute_staged(self):
        i=self.add('新类别','5','新效果',confirmed=True)
        self.assertIn('新类别',self.store.categories());self.assertEqual(self.advisor().verdict(self.store.items()[0]),'review')
        self.store.define('新效果','%',1)
        self.assertEqual(self.advisor().verdict(self.store.items()[0]),'keep')
    def test_pending_item_cannot_be_substitute(self):
        a=self.add(value='1');self.add(value='100',confirmed=False)
        self.assertEqual(self.advisor().verdict(self.store.items()[0]),'keep')
    def test_fixed_units_not_mixed_with_percentage(self):
        self.add(name='兵营训练容量',value='15',unit='')
        self.add(name='兵营训练容量',value='99',unit='%')
        self.assertEqual(self.advisor().plan('兵营训练容量','')['偶人'][0],'15')
    def test_lower_is_better(self):
        self.add(value='1');self.add(value='3');self.store.define('木材采集速度','%',-1)
        self.assertEqual(self.advisor().plan('木材采集速度','%')['偶人'][0],'1')
    def test_repeated_equipped_read_clears_other_slot(self):
        a=self.add(value='1');b=self.add(value='2')
        item=self.store.items()[1];item.equipped=True;self.store.save(item,confirmed=True)
        self.assertTrue(self.store.items()[1].equipped);self.assertFalse(self.store.items()[0].equipped)
    def test_unknown_attribute_remains_review_after_database_registration(self):
        from types import SimpleNamespace
        from treasure.app import App
        item=Item('新类别','新藏品','稀有','100',[Stat('新的条件效果','3')])
        App.prepare_item(SimpleNamespace(store=self.store),item)
        self.assertTrue(item.warnings)
        self.store.save(item,confirmed=False)
        repeated=Item('新类别','新藏品','稀有','100',[Stat('新的条件效果','3')])
        App.prepare_item(SimpleNamespace(store=self.store),repeated)
        self.assertTrue(repeated.warnings)
    def test_old_capture_queue_ignored_after_recycling(self):
        from types import SimpleNamespace
        import queue
        from treasure.app import App
        q=queue.Queue();q.put(('scan',object(),2))
        pending=SimpleNamespace(closed=False,ignore_scans=True,events=q,root=SimpleNamespace(after=lambda *args:None),poll=lambda:None)
        App.poll(pending)
        self.assertTrue(q.empty())
    def test_nonfinite_rejected(self):
        for value in ['NaN','Infinity','-Infinity']:
            with self.assertRaises(ValueError):Stat('test',value)

if __name__=='__main__':unittest.main()
