"""Screen-layout-aware OCR parsing. No screenshot persistence or network calls."""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from statistics import median
from .core import Item, Stat, norm, DEFAULT_CATEGORIES, NAME_CATEGORIES, category_group

@dataclass
class Line:
    text:str
    confidence:float
    left:float
    top:float
    right:float
    bottom:float
    @property
    def x(self):return (self.left+self.right)/2
    @property
    def y(self):return (self.top+self.bottom)/2
    @property
    def height(self):return max(1,self.bottom-self.top)

@dataclass
class Scan:
    mode:str
    items:list[Item]
    messages:list[str]
    text:str
    diagnostics:dict = field(default_factory=dict)

NUM = re.compile(r'([+\-]?\d+(?:[.,]\d+)?)\s*(%?)')
QUALITIES=('普通','精良','稀有','史诗','传奇')

def preferred_preview(scan,previous_scan=None,previous_index=-1):
    if not scan.items:return -1
    if scan.mode!='compare':return 0
    right=next((i for i,item in enumerate(scan.items) if item.screen_side=='right'),min(1,len(scan.items)-1))
    if previous_scan and previous_scan.mode=='compare' and 0<=previous_index<len(scan.items) and len(previous_scan.items)==len(scan.items):
        old_right=next((i for i,item in enumerate(previous_scan.items) if item.screen_side=='right'),min(1,len(previous_scan.items)-1))
        if previous_scan.items[old_right].fingerprint()==scan.items[right].fingerprint():return previous_index
    return right

def result_lines(results):
    lines=[]
    for box,text,score in results or []:
        xs=[p[0] for p in box];ys=[p[1] for p in box]
        lines.append(Line(norm(text),float(score),min(xs),min(ys),max(xs),max(ys)))
    return sorted(lines,key=lambda l:(l.y,l.x))

def attribute_headers(lines):
    """Allow one misread title glyph only with a nearby identity and value row."""
    target='藏品属性';headers=[]
    for line in lines:
        if line.text==target:headers.append(line);continue
        if len(line.text)!=len(target) or sum(a!=b for a,b in zip(line.text,target))!=1 or line.confidence<.65:continue
        span=max(line.right-line.left,line.height*3)
        identity=any(l.bottom<line.top and 0<line.y-l.y<max(240,line.height*16)
                     and abs(l.x-line.x)<span*1.5 and any(l.text.startswith(q) for q in QUALITIES) for l in lines)
        value=any(line.bottom<l.y<line.y+max(180,line.height*12)
                  and line.x<l.right<line.x+span*3
                  and re.search(r'[+\-]\d+(?:[.,]\d+)?%?$',l.text) for l in lines)
        if identity and value:headers.append(line)
    return headers

def parse(lines:list[Line],category_for_name=None):
    text='\n'.join(l.text for l in lines)
    # High-specificity app labels detect self-capture before any gear is staged.
    markers=('选取游戏区域','只在自己的藏品详情','重复辨识一致后','本次辨识结果',
             '结果/比较物件','结果/比较对象','其他最高','本件值','核对/编辑资料',
             '持有库/回收检查','擷取一次','即时判断','用途配装','核对/编辑本件')
    check_text=text.replace('／','/')
    hits=sorted({m for m in markers if norm(m).replace('／','/') in check_text})
    if len(hits)>=2 or '自己的藏品详情/替换/回收' in check_text:
        return Scan('blocked',[],['偵測到助手介面遮擋；請重新擷取，不會寫入持有庫。'],text,{'self_capture_markers':hits})
    headers=attribute_headers(lines)
    if not headers:
        categories=sorted({l.text for l in lines if l.text in DEFAULT_CATEGORIES})
        if len(categories)>=2:
            return Scan('overview',[],['已識別藏品總覽欄位；請打開單件詳情或替換畫面以收錄完整屬性。'],text,{'categories':categories,'groups':sorted({category_group(c) for c in categories})})
        numbers=sum(bool(NUM.fullmatch(l.text)) for l in lines)
        reason='讀到數值，但未找到完整「藏品屬性」標題；可能被視窗遮擋或選區裁切，請重新框選完整面板。' if numbers else '未找到藏品詳情；請在遊戲打開單件詳情、替換或回收預覽。'
        return Scan('none',[],[reason],text,{'numeric_lines':numbers})
    best=[]
    for h in headers:
        group=[j for j in headers if abs(h.y-j.y)<max(h.height,j.height)]
        if len(group)>len(best):best=group
    headers=sorted(best,key=lambda l:l.x)[:2]
    recycle_title=any(l.text=='藏品回收' and l.bottom<headers[0].top and headers[0].y-l.y<max(400,headers[0].height*30) for l in lines)
    mode='compare' if len(headers)==2 else 'recycle' if recycle_title else 'detail'
    items=[];messages=[];diagnostics={'columns':[],'attribute_headers':[{'text':h.text,'recovered':h.text!='藏品属性'} for h in headers]}
    mids=[(headers[0].x+headers[1].x)/2] if len(headers)==2 else []
    ignored={'查看','生效中','卸下','替换藏品','藏品回收','藏宝阁','藏品总战力','属性预览','精美饰物','祭祀礼乐'}
    for idx,header in enumerate(headers):
        column=[l for l in lines if not mids or (l.x<mids[0] if idx==0 else l.x>=mids[0])]
        above=[l for l in column if l.bottom<header.top and header.y-l.y<max(240,header.height*16)]
        quality_lines=[l for l in above if any(l.text.startswith(q) for q in QUALITIES)]
        qline=min(quality_lines,key=lambda l:abs(l.y-(header.y-55))) if quality_lines else None
        quality=next((q for q in QUALITIES if qline and qline.text.startswith(q)),'')
        level_lines=[l for l in above if header.y-l.y<140 and re.fullmatch(r'(?:等级[:：]?|Lv\.?|LV\.?|lv\.?)[0-9]{1,4}',l.text)]
        level=re.search(r'[0-9]+',level_lines[0].text).group() if len(level_lines)==1 else ''
        name=qline.text[len(quality):] if qline else ''
        if qline and not name:
            peers=[l for l in above if abs(l.y-qline.y)<qline.height and l is not qline and l.text not in ignored and not re.search(r'\d',l.text)]
            name=''.join(l.text for l in sorted(peers,key=lambda l:l.x))
        if not qline:
            names=[l for l in above if 3<=len(l.text)<=30 and l.text not in ignored and l.text not in DEFAULT_CATEGORIES
                   and re.search(r'[\u4e00-\u9fff]',l.text) and not re.search(r'\d',l.text) and header.y-l.y<130]
            if names:name=min(names,key=lambda l:abs(l.y-(header.y-55))).text
        name_y=qline.y if qline else header.y-60
        power_lines=[l for l in above if l not in level_lines and re.fullmatch(r'[^\d]{0,2}\d{4,12}',l.text) and l.y>name_y]
        power=re.sub(r'\D','',min(power_lines,key=lambda l:abs(l.y-(header.y-25))).text) if power_lines else ''
        category=''
        badges=[l for l in above if l.text in DEFAULT_CATEGORIES and header.y-l.y<160]
        if badges:category=min(badges,key=lambda l:abs(l.y-name_y)).text
        if not category and qline:
            possible=[l for l in above if 1<=len(l.text)<=8 and 8<qline.y-l.y<45 and l.text not in ignored and not re.search(r'\d',l.text)]
            if len(possible)==1 and mode=='detail':category=possible[0].text
        if not category:category=(category_for_name(name) if category_for_name else '') or NAME_CATEGORIES.get(name,'')
        after=[l for l in column if l.top>header.bottom and l.text in ('替换藏品','卸下','穿戴','普通','精良','可获得','回收','展示藏品对比','显示藏品对比','藏品对比')]
        stop=min([l.top for l in after]+[header.y+max(330,header.height*22)])
        body=[l for l in column if header.bottom<l.y<stop]
        values=[];bad_numbers=[]
        for l in body:
            if NUM.fullmatch(l.text) and l.x>header.x:values.append((l,l.text,''))
            else:
                m=re.search(r'([+\-]\d+(?:[.,]\d+)?%?)$',l.text)
                if m and re.search(r'[\u4e00-\u9fff]',l.text[:m.start()]):values.append((l,m.group(),l.text[:m.start()]))
                elif l.x>header.x and re.search(r'\d',l.text) and not re.search(r'[\u4e00-\u9fff]',l.text) and any(c in l.text for c in '%+-'):
                    bad_numbers.append(l)
        values.sort(key=lambda v:v[0].y)
        # Build text rows independently of numbers, so a missing number cannot join adjacent rows.
        inline_lines={id(v[0]) for v in values if v[2]}
        positions=[v[0].left if not v[2] else v[0].right-len(v[1])*v[0].height*.55 for v in values]+[l.left for l in bad_numbers]
        value_left=median(positions) if positions else header.x+40
        labels=[l for l in body if id(l) not in inline_lines and re.search(r'[\u4e00-\u9fff]',l.text)
                and l.text not in ignored and l.right<=value_left+8]
        labels.sort(key=lambda l:(l.y,l.x))
        text_height=median([l.height for l in labels]) if labels else header.height
        gaps=[values[j][0].y-values[j-1][0].y for j in range(1,len(values)) if values[j][0].y-values[j-1][0].y>text_height*.65]
        threshold=min(text_height*1.25,median(gaps)*.60) if gaps else text_height*1.25
        groups=[]
        def anchor(line):
            return min(range(len(values)),key=lambda j:abs(line.y-values[j][0].y)) if values else None
        for l in labels:
            if groups and l.y-groups[-1][-1].y<=max(4,threshold) and anchor(l)==anchor(groups[-1][0]):groups[-1].append(l)
            else:groups.append([l])
        def distance(y,group):
            top=min(l.top for l in group);bottom=max(l.bottom for l in group)
            return max(top-y,y-bottom,0)
        stats=[];warnings=[];scores=[qline.confidence] if qline else [header.confidence];unpaired=[];used=set()
        for vl,token,inline in values:
            options=[(distance(vl.y,g),j,g) for j,g in enumerate(groups) if j not in used]
            options.sort(key=lambda entry:(entry[0],abs(vl.y-sum(l.y for l in entry[2])/len(entry[2]))))
            matched=[]
            if options and options[0][0]<=max(4,text_height*.40):
                _,j,matched=options[0];used.add(j)
            label=(inline+''.join(l.text for l in sorted(matched,key=lambda l:(l.y,l.x)))).strip('·:：')
            if len(label)<2 or not re.search(r'[\u4e00-\u9fff]',label):
                if token.startswith(('+','-')) or '%' in token:unpaired.append(token)
                continue
            match=NUM.fullmatch(token)
            if not match:unpaired.append(token);continue
            score=min([vl.confidence]+[l.confidence for l in matched])
            try:stats.append(Stat(label,match.group(1).replace(',','.'),match.group(2),score,label))
            except ValueError:unpaired.append(token);continue
            scores.append(score)
        missing_labels=[''.join(l.text for l in group) for j,group in enumerate(groups) if j not in used]
        if unpaired:warnings.append('有數值缺少對應屬性名稱：'+'、'.join(unpaired))
        if missing_labels:warnings.append('屬性缺少可讀數值：'+'、'.join(missing_labels))
        if bad_numbers:warnings.append('數值文字未能解析：'+'、'.join(l.text for l in bad_numbers))
        hidden=any('未揭示' in l.text or '隐藏属性' in l.text for l in body)
        if hidden:warnings.append('存在未揭示屬性')
        if not category:warnings.append('擺放類別未讀到，請在核對頁選擇')
        if not quality:warnings.append('品質未讀到；可核對屬性後手動保存')
        if not name:warnings.append('名稱未讀到（次要資訊，可留空）')
        if not power:warnings.append('戰力未讀到（次要資訊，可留空）')
        expected={'精良':2,'稀有':3,'史诗':4,'传奇':5}.get(quality)
        if expected is None or len(stats)!=expected:warnings.append('請核對屬性是否完整；已配對 '+str(len(stats))+' 項')
        if len({(s.name,s.unit) for s in stats})!=len(stats):warnings.append('存在重複屬性')
        if min(scores)<.90:warnings.append('部分文字不清楚，請核對')
        diagnostics['columns'].append({'column':idx+1,'numeric_rows':len(values),'paired_attributes':len(stats),'unpaired_values':unpaired,'missing_category':not bool(category),'missing_quality':not bool(quality),'unpaired_labels':missing_labels,'unreadable_numbers':[l.text for l in bad_numbers]})
        if not stats:
            messages.append('第 '+str(idx+1)+' 欄讀到 '+str(len(values))+' 個數值，但沒有完整屬性名稱；請確認未被其他視窗遮擋。')
            if not (power and (name or category)):continue
        complete=bool(stats) and not (unpaired or missing_labels or bad_numbers or hidden) and expected is not None and len(stats)==expected and len({(s.name,s.unit) for s in stats})==len(stats)
        equipped=(idx==0 and mode=='compare') or (mode=='detail' and any(l.text=='卸下' for l in after))
        items.append(Item(category,name,quality,power,stats,equipped,min(scores),warnings,mode,complete=complete,level=level,screen_side=('left' if idx==0 else 'right') if mode=='compare' else ''))
    if mode=='compare' and any(l.text=='替换藏品' for l in lines) and len(items)==2:
        categories={i.category for i in items if i.category}
        if len(categories)==1:
            category=next(iter(categories))
            for item,column in zip(items,diagnostics['columns']):
                if not item.category:
                    item.category=category
                    item.warnings=[w for w in item.warnings if w!='擺放類別未讀到，請在核對頁選擇']
                    column['missing_category']=False;column['category_source']='same_replacement_slot'
        elif len(categories)>1:
            for item in items:item.warnings.append('替換兩側類別不一致，請核對欄位類別。')
    if not items and not messages:messages.append('找到屬性面板，但尚無可配對的屬性；請完整框選左側名稱與右側數值。')
    return Scan(mode,items,messages,text,diagnostics)

class Engine:
    def __init__(self):self.engine=None
    def recognize(self,image):
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        if self.engine is None:
            self.engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
        rgb=image.convert('RGB')
        result,_=self.engine(np.asarray(rgb)[:,:,::-1].copy(),use_cls=False)
        return result_lines(result)
