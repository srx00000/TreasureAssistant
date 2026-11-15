import unittest
from decimal import Decimal
from itertools import product
from treasure.core import Advisor,Item,Stat

WOOD='木材产量';COAL='煤矿产量'
class MultiUseTests(unittest.TestCase):
    def gear(self,i,wood=None,coal=None,category='汉灯',confirmed=True,equipped=False,extras=()):
        stats=([Stat(WOOD,str(wood))] if wood is not None else [])+([Stat(COAL,str(coal))] if coal is not None else [])+list(extras)
        return Item(category,str(i),'稀有',str(1000+i),stats,id=i,confirmed=confirmed,equipped=equipped)
    def advisor(self,items,defs=None):return Advisor(items,defs or {(WOOD,'%'):1,(COAL,'%'):1})
    def goals(self,w='1',c='1'):return [(WOOD,'%',w),(COAL,'%',c)]
    def test_compromise_wins_equal_weights_but_specialist_wins_priority(self):
        items=[self.gear(1,20),self.gear(2,None,20),self.gear(3,12,12)]
        a=self.advisor(items)
        self.assertEqual(a.weighted_plan(self.goals())[0]['汉灯'][1][0].id,3)
        self.assertEqual(a.weighted_plan(self.goals('2'))[0]['汉灯'][1][0].id,1)
        self.assertEqual(a.verdict(items[2]),'multi');self.assertIn('multi',a.recycle_risks({3:1})[3])
    def test_one_item_covering_all_attributes_is_valid_replacement(self):
        items=[self.gear(1,12,12),self.gear(2,13,13)]
        a=self.advisor(items);self.assertEqual(a.verdict(items[0]),'candidate');self.assertEqual(a.recycle_risks({1:1})[1],[])
    def test_added_negative_effect_prevents_universal_replacement(self):
        items=[self.gear(1,12,12),self.gear(2,13,13,extras=[Stat('建造速度','-1')])]
        a=self.advisor(items,{(WOOD,'%'):1,(COAL,'%'):1,('建造速度','%'):1})
        self.assertEqual(a.dominators(items[0]),[]);self.assertEqual(a.verdict(items[0]),'multi')
    def test_unknown_effects_cannot_establish_safe_recycling(self):
        items=[self.gear(1,12,12),self.gear(2,13,13,extras=[Stat('未知效果','2')])]
        self.assertEqual(self.advisor(items).dominators(items[0]),[])
    def test_single_scope_and_unit_must_match(self):
        items=[self.gear(1,extras=[Stat('[全阵营]士兵生命值','2')]),self.gear(2,extras=[Stat('士兵生命值','10')])]
        a=self.advisor(items,{('[全阵营]士兵生命值','%'):1,('士兵生命值','%'):1})
        self.assertEqual(a.dominators(items[0]),[])
    def test_batch_does_not_use_another_deleted_record_as_substitute(self):
        items=[self.gear(1,1,1),self.gear(2,2,2),self.gear(3,3,3)]
        a=self.advisor(items);risks=a.recycle_risks({2:1,3:1})
        self.assertIn('multi',risks[2]);self.assertIn('multi',risks[3])
        self.assertFalse(a.recycle_risks({2:1})[2])
    def test_partial_quantity_keeps_its_own_substitute(self):
        item=self.gear(1,12,12);item.quantity=2
        self.assertEqual(self.advisor([item]).recycle_risks({1:1})[1],[])
    def test_duplicate_complete_effects_can_keep_one_but_not_delete_both(self):
        items=[self.gear(1,12,12),self.gear(2,12,12)]
        a=self.advisor(items);self.assertEqual(a.verdict(items[0]),'tie')
        self.assertEqual(a.recycle_risks({1:1})[1],[])
        self.assertTrue(all('multi' in r for r in a.recycle_risks({1:1,2:1}).values()))
    def test_unconfirmed_record_cannot_dominate_or_win(self):
        items=[self.gear(1,2,2),self.gear(2,100,100,confirmed=False)]
        a=self.advisor(items);self.assertEqual(a.dominators(items[0]),[])
        self.assertEqual(a.weighted_plan(self.goals())[0]['汉灯'][1][0].id,1)
    def test_totals_current_and_tie_prefer_equipped(self):
        items=[self.gear(1,2,2),self.gear(2,2,2,equipped=True),self.gear(3,3,4,'偶人',equipped=True)]
        plan,totals,current,_=self.advisor(items).weighted_plan(self.goals())
        self.assertEqual(plan['汉灯'][1][0].id,2);self.assertEqual(totals,current)
        self.assertEqual(totals[(WOOD,'%')],Decimal(5));self.assertEqual(totals[(COAL,'%')],Decimal(6))
    def test_missing_attribute_is_zero_bonus_not_a_different_unit(self):
        items=[self.gear(1,4,extras=[Stat(COAL,'100','')])]
        _,totals,_,_=self.advisor(items).weighted_plan(self.goals())
        self.assertEqual(totals[(COAL,'%')],0)
    def test_category_with_no_target_has_no_recommendation(self):
        items=[self.gear(1,2),self.gear(2,category='偶人',extras=[Stat('建造速度','5')])]
        plan,_,_,missing=self.advisor(items).weighted_plan(self.goals())
        self.assertNotIn('偶人',plan);self.assertIn('偶人',missing)
    def test_weighted_optimum_matches_exhaustive_slot_combinations(self):
        items=[self.gear(1,10,1),self.gear(2,3,6),self.gear(3,1,9,'偶人'),self.gear(4,8,2,'偶人')]
        plan,totals,_,_=self.advisor(items).weighted_plan(self.goals('1.25','0.75'))
        scores=[]
        for group in product(items[:2],items[2:]):
            scores.append(sum(Decimal(s.value)*(Decimal('1.25') if s.name==WOOD else Decimal('.75')) for i in group for s in i.stats))
        actual=totals[(WOOD,'%')]*Decimal('1.25')+totals[(COAL,'%')]*Decimal('.75')
        self.assertEqual(actual,max(scores));self.assertEqual(len(plan),2)
    def test_invalid_goals_are_rejected(self):
        a=self.advisor([self.gear(1,2,2)])
        invalid=[[],self.goals('0'),self.goals('-1'),self.goals('NaN'),[(WOOD,'%',1),(WOOD,'%',2)],[(WOOD,'',1)],[('未知','%',1)]]
        for goals in invalid:
            with self.subTest(goals=goals):
                with self.assertRaises(ValueError):a.weighted_plan(goals)
    def test_lower_direction_is_applied_and_decimals_preserved(self):
        items=[self.gear(1,'1.01','3'),self.gear(2,'1.02','3')]
        a=self.advisor(items,{(WOOD,'%'):-1,(COAL,'%'):1})
        self.assertEqual(a.weighted_plan(self.goals())[0]['汉灯'][1][0].id,1)
        self.assertEqual(a.dominators(items[1]),[1])

if __name__=='__main__':unittest.main()
