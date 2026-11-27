"""Catalog content is generic; inventory observations in these tests are synthetic."""
import tempfile,unittest
from pathlib import Path
from treasure.core import Store,Item,Stat,ATTRIBUTE_CATALOG,NAME_CATEGORIES
from treasure.ocr import Line,parse

SEASON_NAMES={
 '青铜觥':['龙纹兕觥'],
 '青铜尊':['妇好青铜鸮尊','何尊','四羊方尊'],
 '青铜壶':['颂壶','曾中游父方壶','错金银立鸟几何纹铜壶'],
 '博山炉':['和田玉博山炉'],
 '青铜簋':['利簋','伯旅父簋'],
 '青铜鼎':['大禾方鼎','杜岭方鼎'],
}

def line(value,x,y,width=80,height=18):return Line(value,.99,x-width/2,y-height/2,x+width/2,y+height/2)

def detail(name,quality='稀有',badge=None):
    lines=[line(quality+name,180,100,220),line('100001',185,130),line('藏品属性',180,160)]
    if badge:lines.append(line(badge,120,75))
    for j,effect in enumerate(['木材产量','煤矿产量','研究速度']):
        lines.extend([line(effect,125,195+j*35),line('+'+str(j+1)+'%',260,195+j*35,40)])
    return lines+[line('卸下',270,340)]

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite3';self.store=Store(self.path)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def typo(self):return Item('偶人','测试品','稀有','100001',[Stat('木材産量错字','4')],source='detail',complete=False)
    def test_twelve_new_names_seeded_as_category_metadata_only(self):
        mapping=self.store.name_categories()
        for category,names in SEASON_NAMES.items():
            for name in names:self.assertEqual(mapping[name],category)
        self.assertEqual(sum(map(len,SEASON_NAMES.values())),12)
        self.assertFalse(self.store.items());self.assertFalse(self.store.drafts())
    def test_names_persist_in_database_and_survive_restart(self):
        self.assertEqual(self.store.conn.execute('SELECT category FROM collection_names WHERE name=?',('何尊',)).fetchone()[0],'青铜尊')
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.inferred_category('龍紋兕觥'),'青铜觥')
    def test_additive_name_migration_preserves_inventory_drafts_and_settings(self):
        good=Item('偶人','测试旧品','稀有','100001',[Stat('木材产量','4')],quantity=3)
        iid=self.store.save(good,True)[0];fp=self.store.items()[0].fingerprint()
        did=self.store.stage(self.typo(),'original OCR text');self.store.setting('topmost',True)
        self.store.conn.execute('DROP TABLE collection_names');self.store.conn.commit();self.store.close();self.store=Store(self.path)
        self.assertEqual(self.store.items()[0].id,iid);self.assertEqual(self.store.items()[0].quantity,3)
        self.assertEqual(self.store.items()[0].fingerprint(),fp);self.assertEqual(self.store.draft_text(did),'original OCR text')
        self.assertTrue(self.store.get_setting('topmost'));self.assertEqual(self.store.inferred_category('何尊'),'青铜尊')
    def test_unknown_name_has_no_invented_category(self):self.assertEqual(self.store.inferred_category('没有收录的测试器物'),'')
    def test_known_catalog_wins_over_legacy_wrong_category(self):
        self.store.save(Item('偶人','何尊','稀有','100001',[Stat('木材产量','1')]),True)
        self.assertEqual(self.store.inferred_category('何尊'),'青铜尊')
        self.assertEqual(self.store.items()[0].category,'偶人')
    def test_conflicting_learned_name_is_not_guessed(self):
        for j,category in enumerate(('偶人','汉灯')):self.store.save(Item(category,'测试同名','稀有',str(100001+j),[Stat('木材产量','1')]),True)
        self.assertEqual(self.store.inferred_category('测试同名'),'')
    def test_clear_and_reset_restore_catalog_without_owned_items(self):
        for reset in (False,True):
            self.store.clear(reset_rules=reset)
            self.assertEqual(self.store.inferred_category('利簋'),'青铜簋');self.assertFalse(self.store.items())
    def test_pending_ocr_does_not_expand_selectable_catalog(self):
        before=self.store.catalog_definitions();did=self.store.stage(self.typo())
        self.assertEqual(self.store.catalog_definitions(),before)
        self.assertEqual(self.store.drafts()[0].draft_id,did);self.assertEqual(self.store.drafts()[0].stats[0].name,'木材产量错字')
    def test_even_confirmed_or_imported_custom_rules_do_not_expand_choices(self):
        bad=self.typo();bad.complete=True;self.store.save(bad,True);self.store.define(bad.stats[0].name,'%',1)
        self.store.define('旧库已确认错字','%',-1)
        self.assertNotIn((bad.stats[0].name,'%'),self.store.catalog_definitions())
        self.assertNotIn(('旧库已确认错字','%'),self.store.catalog_definitions())
        self.assertEqual(self.store.items()[0].stats[0].name,bad.stats[0].name)
    def test_canonical_direction_overrides_are_preserved(self):
        self.store.define('木材产量','%',-1)
        self.assertEqual(self.store.catalog_definitions()[('木材产量','%')],-1)
    def test_wrong_units_do_not_pollute_catalog(self):
        self.store.define('兵营训练容量','%',1);self.store.define('木材产量','',1)
        self.assertEqual(set(self.store.catalog_definitions()),set(ATTRIBUTE_CATALOG))
        self.assertIn(('兵营训练容量',''),self.store.catalog_definitions())
        self.assertNotIn(('兵营训练容量','%'),self.store.catalog_definitions())
    def test_all_correct_names_parse_without_own_inventory(self):
        for category,names in SEASON_NAMES.items():
            for name in names:
                item=parse(detail(name)).items[0];self.assertEqual(item.name,name);self.assertEqual(item.category,category)
    def test_explicit_low_resolution_readings_are_corrected_with_warning(self):
        for bad,good in [('龙纹兄','龙纹兕觥'),('妇好青铜鸡尊','妇好青铜鸮尊'),('利算','利簋'),('伯旅父算','伯旅父簋')]:
            item=parse(detail(bad)).items[0]
            self.assertEqual(item.name,good);self.assertEqual(item.category,NAME_CATEGORIES[good]);self.assertTrue(any('對照補正' in w for w in item.warnings))
    def test_name_match_repairs_unrecognized_badge_but_not_valid_conflict(self):
        self.assertEqual(parse(detail('龙纹兄',badge='青钢能')).items[0].category,'青铜觥')
        self.assertEqual(parse(detail('何尊',badge='偶人')).items[0].category,'偶人')
    def test_ascii_power_icon_does_not_join_separate_name(self):
        lines=detail('何尊');lines=[l for l in lines if l.text!='稀有何尊']
        lines.extend([line('稀有',90,100,50),line('何尊',180,100),line('M',140,125,15)])
        item=parse(lines).items[0];self.assertEqual(item.name,'何尊');self.assertEqual(item.category,'青铜尊')
    def test_wrapped_name_is_ordered_by_rows(self):
        lines=detail('何尊');lines=[l for l in lines if l.text!='稀有何尊']
        lines.extend([line('精良',70,100,50,30),line('错金银立鸟几何纹',210,94,150,16),line('铜壶',200,108,50,16)])
        item=parse(lines).items[0];self.assertEqual(item.name,'错金银立鸟几何纹铜壶');self.assertEqual(item.category,'青铜壶')

if __name__=='__main__':unittest.main()
