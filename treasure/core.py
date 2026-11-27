"""Local inventory, lossless decimal statistics and per-slot recommendations."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from difflib import SequenceMatcher
from opencc import OpenCC

_SIM = OpenCC('t2s')
_TRAD = OpenCC('s2twp')

def norm(text: str) -> str:
    text = _SIM.convert(str(text)).strip()
    return ''.join(text.split()).translate(str.maketrans({'【':'[','】':']','（':'(', '）':')','％':'%','−':'-','，':','}))

def traditional(text: str) -> str:
    return _TRAD.convert(text)

CATEGORY_GROUPS = {
    '精美饰物':['汉灯','偶人','美玉','面具','松石','金器'],
    '祭祀礼乐':['青铜觥','青铜尊','青铜壶','博山炉','青铜簋','青铜鼎'],
}
DEFAULT_CATEGORIES = [c for group in CATEGORY_GROUPS.values() for c in group]

def category_group(category):
    return next((group for group,categories in CATEGORY_GROUPS.items() if norm(category) in categories),'自订')
NAME_CATEGORIES = {
    '鹿形青铜油灯':'汉灯','击鼓说唱陶俑':'偶人','猪嘴玉龙':'美玉',
    '青铜兽面面具':'面具','鎏金镶绿松石龙首带钩':'松石',
    '镀金镶绿松石龙首带钩':'松石','鹰形金冠饰':'金器',
    '卧羊首金柄钥':'金器','卧羊首金琴钥':'金器',
    '龙纹兕觥':'青铜觥',
    '妇好青铜鸮尊':'青铜尊','何尊':'青铜尊','四羊方尊':'青铜尊',
    '颂壶':'青铜壶','曾中游父方壶':'青铜壶','曾仲游父方壶':'青铜壶',
    '错金银立鸟几何纹铜壶':'青铜壶','和田玉博山炉':'博山炉',
    '利簋':'青铜簋','伯旅父簋':'青铜簋','大禾方鼎':'青铜鼎','杜岭方鼎':'青铜鼎',
}
# Observed low-resolution glyph readings; never fuzzy-match arbitrary item names.
NAME_READINGS = {
    '龙纹兄':'龙纹兕觥','龙纹兄能':'龙纹兕觥',
    '妇好青铜鸡尊':'妇好青铜鸮尊',
    '利算':'利簋','利篮':'利簋','伯旅父算':'伯旅父簋',
}
SEED_ATTRS = [
    '木材采集速度','食物采集速度','铁矿采集速度','煤矿采集速度',
    '木材产量','食物产量','铁矿产量','煤矿产量','建造速度','研究速度','治疗速度',
    '降低太学院开发消耗','降低所有建筑建造消耗','兵营训练容量','兵营训练速度',
    '训练士兵消耗资源减少','训练骑兵资源消耗减少','训练弓兵资源消耗减少',
    '训练步兵资源消耗减少',
] + [f'{troop}{stat}' for troop in ['步兵','骑兵','弓兵','[全阵营]士兵']
     for stat in ['攻击力','防御力','生命值','破坏力']]
ATTRIBUTE_CATALOG = {(n,'%' if n!='兵营训练容量' else ''):1 for n in SEED_ATTRS}

def number(value) -> str:
    d = Decimal(str(value))
    if not d.is_finite():
        raise ValueError('Non-finite number')
    return format(d.normalize(), 'f')

def fmt(value, unit='%', sign=False):
    s = number(value)
    return ('+' if sign and Decimal(s) > 0 else '') + s + unit

@dataclass
class Stat:
    name: str
    value: str
    unit: str = '%'
    confidence: float = 1.0
    raw: str = ''
    def __post_init__(self):
        self.name = norm(self.name)
        self.value = number(self.value)
        if self.unit not in ('%', ''):
            raise ValueError('Unsupported unit')
        if not self.name:
            raise ValueError('Empty attribute')

@dataclass
class Item:
    category: str
    name: str
    quality: str
    power: str
    stats: list[Stat]
    equipped: bool = False
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    source: str = 'manual'
    id: int | None = None
    confirmed: bool = False
    quantity: int = 1
    draft_id: int | None = None
    complete: bool = True
    level: str = ''
    suspected_id: int | None = None
    observation_note: str = ''
    screen_side: str = ''
    def fingerprint(self):
        # Name is deliberately excluded: OCR variations in names must not duplicate gear.
        payload = [norm(self.category),norm(self.quality),str(self.power),
                   sorted((norm(s.name),number(s.value),s.unit) for s in self.stats)]
        if self.level:payload.append(norm(self.level))
        return hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()
    def label(self):
        category=self.name or self.category or '名稱待核對'
        if self.draft_id is not None:return f'{category} D{self.draft_id:04d}'
        return f'{category} #{self.id:04d}' if self.id else category

class Store:
    def __init__(self,path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA secure_delete=ON')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS categories(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS collection_names(name TEXT PRIMARY KEY,category TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attributes(name TEXT NOT NULL,unit TEXT NOT NULL,direction INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(name,unit));
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,fingerprint TEXT UNIQUE NOT NULL,category TEXT NOT NULL,name TEXT NOT NULL,quality TEXT NOT NULL,power TEXT NOT NULL,equipped INTEGER NOT NULL DEFAULT 0,confirmed INTEGER NOT NULL DEFAULT 0,quantity INTEGER NOT NULL DEFAULT 1,source TEXT NOT NULL,last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stats(item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,name TEXT NOT NULL,value TEXT NOT NULL,unit TEXT NOT NULL,raw TEXT NOT NULL DEFAULT '',PRIMARY KEY(item_id,name,unit));
        CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY AUTOINCREMENT,fingerprint TEXT UNIQUE NOT NULL,category TEXT NOT NULL,name TEXT NOT NULL,quality TEXT NOT NULL,power TEXT NOT NULL,payload TEXT NOT NULL,raw_text TEXT NOT NULL DEFAULT '',last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observation_aliases(fingerprint TEXT PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,item_fingerprint TEXT NOT NULL);
        ''')
        with self.conn:
            if 'level' not in {r[1] for r in self.conn.execute('PRAGMA table_info(items)')}:
                self.conn.execute("ALTER TABLE items ADD COLUMN level TEXT NOT NULL DEFAULT ''")
            self.conn.executemany('INSERT OR IGNORE INTO categories VALUES(?)',[(v,) for v in DEFAULT_CATEGORIES])
            self.conn.executemany('INSERT OR IGNORE INTO collection_names VALUES(?,?)',NAME_CATEGORIES.items())
            self.conn.executemany('INSERT OR IGNORE INTO attributes VALUES(?,?,1)',[(v,'%' if v!='兵营训练容量' else '') for v in SEED_ATTRS])
    def close(self): self.conn.close()
    def get_setting(self,key,default=None):
        row=self.conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def setting(self,key,value):
        with self.conn:self.conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def categories(self):return [r[0] for r in self.conn.execute('SELECT name FROM categories ORDER BY rowid')]
    def definitions(self):return {(n,u):d for n,u,d in self.conn.execute('SELECT name,unit,direction FROM attributes ORDER BY name')}
    def catalog_definitions(self):
        definitions=self.definitions()
        return {key:definitions.get(key,direction) for key,direction in sorted(ATTRIBUTE_CATALOG.items())}
    def name_categories(self):
        learned={}
        for item in self.items(True):
            if item.name:learned.setdefault(item.name,set()).add(item.category)
        return {n:next(iter(categories)) for n,categories in learned.items() if len(categories)==1}|dict(self.conn.execute('SELECT name,category FROM collection_names'))
    def define(self,name,unit,direction):
        if direction not in (-1,0,1):raise ValueError('Invalid direction')
        with self.conn:self.conn.execute('INSERT OR REPLACE INTO attributes VALUES(?,?,?)',(norm(name),unit,direction))
    def inferred_category(self,name):
        name=norm(name)
        return self.name_categories().get(name,'')
    def save(self,item:Item,confirmed=False,update_id=None,quantity=None):
        with self.conn:return self._save(item,confirmed,update_id,quantity)
    def _save(self,item:Item,confirmed=False,update_id=None,quantity=None):
        item.category=norm(item.category); item.name=norm(item.name); item.quality=norm(item.quality);item.level=norm(item.level)
        if not item.category or not item.stats:raise ValueError('category and attributes required')
        if confirmed and not item.complete:raise ValueError('incomplete attributes must be reviewed before confirmation')
        if len({(s.name,s.unit) for s in item.stats}) != len(item.stats): raise ValueError('duplicate attributes')
        count=item.quantity if quantity is None else int(quantity)
        if count<1:raise ValueError('quantity must be positive')
        fp=item.fingerprint(); now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.conn.execute('INSERT OR IGNORE INTO categories VALUES(?)',(item.category,))
        self.conn.executemany('INSERT OR IGNORE INTO attributes VALUES(?,?,0)',[(s.name,s.unit) for s in item.stats])
        existing=self.conn.execute('SELECT id FROM items WHERE fingerprint=?',(fp,)).fetchone()
        if update_id is not None:
            if existing and existing[0]!=update_id:raise ValueError('same visible data already exists; update quantity there')
            if not self.conn.execute('SELECT 1 FROM items WHERE id=?',(update_id,)).fetchone():raise ValueError('missing item')
            item_id=update_id
            self.conn.execute('UPDATE items SET fingerprint=?,category=?,name=?,quality=?,power=?,equipped=?,confirmed=?,quantity=?,source=?,last_seen=?,level=? WHERE id=?',
                (fp,item.category,item.name,item.quality,str(item.power),int(item.equipped),int(confirmed),count,item.source,now,item.level,item_id))
        elif existing:
            item_id=existing[0]
            # Reobserving never increments quantities and never downgrades a reviewed record.
            self.conn.execute('UPDATE items SET last_seen=?,source=? WHERE id=?',(now,item.source,item_id))
            if confirmed:
                self.conn.execute('UPDATE items SET confirmed=1 WHERE id=?',(item_id,))
                if item.equipped:
                    self.conn.execute('UPDATE items SET equipped=0 WHERE category=?',(item.category,))
                    self.conn.execute('UPDATE items SET equipped=1 WHERE id=?',(item_id,))
            return item_id,False
        else:
            cur=self.conn.execute('INSERT INTO items(fingerprint,category,name,quality,power,equipped,confirmed,quantity,source,last_seen,level) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (fp,item.category,item.name,item.quality,str(item.power),int(item.equipped),int(confirmed),count,item.source,now,item.level))
            item_id=cur.lastrowid
        self.conn.execute('DELETE FROM stats WHERE item_id=?',(item_id,))
        self.conn.executemany('INSERT INTO stats VALUES(?,?,?,?,?)',[(item_id,s.name,s.value,s.unit,s.raw) for s in item.stats])
        if item.equipped and confirmed:
            self.conn.execute('UPDATE items SET equipped=0 WHERE category=? AND id<>?',(item.category,item_id))
        return item_id,True
    def items(self,confirmed_only=False):
        rows=self.conn.execute('SELECT id,category,name,quality,power,equipped,confirmed,quantity,source,level FROM items'+(' WHERE confirmed=1' if confirmed_only else '')+' ORDER BY id').fetchall()
        attrs={}
        for item_id,n,v,u,raw in self.conn.execute('SELECT item_id,name,value,unit,raw FROM stats ORDER BY rowid'):
            attrs.setdefault(item_id,[]).append(Stat(n,v,u,raw=raw))
        return [Item(c,n,q,p,attrs.get(i,[]),equipped=bool(e),source=so,id=i,confirmed=bool(co),quantity=qt,level=level) for i,c,n,q,p,e,co,qt,so,level in rows]
    def delete(self,counts:dict[int,int]):
        # Called only after explicit confirmation of completed recycling.
        with self.conn:
            for item_id,count in counts.items():
                row=self.conn.execute('SELECT quantity FROM items WHERE id=?',(item_id,)).fetchone()
                if not row or count<1 or count>row[0]:raise ValueError('invalid recycled quantity')
                if count==row[0]:self.conn.execute('DELETE FROM items WHERE id=?',(item_id,))
                else:self.conn.execute('UPDATE items SET quantity=quantity-? WHERE id=?',(count,item_id))
            self._sync_draft_references(set(counts))
        # No deleted-item archive; reclaim unused pages now (inventory is small).
        self.conn.execute('VACUUM')
    def _sync_draft_references(self,changed_ids):
        for did,payload in self.conn.execute('SELECT id,payload FROM drafts').fetchall():
            value=json.loads(payload);item_id=value.get('editing_id')
            if item_id in changed_ids:
                actual=self.conn.execute('SELECT quantity FROM items WHERE id=?',(item_id,)).fetchone()
                if actual is None:
                    value['editing_id']=None;value.setdefault('warnings',[]).append('原紀錄已刪除，確認草稿時將新增一筆。')
                else:value['quantity']=actual[0]
            suspect=value.get('suspected_id')
            if suspect in changed_ids and not self.conn.execute('SELECT 1 FROM items WHERE id=?',(suspect,)).fetchone():value['suspected_id']=None
            self.conn.execute('UPDATE drafts SET payload=? WHERE id=?',(json.dumps(value,ensure_ascii=False,separators=(',',':')),did))
    def backup(self,destination):
        with sqlite3.connect(str(destination)) as target:self.conn.backup(target)
    @staticmethod
    def _draft_payload(item):
        return json.dumps({'stats':[{'name':a.name,'value':a.value,'unit':a.unit,'confidence':a.confidence,'raw':a.raw} for a in item.stats],
            'warnings':list(dict.fromkeys(item.warnings)), 'confidence':item.confidence,'equipped':item.equipped,
            'quantity':item.quantity,'source':item.source,'complete':item.complete,'editing_id':item.id,'level':item.level,'suspected_id':item.suspected_id},ensure_ascii=False,separators=(',',':'))
    def drafts(self):
        result=[]
        for did,c,n,q,p,payload in self.conn.execute('SELECT id,category,name,quality,power,payload FROM drafts ORDER BY id'):
            v=json.loads(payload)
            result.append(Item(c,n,q,p,[Stat(**a) for a in v['stats']],equipped=bool(v.get('equipped')),confidence=v.get('confidence',0),
                warnings=v.get('warnings',[]),source=v.get('source','capture'),quantity=v.get('quantity',1),id=v.get('editing_id'),draft_id=did,complete=v.get('complete',False),level=v.get('level',''),suspected_id=v.get('suspected_id')))
        return result
    def draft_text(self,draft_id):
        row=self.conn.execute('SELECT raw_text FROM drafts WHERE id=?',(draft_id,)).fetchone()
        return row[0] if row else ''
    def _compatible_drafts(self,item):
        # Unknown metadata alone is not sufficient: only uniquely compatible subsets merge.
        if not item.power or not item.category:return []
        current={(s.name,s.unit):s.value for s in item.stats}
        matches=[]
        for draft in self.drafts():
            if draft.power!=item.power or draft.category!=item.category:continue
            if draft.quality and item.quality and draft.quality!=item.quality:continue
            if draft.level!=item.level:continue
            other={(s.name,s.unit):s.value for s in draft.stats}
            if current==other or (current.items()<=other.items() or other.items()<=current.items()):
                matches.append(draft)
        return matches
    def stage(self,item,raw_text='',update_id=None):
        item.category=norm(item.category);item.name=norm(item.name);item.quality=norm(item.quality)
        if item.quantity<1:raise ValueError('quantity must be positive')
        if not item.stats and not (item.power and (item.name or item.category)):
            raise ValueError('no recognizable item data to preserve')
        fp=item.fingerprint();row=self.conn.execute('SELECT id FROM drafts WHERE fingerprint=?',(fp,)).fetchone()
        existing=next((d for d in self.drafts() if row and d.draft_id==row[0]),None)
        if existing is None and item.suspected_id is not None:
            candidates=[d for d in self.drafts() if d.suspected_id==item.suspected_id and d.draft_id!=update_id and d.source!='manual-draft' and d.id is None]
            if len(candidates)==1:existing=candidates[0]
        if update_id is not None:
            original=next((d for d in self.drafts() if d.draft_id==update_id),None)
            if original is None:raise ValueError('draft no longer exists')
            if existing is None:existing=original
        if existing is None:
            compatible=self._compatible_drafts(item)
            if item.suspected_id is not None:compatible=[d for d in compatible if d.source!='manual-draft' and d.id is None]
            if len(compatible)==1:existing=compatible[0]
        # Keep a more complete observation; do not replace it with a shorter later read.
        chosen=item
        if update_id is None and existing is not None and (existing.source=='manual-draft' or existing.id is not None or (existing.complete,len(existing.stats),existing.confidence)>(item.complete,len(item.stats),item.confidence)):
            chosen=existing;fp=existing.fingerprint()
        payload=self._draft_payload(chosen);now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.conn:
            self.conn.executemany('INSERT OR IGNORE INTO attributes VALUES(?,?,0)',[(a.name,a.unit) for a in chosen.stats])
            if existing is not None:
                did=existing.draft_id
                self.conn.execute('UPDATE drafts SET fingerprint=?,category=?,name=?,quality=?,power=?,payload=?,raw_text=?,last_seen=? WHERE id=?',
                    (fp,chosen.category,chosen.name,chosen.quality,chosen.power,payload,(self.draft_text(did) if chosen is existing else raw_text)[:12000],now,did))
            else:
                cur=self.conn.execute('INSERT INTO drafts(fingerprint,category,name,quality,power,payload,raw_text,last_seen) VALUES(?,?,?,?,?,?,?,?)',
                    (fp,item.category,item.name,item.quality,item.power,payload,raw_text[:12000],now));did=cur.lastrowid
            if update_id is not None and update_id!=did:self.conn.execute('DELETE FROM drafts WHERE id=?',(update_id,))
        return did
    def observe(self,item,confirmed=False,raw_text=''):
        existing=next((i for i in self.items() if i.fingerprint()==item.fingerprint()),None)
        if existing is None and item.source!='manual-draft':
            alias=self.conn.execute('SELECT item_id,item_fingerprint FROM observation_aliases WHERE fingerprint=?',(item.fingerprint(),)).fetchone()
            target=next((i for i in self.items(True) if alias and i.id==alias[0] and i.fingerprint()==alias[1]),None)
            if target is not None:
                item.observation_note='沿用已核對的辨識差異對應；既有屬性與件數保持不變。'
                return 'item',target.id
            if alias is not None:
                with self.conn:self.conn.execute('DELETE FROM observation_aliases WHERE fingerprint=?',(item.fingerprint(),))
            target,kind=self.related_record(item)
            if target is not None:
                if kind=='partial':
                    item.observation_note='本次漏讀或等級未讀到，沿用既有已確認資料。'
                    return 'item',target.id
                item.suspected_id=target.id;item.warnings=list(dict.fromkeys(item.warnings+[f'疑似已收錄 #{target.id:04d}；請比對或沿用既有資料。']))
                return 'draft',self.stage(item,raw_text)
        if confirmed:
            saved_id,_=self.save(item,confirmed=True)
            # Remove only the uniquely matching draft when the full record is confirmed.
            matching=self._compatible_drafts(item)
            exact=[d for d in self.drafts() if d.fingerprint()==item.fingerprint()]
            removable=[d for d in (exact or (matching if len(matching)==1 else [])) if d.id is None and d.source!='manual-draft']
            with self.conn:
                for d in removable:self.conn.execute('DELETE FROM drafts WHERE id=?',(d.draft_id,))
            return 'item',saved_id
        if existing is not None:
            self.save(item,confirmed=False)
            return 'item',existing.id
        return 'draft',self.stage(item,raw_text)
    def related_record(self,item):
        """Conservative hints: same name, slot, power; never absorb changed values."""
        if not (item.name and item.category and item.power and item.stats):return None,None
        observed={(s.name,s.unit):s.value for s in item.stats}
        if len(observed)!=len(item.stats):return None,None
        matches=[];definitions=self.definitions()
        for known in self.items(True):
            if (norm(known.name),known.category,known.power)!=(norm(item.name),norm(item.category),item.power):continue
            if item.quality and item.quality!=known.quality:continue
            if item.level and item.level!=known.level:continue
            values={(s.name,s.unit):s.value for s in known.stats}
            if len(observed)>len(values):continue
            used=set();exact=0;valid=True
            for key,value in observed.items():
                if key in values:
                    if key in used or values[key]!=value:valid=False;break
                    used.add(key);exact+=1;continue
                # A valid other troop/scope/effect is not an OCR synonym.
                if key in definitions and definitions[key]!=0:valid=False;break
                choices=[k for k,v in values.items() if k not in used and k[1]==key[1] and v==value
                         and SequenceMatcher(None,k[0],key[0]).ratio()>=.84
                         and (k[0].split(']')[0] if ']' in k[0] else '')==(key[0].split(']')[0] if ']' in key[0] else '')]
                if len(choices)!=1:valid=False;break
                used.add(choices[0])
            if not valid:continue
            all_exact=exact==len(observed)
            enough=len(observed)>=2 or (all_exact and len(observed)==len(values))
            if enough:matches.append((known,'partial' if all_exact and (not item.complete or (not item.level and known.level)) else 'suspect'))
        return matches[0] if len(matches)==1 else (None,None)
    def accept_existing(self,draft_id,item_id):
        draft=next((d for d in self.drafts() if d.draft_id==draft_id),None)
        target=next((i for i in self.items(True) if i.id==item_id),None)
        if draft is None or target is None or draft.suspected_id!=item_id:raise ValueError('對應資料已變更，請重新比對')
        related,kind=self.related_record(draft)
        if related is None or related.id!=item_id:raise ValueError('對應資料已變更，請重新比對')
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO observation_aliases VALUES(?,?,?)',(draft.fingerprint(),item_id,target.fingerprint()))
            self.conn.execute('DELETE FROM drafts WHERE id=?',(draft_id,))
        return item_id
    def organize_pending(self):
        changed=0
        for draft in self.drafts():
            if draft.id is not None or draft.source not in ('capture','detail','compare','recycle'):continue
            target,kind=self.related_record(draft)
            if target is None:continue
            draft.suspected_id=target.id
            draft.warnings=list(dict.fromkeys(draft.warnings+[f'疑似已收錄 #{target.id:04d}；請比對或沿用既有資料。']))
            self.stage(draft,self.draft_text(draft.draft_id),update_id=draft.draft_id);changed+=1
        groups={}
        for draft in self.drafts():
            if draft.suspected_id is not None and draft.id is None and draft.source in ('capture','detail','compare','recycle'):
                groups.setdefault(draft.suspected_id,[]).append(draft)
        for group in groups.values():
            if len(group)<2:continue
            keep=min(d.draft_id for d in group);best=max(group,key=lambda d:(d.complete,len(d.stats),d.confidence))
            raw=self.draft_text(best.draft_id)
            with self.conn:
                for draft in group:
                    if draft.draft_id!=keep:self.conn.execute('DELETE FROM drafts WHERE id=?',(draft.draft_id,))
                self.conn.execute('UPDATE drafts SET fingerprint=?,category=?,name=?,quality=?,power=?,payload=?,raw_text=? WHERE id=?',
                    (best.fingerprint(),best.category,best.name,best.quality,best.power,self._draft_payload(best),raw,keep))
        return changed
    def counts(self):
        items=self.items();drafts=self.drafts()
        return {'confirmed_groups':sum(i.confirmed for i in items),'confirmed_quantity':sum(i.quantity for i in items if i.confirmed),
                'pending':sum(not i.confirmed for i in items)+len(drafts),'suspected':sum(d.suspected_id is not None for d in drafts)}
    def resolve_draft(self,draft_id,item,confirmed=True):
        draft=next((d for d in self.drafts() if d.draft_id==draft_id),None)
        if draft is None:raise ValueError('draft no longer exists')
        existing=next((i for i in self.items() if i.fingerprint()==item.fingerprint()),None)
        if existing is not None and item.id is None and item.source in ('capture','detail','compare','recycle','import'):item.quantity=existing.quantity
        # save() and draft removal share one transaction; a failed edit preserves the draft.
        with self.conn:
            item_id,_=self._save(item,confirmed=confirmed,update_id=item.id if item.id is not None else existing.id if existing else None)
            if confirmed and existing is not None and draft.suspected_id==item_id:
                self.conn.execute('INSERT OR REPLACE INTO observation_aliases VALUES(?,?,?)',(draft.fingerprint(),item_id,item.fingerprint()))
            self.conn.execute('DELETE FROM drafts WHERE id=?',(draft_id,))
        return item_id
    def confirm_visible(self,item):
        """Explicitly accept the displayed snapshot without changing a live OCR object."""
        reviewed=replace(item,stats=list(item.stats),warnings=[],complete=True,confirmed=True,draft_id=None)
        if item.suspected_id is not None:raise ValueError('疑似重複資料，請先比對並選擇沿用既有，或在編輯中補正。')
        if not reviewed.category or not reviewed.stats:raise ValueError('需要擺放類別與至少一項完整屬性；請用編輯／補正。')
        if len({(s.name,s.unit) for s in reviewed.stats})!=len(reviewed.stats):raise ValueError('屬性重複，請先編輯／補正。')
        existing=next((i for i in self.items() if i.fingerprint()==reviewed.fingerprint()),None)
        draft=next((d for d in self.drafts() if d.draft_id==item.draft_id),None) if item.draft_id is not None else None
        if draft is not None and draft.fingerprint()!=reviewed.fingerprint():raise ValueError('草稿已更新，請重新擷取或編輯／補正後確認。')
        if item.draft_id is not None and draft is None and existing is None:raise ValueError('此草稿已移除，請重新擷取。')
        if item.id is not None and item.draft_id is None and (existing is None or existing.id!=item.id):raise ValueError('此紀錄已變更，請重新擷取。')
        # A new OCR observation cannot reset an existing group's manually checked count.
        if existing is not None and item.source!='manual-draft':reviewed.quantity=existing.quantity
        with self.conn:
            item_id,_=self._save(reviewed,confirmed=True,update_id=reviewed.id or (existing.id if existing else None))
            # Same convention as the full editor's explicit review checkbox.
            for stat in reviewed.stats:
                self.conn.execute('UPDATE attributes SET direction=1 WHERE name=? AND unit=? AND direction=0',(stat.name,stat.unit))
            if draft is not None:self.conn.execute('DELETE FROM drafts WHERE id=?',(draft.draft_id,))
        return item_id
    def remove_records(self,item_ids=(),draft_ids=()):
        item_ids=set(item_ids);draft_ids=set(draft_ids)
        with self.conn:
            for table,ids in (('items',item_ids),('drafts',draft_ids)):
                for record_id in ids:
                    if not self.conn.execute(f'SELECT 1 FROM {table} WHERE id=?',(record_id,)).fetchone():raise ValueError('record no longer exists')
            for table,ids in (('items',item_ids),('drafts',draft_ids)):
                for record_id in ids:self.conn.execute(f'DELETE FROM {table} WHERE id=?',(record_id,))
            self._sync_draft_references(item_ids)
        self.conn.execute('VACUUM')
    def clear(self,reset_rules=False,reset_settings=False):
        with self.conn:
            self.conn.execute('DELETE FROM items');self.conn.execute('DELETE FROM drafts')
            # Full clears start a new inventory; ordinary deletion never reuses IDs.
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('items','drafts')")
            if reset_rules:
                self.conn.execute('DELETE FROM attributes');self.conn.execute('DELETE FROM categories')
                self.conn.execute('DELETE FROM collection_names')
                self.conn.executemany('INSERT INTO collection_names VALUES(?,?)',NAME_CATEGORIES.items())
                self.conn.executemany('INSERT INTO categories VALUES(?)',[(v,) for v in DEFAULT_CATEGORIES])
                self.conn.executemany('INSERT INTO attributes VALUES(?,?,1)',[(v,'%' if v!='兵营训练容量' else '') for v in SEED_ATTRS])
            if reset_settings:self.conn.execute('DELETE FROM settings')
        self.conn.execute('VACUUM')

@dataclass
class AttributeComparison:
    stat: Stat
    state: str
    best_other: str | None
    alternatives: list[int]

class Advisor:
    def __init__(self,items:list[Item],definitions):
        self.items=items;self.definitions=definitions
    def compare(self,item:Item,excluded=None):
        excluded=set(excluded or [])
        result=[]
        for s in item.stats:
            direction=self.definitions.get((s.name,s.unit),0)
            if not direction:
                result.append(AttributeComparison(s,'unknown',None,[]));continue
            alternatives=[]
            for other in self.items:
                if not other.confirmed or other.category!=item.category or other.id==item.id or other.id in excluded:continue
                for v in other.stats:
                    if (v.name,v.unit)==(s.name,s.unit):alternatives.append((Decimal(v.value),other.id))
            # Absence is NOT used as evidence of a substitute. Actual matching stats only.
            if not alternatives:result.append(AttributeComparison(s,'unique',None,[]));continue
            best=max(v*direction for v,_ in alternatives)*direction
            ids=[i for v,i in alternatives if v==best]
            current=Decimal(s.value)
            state='unique' if (current-best)*direction>0 else 'tie' if current==best else 'lower'
            result.append(AttributeComparison(s,state,number(best),ids))
        return result
    def verdict(self,item):
        if not item.confirmed:return 'review'
        rows=self.compare(item)
        if not rows or any(r.state=='unknown' for r in rows):return 'review'
        if any(r.state=='unique' for r in rows):return 'keep'
        if not self.dominators(item):return 'multi'
        if any(r.state=='tie' for r in rows):return 'tie'
        return 'candidate'
    def dominators(self,item,excluded=None):
        """One retained item must cover all effects, including any added drawbacks."""
        excluded=set(excluded or [])
        current={(s.name,s.unit):Decimal(s.value) for s in item.stats}
        if not current or any(not self.definitions.get(k) for k in current):return []
        result=[]
        for other in self.items:
            if not other.confirmed or other.category!=item.category or other.id==item.id or other.id in excluded:continue
            values={(s.name,s.unit):Decimal(s.value) for s in other.stats}
            if not current.keys()<=values.keys():continue
            keys=current.keys()|values.keys()
            if all(self.definitions.get(k,0) and (values.get(k,Decimal(0))-current.get(k,Decimal(0)))*self.definitions[k]>=0 for k in keys):result.append(other.id)
        return result
    def weighted_plan(self,goals):
        """Exact additive optimum: independent slots, explicit weights, percent effects."""
        parsed=[];seen=set()
        for name,unit,weight in goals:
            key=(norm(name),unit);weight=Decimal(number(weight))
            if key in seen or weight<=0:raise ValueError('屬性不可重複，權重必須大於 0')
            if not self.definitions.get(key,0):raise ValueError('請先核對目標屬性的比較方向')
            if unit!='%':raise ValueError('多用途加權目前只支援百分比屬性，固定數量請用單項專精')
            seen.add(key);parsed.append((key,weight))
        if not parsed:raise ValueError('請先設定至少一項多用途目標')
        groups={}
        for item in self.items:
            if item.confirmed:groups.setdefault(item.category,[]).append(item)
        result={};totals={key:Decimal(0) for key,_ in parsed};current=dict(totals);missing=[]
        for category,items in groups.items():
            values={i.id:{(s.name,s.unit):Decimal(s.value) for s in i.stats} for i in items}
            if not any(any(key in values[i.id] for key,_ in parsed) for i in items):missing.append(category);continue
            def score(i):return sum((values[i.id].get(key,Decimal(0))*weight*self.definitions[key] for key,weight in parsed),Decimal(0))
            best=max(score(i) for i in items)
            winners=sorted((i for i in items if score(i)==best),key=lambda i:(not i.equipped,i.id or 0))
            result[category]=(number(best),winners)
            for key,_ in parsed:
                totals[key]+=values[winners[0].id].get(key,Decimal(0))
                current[key]+=next((values[i.id].get(key,Decimal(0)) for i in items if i.equipped),Decimal(0))
        return result,totals,current,missing
    def plan(self,name,unit):
        direction=self.definitions.get((name,unit),0)
        if not direction:return {}
        by_category={}
        for item in self.items:
            if not item.confirmed:continue
            for s in item.stats:
                if (s.name,s.unit)==(name,unit):by_category.setdefault(item.category,[]).append((Decimal(s.value),item))
        result={}
        for cat,group in by_category.items():
            best=max(v*direction for v,_ in group)*direction
            winners=[i for v,i in group if v==best]
            winners.sort(key=lambda i:(not i.equipped,i.id or 0))
            result[cat]=(number(best),winners)
        return result
    def recycle_risks(self,counts:dict[int,int]):
        excluded={i.id for i in self.items if counts.get(i.id,0)>=i.quantity}
        risks={}
        for item in self.items:
            if item.id not in counts:continue
            reasons=[]
            if not item.confirmed:reasons.append('review')
            if item.equipped:reasons.append('equipped')
            if item.id in excluded:
                for r in self.compare(item,excluded):
                    if r.state in ('unique','unknown'):reasons.append(r.stat.name)
                if item.confirmed and not self.dominators(item,excluded):reasons.append('multi')
            risks[item.id]=reasons
        return risks
    def removal_impact(self,counts,goals=(),scope=None):
        """Read-only losses for an explicitly selected batch, without choosing a policy."""
        confirmed=[i for i in self.items if i.confirmed]
        by_id={i.id:i for i in confirmed}
        if any(k not in by_id or v<1 or v>by_id[k].quantity for k,v in counts.items()):raise ValueError('請先選取已確認藏品並核對件數')
        excluded={k for k,v in counts.items() if v==by_id[k].quantity}
        categories={by_id[k].category for k in counts}
        remaining=[i for i in confirmed if i.id not in excluded]
        effects=[]
        for category in sorted(categories):
            keys={(s.name,s.unit) for i in confirmed if i.category==category for s in i.stats}
            for key in sorted(keys):
                direction=self.definitions.get(key,0)
                def best(items):
                    values=[Decimal(s.value) for i in items if i.category==category for s in i.stats if (s.name,s.unit)==key]
                    return (max(v*direction for v in values)*direction) if values and direction else None
                before=best(confirmed);after=best(remaining)
                effects.append({'category':category,'name':key[0],'unit':key[1],
                    'before':number(before) if before is not None else None,'after':number(after) if after is not None else None,
                    'loss':number((before-after)*direction) if before is not None and after is not None else None})
        score=None
        if goals:
            parsed=[((norm(n),u),Decimal(number(w))) for n,u,w in goals]
            if any(w<=0 or not self.definitions.get(key) for key,w in parsed):raise ValueError('目標權重或屬性規則尚未設定')
            if len(parsed)>1 and any(key[1]!='%' for key,w in parsed):raise ValueError('多用途比較只支援百分比')
            def total(items):
                groups={i.category for i in items if scope is None or category_group(i.category)==scope}
                return sum((max((sum((Decimal(next((s.value for s in i.stats if (s.name,s.unit)==key),'0'))*w*self.definitions[key] for key,w in parsed),Decimal(0)) for i in items if i.category==category),default=Decimal(0)) for category in groups),Decimal(0))
            before=total(confirmed);after=total(remaining)
            score={'before':number(before),'after':number(after),'loss':number(before-after)}
        return {'effects':effects,'score':score}
