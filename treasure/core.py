"""Local inventory, lossless decimal statistics and per-slot recommendations."""
from __future__ import annotations
from dataclasses import dataclass, field
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

DEFAULT_CATEGORIES = ['汉灯','偶人','美玉','面具','松石','金器']
NAME_CATEGORIES = {
    '鹿形青铜油灯':'汉灯','击鼓说唱陶俑':'偶人','猪嘴玉龙':'美玉',
    '青铜兽面面具':'面具','鎏金镶绿松石龙首带钩':'松石',
    '镀金镶绿松石龙首带钩':'松石','鹰形金冠饰':'金器',
    '卧羊首金柄钥':'金器','卧羊首金琴钥':'金器',
}
SEED_ATTRS = [
    '木材采集速度','食物采集速度','铁矿采集速度','煤矿采集速度',
    '木材产量','食物产量','铁矿产量','煤矿产量','建造速度','研究速度','治疗速度',
    '降低太学院开发消耗','降低所有建筑建造消耗','兵营训练容量','兵营训练速度',
    '训练士兵消耗资源减少','训练骑兵资源消耗减少','训练弓兵资源消耗减少',
    '训练步兵资源消耗减少',
] + [f'{troop}{stat}' for troop in ['步兵','骑兵','弓兵','[全阵营]士兵']
     for stat in ['攻击力','防御力','生命值','破坏力']]

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
    def fingerprint(self):
        # Name is deliberately excluded: OCR variations in names must not duplicate gear.
        payload = [norm(self.category),norm(self.quality),str(self.power),
                   sorted((norm(s.name),number(s.value),s.unit) for s in self.stats)]
        return hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()
    def label(self):
        return f'{self.category} #{self.id:04d}' if self.id else self.category

class Store:
    def __init__(self,path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA secure_delete=ON')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS categories(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS attributes(name TEXT NOT NULL,unit TEXT NOT NULL,direction INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(name,unit));
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,fingerprint TEXT UNIQUE NOT NULL,category TEXT NOT NULL,name TEXT NOT NULL,quality TEXT NOT NULL,power TEXT NOT NULL,equipped INTEGER NOT NULL DEFAULT 0,confirmed INTEGER NOT NULL DEFAULT 0,quantity INTEGER NOT NULL DEFAULT 1,source TEXT NOT NULL,last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stats(item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,name TEXT NOT NULL,value TEXT NOT NULL,unit TEXT NOT NULL,raw TEXT NOT NULL DEFAULT '',PRIMARY KEY(item_id,name,unit));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        with self.conn:
            self.conn.executemany('INSERT OR IGNORE INTO categories VALUES(?)',[(v,) for v in DEFAULT_CATEGORIES])
            self.conn.executemany('INSERT OR IGNORE INTO attributes VALUES(?,?,1)',[(v,'%' if v!='兵营训练容量' else '') for v in SEED_ATTRS])
    def close(self): self.conn.close()
    def get_setting(self,key,default=None):
        row=self.conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def setting(self,key,value):
        with self.conn:self.conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def categories(self):return [r[0] for r in self.conn.execute('SELECT name FROM categories ORDER BY rowid')]
    def definitions(self):return {(n,u):d for n,u,d in self.conn.execute('SELECT name,unit,direction FROM attributes ORDER BY name')}
    def define(self,name,unit,direction):
        if direction not in (-1,0,1):raise ValueError('Invalid direction')
        with self.conn:self.conn.execute('INSERT OR REPLACE INTO attributes VALUES(?,?,?)',(norm(name),unit,direction))
    def inferred_category(self,name):
        name=norm(name)
        row=self.conn.execute('SELECT category FROM items WHERE name=? ORDER BY confirmed DESC,id LIMIT 1',(name,)).fetchone()
        return row[0] if row else NAME_CATEGORIES.get(name,'')
    def save(self,item:Item,confirmed=False,update_id=None,quantity=None):
        item.category=norm(item.category); item.name=norm(item.name); item.quality=norm(item.quality)
        if not item.category or not item.stats:raise ValueError('category and attributes required')
        if len({(s.name,s.unit) for s in item.stats}) != len(item.stats): raise ValueError('duplicate attributes')
        count=item.quantity if quantity is None else int(quantity)
        if count<1:raise ValueError('quantity must be positive')
        fp=item.fingerprint(); now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO categories VALUES(?)',(item.category,))
            self.conn.executemany('INSERT OR IGNORE INTO attributes VALUES(?,?,0)',[(s.name,s.unit) for s in item.stats])
            existing=self.conn.execute('SELECT id FROM items WHERE fingerprint=?',(fp,)).fetchone()
            if update_id is not None:
                if existing and existing[0]!=update_id:raise ValueError('same visible data already exists; update quantity there')
                if not self.conn.execute('SELECT 1 FROM items WHERE id=?',(update_id,)).fetchone():raise ValueError('missing item')
                item_id=update_id
                self.conn.execute('UPDATE items SET fingerprint=?,category=?,name=?,quality=?,power=?,equipped=?,confirmed=?,quantity=?,source=?,last_seen=? WHERE id=?',
                    (fp,item.category,item.name,item.quality,str(item.power),int(item.equipped),int(confirmed),count,item.source,now,item_id))
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
                cur=self.conn.execute('INSERT INTO items(fingerprint,category,name,quality,power,equipped,confirmed,quantity,source,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (fp,item.category,item.name,item.quality,str(item.power),int(item.equipped),int(confirmed),count,item.source,now))
                item_id=cur.lastrowid
            self.conn.execute('DELETE FROM stats WHERE item_id=?',(item_id,))
            self.conn.executemany('INSERT INTO stats VALUES(?,?,?,?,?)',[(item_id,s.name,s.value,s.unit,s.raw) for s in item.stats])
            if item.equipped and confirmed:
                self.conn.execute('UPDATE items SET equipped=0 WHERE category=? AND id<>?',(item.category,item_id))
        return item_id,True
    def items(self,confirmed_only=False):
        rows=self.conn.execute('SELECT id,category,name,quality,power,equipped,confirmed,quantity,source FROM items'+(' WHERE confirmed=1' if confirmed_only else '')+' ORDER BY id').fetchall()
        attrs={}
        for item_id,n,v,u,raw in self.conn.execute('SELECT item_id,name,value,unit,raw FROM stats ORDER BY rowid'):
            attrs.setdefault(item_id,[]).append(Stat(n,v,u,raw=raw))
        return [Item(c,n,q,p,attrs.get(i,[]),equipped=bool(e),source=so,id=i,confirmed=bool(co),quantity=qt) for i,c,n,q,p,e,co,qt,so in rows]
    def delete(self,counts:dict[int,int]):
        # Called only after explicit confirmation of completed recycling.
        with self.conn:
            for item_id,count in counts.items():
                row=self.conn.execute('SELECT quantity FROM items WHERE id=?',(item_id,)).fetchone()
                if not row or count<1 or count>row[0]:raise ValueError('invalid recycled quantity')
                if count==row[0]:self.conn.execute('DELETE FROM items WHERE id=?',(item_id,))
                else:self.conn.execute('UPDATE items SET quantity=quantity-? WHERE id=?',(count,item_id))
        # No deleted-item archive; reclaim unused pages now (inventory is small).
        self.conn.execute('VACUUM')
    def backup(self,destination):
        with sqlite3.connect(str(destination)) as target:self.conn.backup(target)
    def clear(self):
        with self.conn:self.conn.execute('DELETE FROM items')
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
        if any(r.state=='tie' for r in rows):return 'tie'
        return 'candidate'
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
            risks[item.id]=reasons
        return risks
