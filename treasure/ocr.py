"""Screen-layout-aware OCR parsing. No screenshot persistence or network calls."""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from .core import Item, Stat, norm, DEFAULT_CATEGORIES, NAME_CATEGORIES

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

def result_lines(results):
    lines=[]
    for box,text,score in results or []:
        xs=[p[0] for p in box];ys=[p[1] for p in box]
        lines.append(Line(norm(text),float(score),min(xs),min(ys),max(xs),max(ys)))
    return sorted(lines,key=lambda l:(l.y,l.x))

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
    headers=[l for l in lines if l.text=='藏品属性']
    if not headers:
        numbers=sum(bool(NUM.fullmatch(l.text)) for l in lines)
        reason='讀到數值，但未找到完整「藏品屬性」標題；可能被視窗遮擋或選區裁切，請重新框選完整面板。' if numbers else '未找到藏品詳情；請在遊戲打開單件詳情、替換或回收預覽。'
        return Scan('none',[],[reason],text,{'numeric_lines':numbers})
    best=[]
    for h in headers:
        group=[j for j in headers if abs(h.y-j.y)<max(h.height,j.height)]
        if len(group)>len(best):best=group
    headers=sorted(best,key=lambda l:l.x)[:2]
    mode='compare' if len(headers)==2 else 'recycle' if '藏品回收' in text else 'detail'
    items=[];messages=[];diagnostics={'columns':[]}
    mids=[(headers[0].x+headers[1].x)/2] if len(headers)==2 else []
    ignored={'查看','生效中','卸下','替换藏品','藏品回收','藏宝阁','藏品总战力','属性预览','精美饰物','祭祀礼乐'}
    for idx,header in enumerate(headers):
        column=[l for l in lines if not mids or (l.x<mids[0] if idx==0 else l.x>=mids[0])]
        above=[l for l in column if l.bottom<header.top and header.y-l.y<max(240,header.height*16)]
        quality_lines=[l for l in above if any(l.text.startswith(q) for q in QUALITIES)]
        qline=min(quality_lines,key=lambda l:abs(l.y-(header.y-55))) if quality_lines else None
        quality=next((q for q in QUALITIES if qline and qline.text.startswith(q)),'')
        name=qline.text[len(quality):] if qline else ''
        if qline and not name:
            peers=[l for l in above if abs(l.y-qline.y)<qline.height and l is not qline and l.text not in ignored and not re.search(r'\d',l.text)]
            name=''.join(l.text for l in sorted(peers,key=lambda l:l.x))
        if not qline:
            names=[l for l in above if 3<=len(l.text)<=30 and l.text not in ignored and l.text not in DEFAULT_CATEGORIES
                   and re.search(r'[\u4e00-\u9fff]',l.text) and not re.search(r'\d',l.text) and header.y-l.y<130]
            if names:name=min(names,key=lambda l:abs(l.y-(header.y-55))).text
        name_y=qline.y if qline else header.y-60
        power_lines=[l for l in above if re.fullmatch(r'[^\d]{0,2}\d{4,12}',l.text) and l.y>name_y]
        power=re.sub(r'\D','',min(power_lines,key=lambda l:abs(l.y-(header.y-25))).text) if power_lines else ''
        category=''
        badges=[l for l in above if l.text in DEFAULT_CATEGORIES and header.y-l.y<160]
        if badges:category=min(badges,key=lambda l:abs(l.y-name_y)).text
        if not category and qline:
            possible=[l for l in above if 1<=len(l.text)<=8 and 8<qline.y-l.y<45 and l.text not in ignored and not re.search(r'\d',l.text)]
            if len(possible)==1 and mode=='detail':category=possible[0].text
        if not category:category=(category_for_name(name) if category_for_name else '') or NAME_CATEGORIES.get(name,'')
        after=[l for l in column if l.top>header.bottom and l.text in ('替换藏品','卸下','穿戴','普通','精良','可获得','回收')]
        stop=min([l.top for l in after]+[header.y+max(330,header.height*22)])
        body=[l for l in column if header.bottom<l.y<stop]
        values=[]
        for l in body:
            if re.fullmatch(r'[+\-]?\d+(?:[.,]\d+)?%?',l.text):values.append((l,l.text,''))
            else:
                m=re.search(r'([+\-]\d+(?:[.,]\d+)?%?)$',l.text)
                if m:values.append((l,m.group(),l.text[:m.start()]))
        values.sort(key=lambda v:v[0].y)
        stats=[];warnings=[];scores=[qline.confidence] if qline else [header.confidence];unpaired=[]
        for j,(vl,token,inline) in enumerate(values):
            lo=header.bottom if j==0 else (values[j-1][0].y+vl.y)/2
            hi=stop if j==len(values)-1 else (vl.y+values[j+1][0].y)/2
            labels=[l for l in body if lo<=l.y<hi and l.right<=vl.left+6 and l is not vl and not NUM.fullmatch(l.text)]
            label=(inline or ''.join(l.text for l in sorted(labels,key=lambda l:(l.y,l.x)))).strip('·:：')
            if len(label)<2 or not re.search(r'[\u4e00-\u9fff]',label):
                unpaired.append(token);continue
            score=min([vl.confidence]+[l.confidence for l in labels])
            match=NUM.fullmatch(token)
            if not match:continue
            try:stats.append(Stat(label,match.group(1).replace(',','.'),match.group(2),score,label))
            except ValueError:unpaired.append(token);continue
            scores.append(score)
        if unpaired:warnings.append('有數值缺少對應屬性名稱：'+'、'.join(unpaired))
        if any('未揭示' in l.text or '隐藏属性' in l.text for l in body):warnings.append('存在未揭示屬性')
        if not category:warnings.append('擺放類別未讀到，請在核對頁選擇')
        if not quality:warnings.append('品質未讀到；可核對屬性後手動保存')
        if not name:warnings.append('名稱未讀到（次要資訊，可留空）')
        if not power:warnings.append('戰力未讀到（次要資訊，可留空）')
        expected={'稀有':3,'史诗':4,'传奇':5}.get(quality)
        if expected is None or len(stats)!=expected:warnings.append('請核對屬性是否完整；已配對 '+str(len(stats))+' 項')
        if len({(s.name,s.unit) for s in stats})!=len(stats):warnings.append('存在重複屬性')
        if min(scores)<.90:warnings.append('部分文字不清楚，請核對')
        diagnostics['columns'].append({'column':idx+1,'numeric_rows':len(values),'paired_attributes':len(stats),'unpaired_values':unpaired,'missing_category':not bool(category),'missing_quality':not bool(quality)})
        if not stats:
            messages.append('第 '+str(idx+1)+' 欄讀到 '+str(len(values))+' 個數值，但沒有完整屬性名稱；請確認未被其他視窗遮擋。')
            continue
        equipped=(idx==0 and mode=='compare') or (mode=='detail' and any(l.text=='卸下' for l in after))
        items.append(Item(category,name,quality,power,stats,equipped,min(scores),warnings,mode))
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
        result,_=self.engine(np.asarray(rgb)[:,:,::-1].copy())
        return result_lines(result)
