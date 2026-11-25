import hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from treasure.core import Store,Item,Stat,Advisor,CATEGORY_GROUPS,DEFAULT_CATEGORIES,category_group
from treasure.ocr import Scan,Line,parse,preferred_preview

class Version060Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'inventory.sqlite3';self.store=Store(self.path)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def item(self,**kwargs):
        return Item('青铜尊','测试藏品甲','稀有','100001',[Stat('木材产量','4'),Stat('煤矿产量','5'),Stat('训练士兵消耗资源减少','2')],source='detail',**kwargs)
    def known(self,**kwargs):
        item=self.item(**kwargs);self.store.save(item,confirmed=True);return self.store.items()[0]
    def typo(self):
        item=self.item();item.stats[-1]=Stat('训练土兵消耗资源减少','2');item.complete=False;return item
    def test_catalog_has_twelve_separate_slots(self):
        self.assertEqual(len(DEFAULT_CATEGORIES),12);self.assertEqual(category_group('青銅尊'),'祭祀礼乐')
        self.assertEqual(category_group('汉灯'),'精美饰物');self.assertEqual(category_group('其他'),'自订')
        self.assertEqual(set(DEFAULT_CATEGORIES),set(self.store.categories()))
    def test_old_fingerprint_and_ids_stay_stable_without_level(self):
        item=self.item();payload=[item.category,item.quality,item.power,sorted((s.name,s.value,s.unit) for s in item.stats)]
        expected=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest();self.assertEqual(item.fingerprint(),expected)
        known=self.known();self.store.setting('language','zh-CN')
        self.store.conn.execute('DROP TABLE observation_aliases');self.store.conn.execute('ALTER TABLE items DROP COLUMN level')
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.items()[0].id,known.id);self.assertEqual(self.store.items()[0].fingerprint(),known.fingerprint())
        self.assertEqual(self.store.get_setting('language'),'zh-CN');self.assertEqual(self.store.items()[0].level,'')
    def test_level_roundtrip_and_distinct_visible_levels(self):
        first=self.item(level='10');second=self.item(level='11')
        self.assertNotEqual(first.fingerprint(),second.fingerprint())
        self.store.save(first,confirmed=True);self.store.save(second,confirmed=True)
        self.assertEqual([i.level for i in self.store.items()],['10','11'])
        d=self.item(level='12',complete=False);did=self.store.stage(d)
        self.assertEqual(self.store.drafts()[0].level,'12')
        d=self.store.drafts()[0];d.complete=True;self.store.resolve_draft(did,d)
        self.assertEqual(self.store.items()[-1].level,'12')
    def test_actual_name_is_primary_label_and_duplicate_names_keep_ids(self):
        first=self.known();second=self.item();second.stats[0]=Stat('木材产量','6');self.store.save(second,confirmed=True)
        items=self.store.items();self.assertTrue(items[0].label().startswith('测试藏品甲'))
        self.assertNotEqual(items[0].label(),items[1].label())
    def test_missing_terms_reuse_unique_verified_item_without_downgrade(self):
        original=self.known(quantity=4);partial=self.item(complete=False);partial.stats=partial.stats[:2]
        kind,iid=self.store.observe(partial)
        self.assertEqual((kind,iid),('item',original.id));self.assertTrue(partial.observation_note)
        self.assertEqual(self.store.drafts(),[]);self.assertEqual(len(self.store.items()[0].stats),3);self.assertEqual(self.store.items()[0].quantity,4)
    def test_missing_level_reuses_unique_exact_observation(self):
        original=self.known(level='10');observed=self.item()
        self.assertEqual(self.store.observe(observed),('item',original.id))
        self.assertEqual(self.store.items()[0].level,'10')
    def test_different_explicit_level_is_not_absorbed(self):
        self.known(level='10');observed=self.item(level='11')
        self.assertEqual(self.store.observe(observed)[0],'draft');self.assertIsNone(self.store.drafts()[0].suspected_id)
    def test_typo_produces_review_hint_not_a_second_inventory_item(self):
        original=self.known(quantity=4);observed=self.typo()
        kind,did=self.store.observe(observed,confirmed=True)
        self.assertEqual(kind,'draft');draft=self.store.drafts()[0];self.assertEqual(draft.suspected_id,original.id)
        self.assertEqual(len(self.store.items()),1);self.assertEqual(self.store.items()[0].quantity,4)
        with self.assertRaises(ValueError):self.store.confirm_visible(draft)
    def test_typo_variations_share_one_review_record(self):
        self.known();one=self.typo();first=self.store.observe(one)[1]
        two=self.item(complete=False);two.stats[-1]=Stat('训练士兵消耗资原减少','2')
        self.assertEqual(self.store.observe(two)[1],first);self.assertEqual(len(self.store.drafts()),1)
    def test_accept_existing_remembers_alias_and_preserves_count(self):
        original=self.known(quantity=4);did=self.store.observe(self.typo())[1]
        self.store.accept_existing(did,original.id);self.assertEqual(self.store.drafts(),[])
        self.store.close();self.store=Store(self.path)
        for _ in range(4):self.assertEqual(self.store.observe(self.typo()),('item',original.id))
        self.assertEqual(self.store.drafts(),[]);self.assertEqual(self.store.items()[0].quantity,4)
        self.assertEqual(self.store.items()[0].stats[-1].name,'训练士兵消耗资源减少')
    def test_changed_numeric_value_is_not_treated_as_an_ocr_alias(self):
        self.known();item=self.typo();item.stats[0]=Stat('木材产量','9')
        self.assertEqual(self.store.observe(item)[0],'draft');self.assertIsNone(self.store.drafts()[0].suspected_id)
    def test_correcting_a_suspected_typo_preserves_quantity_and_remembers_read(self):
        original=self.known(quantity=4);did=self.store.observe(self.typo())[1]
        corrected=self.item();self.store.resolve_draft(did,corrected)
        self.assertEqual(self.store.items()[0].quantity,4)
        self.assertEqual(self.store.observe(self.typo()),('item',original.id));self.assertEqual(self.store.drafts(),[])
    def test_valid_other_effect_and_scope_are_never_fuzzy_merged(self):
        self.known()
        for stat in [Stat('训练骑兵资源消耗减少','2'),Stat('训练士兵消耗资源减少','2','')]:
            item=self.item(complete=False);item.stats[-1]=stat
            self.assertEqual(self.store.related_record(item),(None,None))
        original=self.item();original.stats[-1]=Stat('[全阵营]士兵攻击力','2');self.store.save(original,confirmed=True)
        item=self.item(complete=False);item.stats[-1]=Stat('士兵攻击力','2')
        self.assertEqual(self.store.related_record(item),(None,None))
    def test_ambiguous_items_do_not_match_partial_read(self):
        self.known();other=self.item();other.stats[-1]=Stat('训练士兵消耗资源减少','3');self.store.save(other,confirmed=True)
        partial=self.item(complete=False);partial.stats=partial.stats[:2]
        self.assertEqual(self.store.related_record(partial),(None,None))
    def test_two_observed_terms_cannot_map_to_one_verified_term(self):
        self.known();observed=self.item(complete=False)
        observed.stats=[Stat('训练土兵消耗资源减少','2'),Stat('训练士兵消耗资源减少','2')]
        self.assertEqual(self.store.related_record(observed),(None,None))
    def test_missing_identity_or_single_fragment_is_not_merged(self):
        self.known()
        for field in ('name','category','power'):
            item=self.typo();setattr(item,field,'');self.assertEqual(self.store.related_record(item),(None,None))
        item=self.item(complete=False);item.stats=item.stats[:1]
        self.assertEqual(self.store.related_record(item),(None,None))
    def test_organize_legacy_capture_drafts_protects_manual_work(self):
        original=self.known();one=self.typo();first=self.store.stage(one)
        two=self.item(complete=False);two.stats[-1]=Stat('训练士兵消耗资原减少','2');self.store.stage(two)
        manual=self.typo();manual.source='manual-draft';manual.stats[0]=Stat('木材产量','8');manual_id=self.store.stage(manual)
        self.store.organize_pending();drafts=self.store.drafts()
        self.assertEqual(len(drafts),2);self.assertTrue(any(d.draft_id==manual_id and d.source=='manual-draft' for d in drafts))
        self.assertEqual(sum(d.suspected_id==original.id for d in drafts),1)
    def test_alias_invalidates_after_verified_record_changes(self):
        original=self.known();did=self.store.observe(self.typo())[1];self.store.accept_existing(did,original.id)
        changed=self.store.items()[0];changed.stats[0]=Stat('木材产量','8');self.store.save(changed,confirmed=True,update_id=changed.id)
        self.assertEqual(self.store.observe(self.typo())[0],'draft')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM observation_aliases').fetchone()[0],0)
    def test_stale_review_hint_cannot_be_accepted_after_target_changes(self):
        original=self.known();did=self.store.observe(self.typo())[1]
        changed=self.store.items()[0];changed.stats[0]=Stat('木材产量','9');self.store.save(changed,confirmed=True,update_id=changed.id)
        with self.assertRaises(ValueError):self.store.accept_existing(did,original.id)
        self.assertEqual(len(self.store.drafts()),1)
    def test_delete_and_clear_remove_aliases_and_detach_review_hint(self):
        original=self.known();did=self.store.observe(self.typo())[1];self.store.accept_existing(did,original.id)
        another=self.item(complete=False);another.stats[-1]=Stat('训练士兵消耗资原减少','2');did=self.store.observe(another)[1]
        self.store.remove_records([original.id]);self.assertIsNone(self.store.drafts()[0].suspected_id)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM observation_aliases').fetchone()[0],0)
        self.store.clear();self.assertEqual(self.store.counts()['pending'],0)
    def test_counts_separate_units_groups_and_pending(self):
        self.known(quantity=4);self.store.observe(self.typo());other=self.item();other.power='200001';self.store.stage(other)
        self.assertEqual(self.store.counts(),{'confirmed_groups':1,'confirmed_quantity':4,'pending':2,'suspected':1})
    def test_tradeoffs_are_readonly_and_show_slot_loss(self):
        original=self.known();lower=self.item();lower.power='200001';lower.stats=[Stat('木材产量','2'),Stat('煤矿产量','3')];self.store.save(lower,confirmed=True)
        a=Advisor(self.store.items(),self.store.definitions());result=a.removal_impact({original.id:1},[('木材产量','%',1)],'祭祀礼乐')
        wood=next(r for r in result['effects'] if r['name']=='木材产量')
        self.assertEqual((wood['before'],wood['after'],wood['loss']),('4','2','2'))
        self.assertEqual(result['score']['loss'],'2');self.assertEqual(len(self.store.items()),2)
    def test_new_group_equipped_items_do_not_displace_original_group(self):
        old=self.item();old.category='汉灯';old.equipped=True;self.store.save(old,confirmed=True)
        self.known(equipped=True)
        self.assertTrue(all(i.equipped for i in self.store.items()))

class PreviewAndLayoutParsingTests(unittest.TestCase):
    def scan(self,right='2'):
        return Scan('compare',[Item('汉灯','测试左','稀有','100001',[Stat('木材产量','1')],screen_side='left'),Item('汉灯','测试右','稀有','100002',[Stat('木材产量',right)],screen_side='right')],[],'')
    def test_right_is_default_and_manual_side_persists_until_candidate_changes(self):
        scan=self.scan();self.assertEqual(preferred_preview(scan),1)
        self.assertEqual(preferred_preview(self.scan(),scan,0),0)
        self.assertEqual(preferred_preview(self.scan('3'),scan,0),1)
    def test_single_remaining_right_preview_and_noncomparison(self):
        scan=self.scan();scan.items=scan.items[1:];self.assertEqual(preferred_preview(scan),0)
        scan.mode='detail';self.assertEqual(preferred_preview(scan),0)
    def test_overview_identifies_empty_new_slots_without_inventing_items(self):
        lines=[Line(name,1,10,30+i*20,100,45+i*20) for i,name in enumerate(CATEGORY_GROUPS['祭祀礼乐'])]
        scan=parse(lines);self.assertEqual(scan.mode,'overview');self.assertEqual(scan.items,[])
        self.assertEqual(len(scan.diagnostics['categories']),6)
    def test_new_detail_category_and_explicit_level_parse_separately(self):
        lines=[Line('青铜尊',1,10,10,90,25),Line('稀有测试藏品',1,10,45,160,60),Line('100001',1,100,70,180,85),Line('等级10',1,10,70,75,85),Line('藏品属性',1,80,100,150,115)]
        for n,(name,value) in enumerate([('木材产量','+1%'),('煤矿产量','+2%'),('食物产量','+3%')]):
            y=130+n*30;lines.extend([Line(name,1,10,y,90,y+15),Line(value,1,170,y,210,y+15)])
        item=parse(lines).items[0];self.assertEqual((item.category,item.level,item.power),('青铜尊','10','100001'));self.assertEqual(len(item.stats),3)

if __name__=='__main__':unittest.main()
