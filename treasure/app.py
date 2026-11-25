from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from decimal import Decimal
from difflib import SequenceMatcher
from .core import Store, Advisor, Item, Stat, norm, traditional, fmt, NAME_CATEGORIES, CATEGORY_GROUPS, category_group
from .ocr import Engine, parse, preferred_preview
from .widgets import StatRows
from .capture import overlaps, safe_position, request_frame

APP_ROOT=Path(__file__).resolve().parent.parent
VERSION='0.6.0'
STATES={'keep':'建議保留','tie':'並列備選','candidate':'可回收候選','review':'待核對','multi':'多用途保留'}
ROW_STATES={'unique':'本欄位最高／唯一','tie':'並列最高','lower':'已有較高值','unknown':'比較規則待核對'}


def configure_logging(data):
    logger=logging.getLogger('treasure.'+str(data));logger.setLevel(logging.INFO)
    if not logger.handlers:
        h=RotatingFileHandler(data/'errors.log',maxBytes=256_000,backupCount=1,encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(h)
    return logger


class App:
    def __init__(self,root,language=None,data=None,demo=False):
        self.root=root;self.data=Path(data or APP_ROOT/'data');self.data.mkdir(parents=True,exist_ok=True)
        self.log=configure_logging(self.data);self.store=Store(self.data/('demo.sqlite3' if demo else 'inventory.sqlite3'))
        self.lang=language or self.store.get_setting('language','zh-TW');self.region=self.store.get_setting('region')
        self.events=queue.Queue();self.cancel=threading.Event();self.worker=None
        self.engine=Engine();self.engine_lock=threading.Lock();self.pending=[];self.current=None;self.edit_id=None;self.edit_draft_id=None
        self.last_scan=None;self.demo=demo;self.closed=False;self.ignore_scans=False
        self.monitoring=False;self.editor_open=False;self.editor_window=None;self.owned_windows=[root];self.tickets=[]
        self.poll_after=None;self.capture_afters={};self.selection_after=None
        self.autosave=tk.BooleanVar(value=self.store.get_setting('autosave',True))
        self.auto_dock=tk.BooleanVar(value=self.store.get_setting('auto_dock',True));self.topmost=tk.BooleanVar(value=self.store.get_setting('topmost',False))
        root.title(self.t('藏品助手')+' '+VERSION+(' · DEMO' if demo else ''))
        root.geometry('470x640');root.minsize(430,600);root.attributes('-topmost',self.topmost.get())
        self.plan_goals=self.store.get_setting('plan_goals',[]);self.known_categories={}
        import tkinter.font as tkfont
        families=set(tkfont.families(root));choices=['Microsoft JhengHei UI','Microsoft YaHei UI','Noto Sans CJK TC','Noto Sans CJK SC','DejaVu Sans']
        self.font=next((f for f in choices if f in families),'TkDefaultFont')
        style=ttk.Style(root)
        if 'clam' in style.theme_names():style.theme_use('clam')
        style.configure('.',font=(self.font,10),background='#f4f7f4',foreground='#263a2c')
        style.configure('Treeview',background='#ffffff',fieldbackground='#ffffff',foreground='#263a2c',rowheight=30)
        style.configure('Treeview.Heading',background='#e3ebe4',font=(self.font,10,'bold'))
        style.map('Treeview',background=[('selected','#285d41')],foreground=[('selected','#ffffff')])
        style.configure('TButton',padding=(7,5));style.configure('Headline.TLabel',font=(self.font,15,'bold'))
        self.def_tree=None;self.def_rows=[]
        if demo and not self.store.items():self.seed_demo()
        self.build();self.refresh();self.poll_after=root.after(100,self.poll);root.protocol('WM_DELETE_WINDOW',self.close)
    def t(self,text):
        labels={'設定':'设置','擷取一次':'截图一次','選取／重選遊戲區域':'选择／重选游戏区域','匯入圖片':'导入图片','匯入持有庫':'导入藏品库','匯出持有庫':'导出藏品库','視窗置頂':'窗口置顶'}
        return labels.get(text,self.convert(text)) if self.lang=='zh-CN' else self.convert(text)
    def convert(self,text):
        from .core import _SIM
        return _SIM.convert(str(text)) if self.lang=='zh-CN' else traditional(str(text))
    def showerror(self,text):messagebox.showerror(self.t('無法完成'),self.convert(text),parent=self.root)
    def guard(self,fn):
        try:return fn()
        except Exception as e:self.log.exception('UI operation failed');self.showerror(str(e))
    def button(self,parent,text,command,**kw):return ttk.Button(parent,text=self.t(text),command=lambda:self.guard(command),**kw)
    def own(self,window):self.owned_windows.append(window);return window
    def make_tree(self,parent,columns,headings,widths,height=6):
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,columns=columns,show='headings',height=height,selectmode='extended')
        for col,head,width in zip(columns,headings,widths):tree.heading(col,text=self.t(head));tree.column(col,width=width,minwidth=40,stretch=True)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');tree.pack(side='left',fill='both',expand=True)
        return tree
    def wrapped(self,parent,textvariable=None,text=None):
        label=ttk.Label(parent,textvariable=textvariable,text=self.t(text) if text else None,wraplength=420,justify='left')
        label.bind('<Configure>',lambda e:label.configure(wraplength=max(160,e.width-4)))
        return label
    def build(self):
        for child in list(self.root.winfo_children()):
            if not isinstance(child,tk.Toplevel):child.destroy()
        base=ttk.Frame(self.root,padding=10);base.pack(fill='both',expand=True)
        top=ttk.Frame(base);top.pack(fill='x')
        ttk.Label(top,text=self.t('藏品助手')+(' · DEMO' if self.demo else ''),style='Headline.TLabel').pack(side='left')
        ttk.Label(top,text=VERSION).pack(side='right')
        toolbar=ttk.Frame(base);toolbar.pack(fill='x',pady=8)
        self.start_button=self.button(toolbar,'停止' if self.monitoring else '開始辨識',self.toggle_monitor);self.start_button.pack(side='left',padx=(0,6))
        self.button(toolbar,'擷取一次',self.capture_once).pack(side='left',padx=(0,6))
        self.run_label=tk.StringVar(value=self.t('監看中' if self.monitoring else '已停止'))
        ttk.Label(toolbar,textvariable=self.run_label).pack(side='right')
        self.notebook=ttk.Notebook(base)
        self.scan_tab=ttk.Frame(self.notebook,padding=8);self.plan_tab=ttk.Frame(self.notebook,padding=8)
        self.inventory_tab=ttk.Frame(self.notebook,padding=8);self.settings_tab=ttk.Frame(self.notebook,padding=8)
        for frame,title in [(self.scan_tab,'即時判斷'),(self.plan_tab,'用途配裝'),(self.inventory_tab,'持有庫'),(self.settings_tab,'設定')]:self.notebook.add(frame,text=self.t(title))
        self.build_scan();self.build_plan();self.build_inventory();self.build_settings()
        self.status=tk.StringVar(value=self.t('先到設定選取中央面板，再開始辨識。'))
        self.wrapped(base,textvariable=self.status).pack(side='bottom',fill='x',pady=(7,0))
        self.notebook.pack(fill='both',expand=True)
    def build_scan(self):
        self.result_choice=ttk.Combobox(self.scan_tab,state='readonly',values=[],width=1);self.result_choice.pack(fill='x')
        self.result_choice.bind('<<ComboboxSelected>>',self.choose_scan)
        self.item_heading=self.wrapped(self.scan_tab,text='等待遊戲藏品詳情');self.item_heading.configure(font=(self.font,10,'bold'));self.item_heading.pack(anchor='w',pady=(8,2))
        self.verdict_label=ttk.Label(self.scan_tab,text=self.t('等待辨識'),style='Headline.TLabel');self.verdict_label.pack(anchor='w',pady=(0,5))
        self.scan_notice=tk.StringVar();self.wrapped(self.scan_tab,textvariable=self.scan_notice).pack(fill='x',pady=(0,6))
        footer=ttk.Frame(self.scan_tab);footer.pack(side='bottom',fill='x')
        self.compare_tree=self.make_tree(self.scan_tab,['name','value','best'],['全部屬性','本件','其他最高'],[220,80,85],height=7)
        for state,color in [('unique','#e8f3eb'),('tie','#eaf0f7'),('lower','#f3f4f3'),('unknown','#fff0d7')]:self.compare_tree.tag_configure(state,background=color)
        self.comparison_detail=tk.StringVar(value=self.t('選取一項屬性，查看排名與比較對象。'))
        self.wrapped(footer,textvariable=self.comparison_detail).pack(fill='x',pady=6)
        self.compare_tree.bind('<<TreeviewSelect>>',lambda e:self.show_attribute_detail())
        bar=ttk.Frame(footer);bar.pack(fill='x')
        self.button(bar,'確認本件',self.confirm_current).pack(side='left',padx=(0,7))
        self.button(bar,'編輯／補正',self.edit_current).pack(side='left',padx=(0,7))
        self.reuse_button=self.button(bar,'沿用既有',self.reuse_existing);self.reuse_button.pack(side='left');self.reuse_button.configure(state='disabled')
        self.wrapped(footer,text='確認即視為已核對完整屬性；新屬性預設越高越好。').pack(fill='x',pady=(5,0))
    def build_plan(self):
        bar=ttk.Frame(self.plan_tab);bar.pack(fill='x')
        self.plan_mode=ttk.Combobox(bar,state='readonly',values=[self.t('單項專精'),self.t('多用途加權')],width=12)
        self.plan_mode.current(1 if self.store.get_setting('plan_mode',0)==1 else 0);self.plan_mode.pack(side='left')
        self.plan_mode.bind('<<ComboboxSelected>>',lambda e:self.change_plan_mode())
        self.button(bar,'設定多用途',self.configure_goals).pack(side='left',padx=7)
        self.plan_group=ttk.Combobox(self.plan_tab,state='readonly',values=[self.t(g) for g in CATEGORY_GROUPS]+[self.t('自訂'),self.t('全部組別（需確認同時生效）')]);self.plan_group.current(self.store.get_setting('plan_group',0));self.plan_group.pack(fill='x',pady=(6,0));self.plan_group.bind('<<ComboboxSelected>>',lambda e:self.change_plan_group())
        self.goal=ttk.Combobox(self.plan_tab,state='readonly',width=1);self.goal.pack(fill='x',pady=(6,4));self.goal.bind('<<ComboboxSelected>>',lambda e:self.refresh_plan())
        self.plan_summary=tk.StringVar();self.wrapped(self.plan_tab,textvariable=self.plan_summary).pack(fill='x',pady=4)
        footer=ttk.Frame(self.plan_tab);footer.pack(side='bottom',fill='x')
        self.wrapped(footer,text='各項加成比較；生效狀態不全時不顯示差額。').pack(fill='x')
        totals_frame=ttk.Frame(footer,height=116);totals_frame.pack(fill='x');totals_frame.pack_propagate(False)
        self.plan_totals=self.make_tree(totals_frame,['name','best','current','delta'],['屬性','建議','生效','差額'],[180,65,65,65],height=2)
        self.wrapped(footer,text='分數是加權指標；加成合計不代表實際產量。雙擊建議可在持有庫查看。').pack(fill='x',pady=4)
        self.plan_tree=self.make_tree(self.plan_tab,['slot','item','value'],['欄位','建議／狀態','最高值'],[65,225,85],height=5)
        self.plan_tree.bind('<Double-1>',self.open_plan_item)
    def build_inventory(self):
        bar=ttk.Frame(self.inventory_tab);bar.pack(fill='x',pady=(0,6))
        self.button(bar,'編輯／補正',self.edit_selected).pack(side='left',padx=(0,6))
        self.button(bar,'回收同步',self.delete_selected).pack(side='left',padx=(0,6))
        self.button(bar,'刪除紀錄',self.manual_delete).pack(side='left')
        row=ttk.Frame(self.inventory_tab);row.pack(fill='x',pady=(0,6))
        self.inventory_filter=ttk.Combobox(row,state='readonly',values=[self.t(v) for v in ('全部紀錄','待核對','已確認')],width=12)
        self.inventory_filter.current(0);self.inventory_filter.pack(side='left',fill='x',expand=True)
        self.inventory_group=ttk.Combobox(row,state='readonly',values=[self.t('全部組別')]+[self.t(g) for g in CATEGORY_GROUPS]+[self.t('自訂')],width=10);self.inventory_group.current(0);self.inventory_group.pack(side='left',padx=4);self.inventory_group.bind('<<ComboboxSelected>>',lambda e:self.fill_inventory())
        self.inventory_filter.bind('<<ComboboxSelected>>',lambda e:self.fill_inventory())
        self.show_inventory_detail=tk.BooleanVar(value=self.store.get_setting('inventory_detail',True))
        ttk.Checkbutton(row,text=self.t('詳情'),variable=self.show_inventory_detail,command=self.toggle_inventory_detail).pack(side='left',padx=6)
        self.inventory_summary=tk.StringVar();self.wrapped(self.inventory_tab,textvariable=self.inventory_summary).pack(fill='x',pady=(0,4))
        footer=ttk.Frame(self.inventory_tab);footer.pack(side='bottom',fill='x')
        self.recycle_text=tk.StringVar(value=self.t('單擊或上下鍵查看；Ctrl 多選檢查整批回收。'))
        self.wrapped(footer,textvariable=self.recycle_text).pack(fill='x',pady=5)
        actions=ttk.Frame(footer);actions.pack(fill='x')
        self.button(actions,'手動新增',self.manual_item).pack(side='left',padx=(0,5))
        self.button(actions,'整理誤讀',self.organize_pending).pack(side='left',padx=(0,5))
        self.button(actions,'用途影響',self.show_tradeoffs).pack(side='left')
        self.inventory_panes=ttk.Panedwindow(self.inventory_tab,orient='vertical');self.inventory_panes.pack(fill='both',expand=True)
        listing=ttk.Frame(self.inventory_panes);self.inventory_panes.add(listing,weight=1)
        self.inv_tree=self.make_tree(listing,['label','rank','state','quantity'],['藏品名稱／編號','品階／等級','判斷','件數'],[160,85,95,35],height=4)
        for tag,color in [('keep','#e8f3eb'),('multi','#e6edf8'),('tie','#eaf0f7'),('review','#fff0d7'),('candidate','#f3f4f3')]:self.inv_tree.tag_configure(tag,background=color)
        self.inv_tree.bind('<<TreeviewSelect>>',lambda e:self.inventory_selection())
        self.inv_tree.bind('<Double-1>',lambda e:self.inspect_inventory())
        self.inventory_detail_frame=ttk.Frame(self.inventory_panes,padding=(0,5,0,0))
        self.inventory_detail_heading=tk.StringVar(value=self.t('選取一件查看完整屬性'))
        self.wrapped(self.inventory_detail_frame,textvariable=self.inventory_detail_heading).pack(fill='x')
        self.inventory_stats=self.make_tree(self.inventory_detail_frame,['name','value'],['全部屬性','數值'],[290,85],height=3)
        if self.show_inventory_detail.get():self.inventory_panes.add(self.inventory_detail_frame,weight=1)
    def item_description(self,item):
        return self.t(item.label())+' · '+self.t(category_group(item.category))+'/'+self.t(item.category or '類別待核對')+' · '+self.t(item.quality or '品階未知')+(' Lv.'+item.level if item.level else '')+(' · '+item.power if item.power else '')
    def toggle_inventory_detail(self):
        self.store.setting('inventory_detail',self.show_inventory_detail.get())
        if self.show_inventory_detail.get():
            if str(self.inventory_detail_frame) not in self.inventory_panes.panes():self.inventory_panes.add(self.inventory_detail_frame,weight=1)
        elif str(self.inventory_detail_frame) in self.inventory_panes.panes():self.inventory_panes.forget(self.inventory_detail_frame)
    def inventory_selection(self):
        self.refresh_recycle();self.inspect_inventory()
    def inspect_inventory(self):
        selected=self.selected_records();self.inventory_stats.delete(*self.inventory_stats.get_children())
        if len(selected)!=1:
            self.inventory_detail_heading.set(self.t('選取一件查看完整屬性') if not selected else self.t('已多選；可檢查整批回收。'));return 'break'
        item=selected[0]
        self.inventory_detail_heading.set(self.item_description(item)+(self.t('；疑似重複 #')+f'{item.suspected_id:04d}' if item.suspected_id else ''))
        for stat in item.stats:self.inventory_stats.insert('','end',values=(self.t(stat.name),fmt(stat.value,stat.unit)))
        return 'break'
    def build_settings(self):
        capture=ttk.LabelFrame(self.settings_tab,text=self.t('畫面擷取'),padding=8);capture.pack(fill='x')
        self.button(capture,'選取／重選遊戲區域',self.select_region).pack(anchor='w')
        self.region_label=self.wrapped(capture,text='尚未選區');self.region_label.pack(fill='x',pady=4)
        ttk.Checkbutton(capture,text=self.t('助手重疊時自動移到選區旁'),variable=self.auto_dock,command=lambda:self.store.setting('auto_dock',self.auto_dock.get())).pack(anchor='w')
        ttk.Checkbutton(capture,text=self.t('視窗置頂'),variable=self.topmost,command=lambda:self.guard(self.save_topmost)).pack(anchor='w')
        ttk.Checkbutton(self.settings_tab,text=self.t('清楚且重複一致的資料自動收錄'),variable=self.autosave,command=lambda:self.store.setting('autosave',self.autosave.get())).pack(anchor='w',pady=7)
        row=ttk.Frame(self.settings_tab);row.pack(fill='x')
        ttk.Label(row,text=self.t('介面語言')).pack(side='left',padx=(0,8));lang=ttk.Combobox(row,state='readonly',values=['繁體中文','简体中文'],width=12)
        lang.current(1 if self.lang=='zh-CN' else 0);lang.pack(side='left');lang.bind('<<ComboboxSelected>>',lambda e:self.switch_language('zh-CN' if lang.current()==1 else 'zh-TW'))
        for pair in [[('匯入圖片',self.import_images),('診斷／辨識文字',self.show_scan_text)],[('匯出持有庫',self.export),('匯入持有庫',self.import_data)],[('屬性規則',self.show_definitions),('資料管理',self.database_manager),('重新整理',self.refresh)]]:
            row=ttk.Frame(self.settings_tab);row.pack(fill='x',pady=(8,0))
            for title,fn in pair:self.button(row,title,fn).pack(side='left',padx=(0,7))
        self.wrapped(self.settings_tab,text='擷取時會暫時隱藏重疊的助手視窗，不保存截圖。只在自己的藏品頁使用；遊戲移動或縮放後請重選區。').pack(fill='x',pady=10)
    def save_topmost(self):
        self.store.setting('topmost',self.topmost.get());self.root.attributes('-topmost',self.topmost.get())
    def switch_language(self,lang):
        if self.editor_open:raise ValueError(self.t('請先關閉核對視窗再切換語言'))
        for w in self.owned_windows[1:]:
            if w.winfo_exists():w.destroy()
        self.def_tree=None;self.lang=lang;self.store.setting('language',lang);self.build();self.refresh()
        if self.last_scan:self.display_scan(self.last_scan)
    def region_status(self):
        if not self.region:self.region_label.configure(text=self.t('尚未選取遊戲區域'));return
        r=self.region;self.region_label.configure(text=self.t('已選區：')+f"{r['width']} × {r['height']}"+self.t('；視窗移動或縮放後重選。'))
    def refresh(self):
        self.items=self.store.items();self.item_map={i.id:i for i in self.items};self.draft_records=self.store.drafts();self.draft_map={i.draft_id:i for i in self.draft_records};self.definitions=self.store.definitions();self.advisor=Advisor(self.items,self.definitions);self.known_categories={i.name:i.category for i in self.items if i.confirmed and i.name};self.region_status()
        self.fill_inventory()
        old=self.goal.get();self.goal_defs=[(n,u) for (n,u),d in self.definitions.items() if d and any(i.confirmed and any((s.name,s.unit)==(n,u) for s in i.stats) for i in self.items)]
        values=[self.t(n)+(' (%)' if u else self.t('（固定數量）')) for n,u in self.goal_defs];self.goal['values']=values
        if old in values:self.goal.set(old)
        elif values:self.goal.current(0)
        else:self.goal.set('')
        self.refresh_plan();self.refresh_definitions()
        self.status.set(self.t('持有 ')+str(len(self.items))+self.t(' 組；待核對 ')+str(sum(not i.confirmed for i in self.items)+len(self.draft_records))+self.t(' 組。排名僅依已收錄資料。'))
        if self.current:self.show_comparison(self.current)
    def fill_inventory(self):
        selection=self.inv_tree.selection();focus=self.inv_tree.focus();scroll=self.inv_tree.yview()
        self.inv_tree.delete(*self.inv_tree.get_children());mode=self.inventory_filter.current()
        groups=[None]+list(CATEGORY_GROUPS)+['自订'];group=groups[self.inventory_group.current()]
        def visible(i):return group is None or category_group(i.category)==group
        def rank(i):return self.t(i.quality or '—')+('/'+i.level if i.level else '')
        for item in self.items:
            if not visible(item):continue
            if mode==1 and item.confirmed:continue
            if mode==2 and not item.confirmed:continue
            state=self.advisor.verdict(item);self.inv_tree.insert('','end',iid=str(item.id),values=(self.t(item.label()),rank(item),self.t(STATES[state]),item.quantity),tags=(state,))
        if mode!=2:
            for item in self.draft_records:
                if not visible(item):continue
                self.inv_tree.insert('','end',iid='d:'+str(item.draft_id),values=(self.t(item.label()),rank(item),self.t('疑似重複' if item.suspected_id else '待核對草稿'),item.quantity),tags=('review',))
        remaining=[key for key in selection if self.inv_tree.exists(key)]
        if remaining:self.inv_tree.selection_set(remaining)
        if focus and self.inv_tree.exists(focus):self.inv_tree.focus(focus)
        if scroll:self.inv_tree.yview_moveto(scroll[0])
        counts=self.store.counts()
        self.inventory_summary.set(self.t('已確認持有 ')+str(counts['confirmed_quantity'])+self.t(' 件／')+str(counts['confirmed_groups'])+self.t(' 組；待核對 ')+str(counts['pending'])+self.t(' 筆（疑似重複 ')+str(counts['suspected'])+self.t('）。篩選顯示 ')+str(len(self.inv_tree.get_children()))+self.t(' 筆。'))
        self.inventory_selection()
    def selected_records(self):
        selected=[]
        for key in self.inv_tree.selection():
            item=self.draft_map.get(int(key[2:])) if key.startswith('d:') else self.item_map.get(int(key))
            if item is not None:selected.append(item)
        return selected
    def change_plan_group(self):
        self.store.setting('plan_group',self.plan_group.current());self.refresh_plan()
    def plan_scope(self):
        groups=list(CATEGORY_GROUPS)+['自订'];idx=self.plan_group.current()
        return None if idx==len(groups) else groups[idx]
    def change_plan_mode(self):
        self.store.setting('plan_mode',self.plan_mode.current());self.refresh_plan()
    def configure_goals(self):
        self.stop();win=self.own(tk.Toplevel(self.root));win.title(self.t('設定多用途'));win.geometry('490x500');win.transient(self.root)
        body=ttk.Frame(win,padding=10);body.pack(fill='both',expand=True)
        self.wrapped(body,text='選取百分比屬性並設定權重；1:1 表示等權合計，不保證兩項平均。固定數量使用單項專精。').pack(fill='x',pady=5)
        choices=[(n,u) for n,u in self.goal_defs if u=='%'];choice=ttk.Combobox(body,state='readonly',values=[self.t(n) for n,u in choices]);choice.pack(fill='x')
        if choices:choice.current(0)
        weight=tk.StringVar(value='1');row=ttk.Frame(body);row.pack(fill='x',pady=5)
        ttk.Label(row,text=self.t('權重')).pack(side='left');ttk.Entry(row,textvariable=weight,width=8).pack(side='left',padx=6)
        goals=[dict(v) for v in self.plan_goals]
        tree=self.make_tree(body,['name','weight'],['目標屬性','權重'],[315,90],height=6)
        def refill():
            tree.delete(*tree.get_children())
            for idx,g in enumerate(goals):tree.insert('','end',iid=str(idx),values=(self.t(g['name']),g['weight']))
        def add():
            idx=choice.current()
            if idx<0:raise ValueError(self.t('先收錄並核對百分比屬性'))
            from .core import number
            value=number(weight.get())
            if Decimal(value)<=0:raise ValueError(self.t('權重必須大於 0'))
            name,unit=choices[idx];goals[:]=[g for g in goals if (g['name'],g['unit'])!=(name,unit)];goals.append({'name':name,'unit':unit,'weight':value});refill()
        def remove():
            selected={int(i) for i in tree.selection()};goals[:]=[g for i,g in enumerate(goals) if i not in selected];refill()
        def save():
            self.advisor.weighted_plan([(g['name'],g['unit'],g['weight']) for g in goals])
            self.plan_goals=goals;self.store.setting('plan_goals',goals);self.plan_mode.current(1);self.change_plan_mode();win.destroy()
        self.button(row,'加入／更新',add).pack(side='left')
        controls=ttk.Frame(body);controls.pack(fill='x',pady=6)
        self.button(controls,'移除選取',remove).pack(side='left');self.button(controls,'儲存並分析',save).pack(side='right');refill()
    def refresh_plan(self):
        self.plan_tree.delete(*self.plan_tree.get_children());self.plan_totals.delete(*self.plan_totals.get_children());self.plan_ids={}
        scope=self.plan_scope();advisor=Advisor([i for i in self.items if scope is None or category_group(i.category)==scope],self.definitions)
        multi=self.plan_mode.current()==1;self.goal.configure(state='disabled' if multi else 'readonly')
        self.plan_tree.heading('value',text=self.t('分數' if multi else '最高值'))
        if multi:
            try:plan,totals,current,missing=advisor.weighted_plan([(g['name'],g['unit'],g['weight']) for g in self.plan_goals])
            except ValueError as e:self.plan_summary.set(self.convert(str(e)));return
            goals=[(g['name'],g['unit']) for g in self.plan_goals]
            caption='、'.join(self.t(g['name'])+' ×'+str(g['weight']) for g in self.plan_goals[:2])
            if len(self.plan_goals)>2:caption+=self.t('；共 ')+str(len(self.plan_goals))+self.t(' 項')
        else:
            idx=self.goal.current()
            if idx<0 or idx>=len(self.goal_defs):self.plan_summary.set(self.t('先收錄並核對藏品，再選擇目標屬性。'));return
            name,unit=self.goal_defs[idx];goals=[(name,unit)];plan=advisor.plan(name,unit)
            totals={(name,unit):sum((Decimal(v) for v,_ in plan.values()),Decimal(0))};current={(name,unit):sum((Decimal(s.value) for i in self.items if i.confirmed and i.equipped and i.category in plan for s in i.stats if (s.name,s.unit)==(name,unit)),Decimal(0))};caption=self.t(name)
        switches=0;missing=0
        for cat in self.store.categories():
            if scope is not None and category_group(cat)!=scope:continue
            if cat not in plan:self.plan_tree.insert('','end',values=(self.t(cat),self.t('尚無已核對加成'),'—'));missing+=1;continue
            value,winners=plan[cat];winner=winners[0];switches+=not winner.equipped
            label=' / '.join(f'#{i.id:04d}' for i in winners)+self.t(' · 已生效' if winner.equipped else ' · 需替換')
            rid=self.plan_tree.insert('','end',values=(self.t(cat),label,value if multi else fmt(value,goals[0][1])));self.plan_ids[rid]=winner.id
        known=bool(plan) and all(any(i.confirmed and i.equipped and i.category==cat for i in self.items) for cat in plan)
        for key in goals:
            value=totals[key];actual=current[key]
            self.plan_totals.insert('','end',values=(self.t(key[0]),fmt(value,key[1]) if plan else '—',fmt(actual,key[1]) if known else '—',fmt(value-actual,'pp' if key[1]=='%' else '',True) if known else '—'))
        self.plan_summary.set(caption+'\n'+self.t('需替換 ')+str(switches)+self.t(' 欄；無資料／無加成 ')+str(missing)+self.t(' 欄。'))
    def open_plan_item(self,event=None):
        sel=self.plan_tree.selection()
        if sel and sel[0] in self.plan_ids:
            key=str(self.plan_ids[sel[0]]);self.notebook.select(self.inventory_tab);self.inventory_filter.current(0);self.fill_inventory();self.inv_tree.selection_set(key);self.inv_tree.focus(key);self.inv_tree.see(key);self.inspect_inventory()
    def choose_scan(self,event=None):
        if self.editor_open:return
        idx=self.result_choice.current()
        if 0<=idx<len(self.pending):self.current=self.pending[idx];self.show_comparison(self.current)
    def show_comparison(self,item):
        matched=self.item_map.get(item.id) if item.id is not None and item.draft_id is None else next((i for i in self.items if i.fingerprint()==item.fingerprint()),None);compare_item=matched or item
        verdict=self.advisor.verdict(compare_item)
        self.item_heading.configure(text=self.item_description(compare_item))
        self.reuse_button.configure(state='normal' if item.suspected_id in self.item_map else 'disabled')
        self.verdict_label.configure(text=self.t(STATES[verdict]),foreground={'keep':'#245b39','tie':'#325980','candidate':'#48564b','review':'#8a560c','multi':'#325980'}[verdict])
        self.compare_tree.delete(*self.compare_tree.get_children());self.comparison_rows={}
        for row in self.advisor.compare(compare_item):
            rid=self.compare_tree.insert('','end',values=(self.t(row.stat.name),fmt(row.stat.value,row.stat.unit),fmt(row.best_other,row.stat.unit) if row.best_other is not None else '—'),tags=(row.state,));self.comparison_rows[rid]=row
        if item.observation_note:self.scan_notice.set(self.t(item.observation_note))
        elif compare_item.confirmed:self.scan_notice.set(self.t('本件已確認；可繼續辨識其他藏品。'))
        elif item.warnings:self.scan_notice.set(self.convert(item.warnings[0])+self.t('；可用編輯／補正處理。'))
        else:self.scan_notice.set(self.t('對照完整屬性後可按「確認本件」。'))
        self.comparison_detail.set(self.t('選取屬性可查看排名、差距與比較對象。'))
    def show_attribute_detail(self):
        selected=self.compare_tree.selection()
        if not selected or selected[0] not in self.comparison_rows:return
        r=self.comparison_rows[selected[0]];line=self.t(r.stat.name)+'：'+self.t(ROW_STATES[r.state])
        if r.best_other is not None:
            delta=Decimal(r.stat.value)-Decimal(r.best_other)
            line+=self.t('；差距 ')+fmt(delta,' 個百分點' if r.stat.unit=='%' else '',True)+self.t('；比較 ')+', '.join(f'#{i:04d}' for i in r.alternatives)
        self.comparison_detail.set(line)
    def display_scan(self,scan):
        previous_scan=self.last_scan;previous_index=self.result_choice.current()
        self.last_scan=scan;self.pending=scan.items
        labels=[]
        for idx,item in enumerate(scan.items):
            side=self.t('左／生效' if (item.screen_side or ('left' if idx==0 else 'right'))=='left' else '右／新候選') if scan.mode=='compare' else self.t('預覽')
            labels.append(side+' · '+self.t(item.name or item.category or '名稱待核對')+(' · '+item.power if item.power else ''))
        self.result_choice['values']=labels
        warnings=list(dict.fromkeys(scan.messages+[w for i in scan.items for w in i.warnings]))
        self.scan_notice.set(self.convert(warnings[0]) if warnings else self.t('全部屬性已配對；點選列可看比較依據。'))
        if labels:
            idx=preferred_preview(scan,previous_scan,previous_index)
            self.result_choice.current(idx);self.current=scan.items[idx];self.show_comparison(self.current)
        else:
            self.result_choice.set('');self.current=None;self.compare_tree.delete(*self.compare_tree.get_children());self.reuse_button.configure(state='disabled')
            self.verdict_label.configure(text=self.t('擷取受遮擋' if scan.mode=='blocked' else '尚無完整屬性'),foreground='#8a560c')
            self.item_heading.configure(text=self.t('請依下方原因調整'));self.comparison_detail.set(self.t('可到設定查看診斷與辨識文字。'))
    def open_item(self,item):
        self.notebook.select(self.scan_tab);self.current=item;self.scan_notice.set('');self.show_comparison(item)
    def edit_current(self):
        if self.current is None:raise ValueError(self.t('目前沒有可核對藏品；可到持有庫手動新增。'))
        item=self.draft_map.get(self.current.draft_id,self.current) if self.current.draft_id is not None else self.current
        self.show_editor(item)
    def reuse_existing(self):
        if self.editor_open:raise ValueError(self.t('請先關閉編輯視窗。'))
        if self.current is None or self.current.suspected_id is None or self.current.draft_id is None:raise ValueError(self.t('目前沒有可沿用的對應。'))
        item_id=self.store.accept_existing(self.current.draft_id,self.current.suspected_id)
        self.refresh();self.open_item(self.item_map[item_id]);self.status.set(self.t('已沿用既有資料，件數保持不變。'))
    def organize_pending(self):
        if self.editor_open:raise ValueError(self.t('請先關閉編輯視窗。'))
        self.pause_writes();count=self.store.organize_pending();self.refresh();self.status.set(self.t('已整理 ')+str(count)+self.t(' 筆疑似誤讀；請在待核對清單比對，資料未自動轉正。'))
    def confirm_current(self):
        if self.editor_open:raise ValueError(self.t('請先完成並關閉編輯視窗。'))
        if self.current is None:raise ValueError(self.t('目前沒有可確認的藏品；請先擷取藏品詳情。'))
        item_id=self.store.confirm_visible(self.current)
        self.refresh();self.open_item(self.item_map[item_id])
        self.status.set(self.t('已確認並保存 ')+self.t(self.item_map[item_id].label())+self.t('；持續辨識未中斷。' if self.monitoring else '。'))
    def edit_selected(self):
        sel=self.inv_tree.selection()
        if len(sel)!=1:raise ValueError(self.t('請選取一件要查看的藏品'))
        self.show_editor(self.selected_records()[0],from_inventory=True)
    def manual_item(self):self.show_editor(Item('','','','',[]),from_inventory=self.notebook.select()==str(self.inventory_tab))
    def toggle_editor(self,force=None):
        if force is False:self.close_editor()
        elif self.current:self.show_editor(self.current)
    def close_editor(self):
        if self.editor_open and getattr(self,'editor_from_inventory',False):self.current=self.editor_previous_current
        if self.editor_window and self.editor_window.winfo_exists():self.editor_window.destroy()
        self.editor_window=None;self.editor_open=False
    def show_editor(self,item,from_inventory=False):
        self.stop();self.close_editor();self.editor_previous_current=self.current;self.editor_from_inventory=from_inventory;self.editor_open=True;self.current=item;self.edit_id=item.id;self.edit_draft_id=item.draft_id
        matched=next((i for i in self.items if i.id==item.id),None)
        if matched:item.quantity=matched.quantity
        win=self.own(tk.Toplevel(self.root));self.editor_window=win;win.title(self.t('核對藏品資料'));win.geometry('560x710');win.minsize(520,660);win.geometry(f'+{max(0,self.root.winfo_x()+20)}+{max(0,self.root.winfo_y()+20)}');win.protocol('WM_DELETE_WINDOW',self.close_editor)
        body=ttk.Frame(win,padding=12);body.pack(fill='both',expand=True)
        all_warnings=list(dict.fromkeys(item.warnings))
        warnings='\n'.join(all_warnings[:2])+((self.t('\n另有 ')+str(len(all_warnings)-2)+self.t(' 項原因，可在診斷頁查看。')) if len(all_warnings)>2 else '') or self.t('核對類別、全部屬性及單位；名稱、品質、戰力可留空。')
        self.wrapped(body,text=warnings).pack(fill='x',pady=(0,8))
        self.fields={}
        row=ttk.Frame(body);row.pack(fill='x')
        for key,label,width in [('category','擺放類別',16),('quantity','件數',5)]:
            ttk.Label(row,text=self.t(label)).pack(side='left',padx=(0,6));v=tk.StringVar(value=self.t(item.category) if key=='category' else str(item.quantity));self.fields[key]=v
            widget=ttk.Combobox(row,textvariable=v,values=[self.t(c) for c in self.store.categories()],width=width) if key=='category' else ttk.Entry(row,textvariable=v,width=width)
            widget.pack(side='left',padx=(0,10))
        self.equipped=tk.BooleanVar(value=item.equipped);ttk.Checkbutton(body,text=self.t('目前生效中'),variable=self.equipped).pack(anchor='w',pady=5)
        self.stat_rows=StatRows(body,item.stats,self.definitions,self.t,self.guard);self.stat_rows.pack(fill='both',expand=True,pady=5)
        if item.suspected_id in self.item_map:self.button(body,'載入既有資料供比對',self.load_existing_editor).pack(anchor='w',pady=3)
        meta=ttk.LabelFrame(body,text=self.t('名稱與品階（未顯示可留空）'),padding=6);meta.pack(fill='x',pady=5)
        for rownum,key in enumerate(('name','quality','level','power')):
            label={'name':'藏品名稱','quality':'品階／品質','level':'等級','power':'戰力'}[key];ttk.Label(meta,text=self.t(label)).grid(row=rownum,column=0,sticky='w',padx=(0,8))
            v=tk.StringVar(value=self.t(getattr(item,key)));self.fields[key]=v
            widget=ttk.Combobox(meta,textvariable=v,values=[self.t(q) for q in ('普通','精良','稀有','史诗','传奇')]) if key=='quality' else ttk.Entry(meta,textvariable=v)
            widget.grid(row=rownum,column=1,sticky='ew',pady=2)
        meta.columnconfigure(1,weight=1)
        self.reviewed=tk.BooleanVar(value=False);ttk.Checkbutton(body,text=self.t('已核對完整屬性；新屬性以數值越高越好'),variable=self.reviewed).pack(anchor='w',pady=5)
        bar=ttk.Frame(body);bar.pack(fill='x')
        self.button(bar,'儲存已核對',self.save_reviewed).pack(side='left',padx=(0,6));self.button(bar,'暫存待核對',lambda:self.save_reviewed(False)).pack(side='left');self.button(bar,'關閉',self.close_editor).pack(side='right')
    def load_existing_editor(self):
        target=self.item_map.get(self.current.suspected_id) if self.current else None
        if target is None:raise ValueError(self.t('對應資料已移除。'))
        self.stat_rows.set_stats(target.stats)
        for key in ('category','name','quality','level','power'):self.fields[key].set(self.t(getattr(target,key)))
        self.fields['quantity'].set(str(target.quantity));self.equipped.set(target.equipped);self.edit_id=target.id
    def read_editor(self):
        stats=self.stat_rows.get_stats()
        power=self.fields['power'].get().strip()
        if power and not power.isdigit():raise ValueError(self.t('戰力請填入整數，或留空'))
        return Item(norm(self.fields['category'].get()),norm(self.fields['name'].get()),norm(self.fields['quality'].get()),power,stats,equipped=self.equipped.get(),source=self.current.source if self.current else 'manual',quantity=int(self.fields['quantity'].get()),level=norm(self.fields['level'].get()))
    def save_reviewed(self,confirmed=True):
        if confirmed and not self.reviewed.get():raise ValueError(self.t('請先核對完整屬性，再勾選核對完成。'))
        item=self.read_editor();item.id=self.edit_id
        raw=self.store.draft_text(self.edit_draft_id) if self.edit_draft_id is not None else self.last_scan.text if self.last_scan else ''
        if not confirmed:
            item.complete=False;item.source='manual-draft';item.warnings=[self.t('手動暫存，請核對完整屬性後確認。')]
            did=self.store.stage(item,raw,update_id=self.edit_draft_id)
            self.finish_editor(draft_id=did);self.status.set(self.t('已保存待核對草稿 ')+self.t(self.draft_map[did].label()));return
        if not item.category or not item.stats:raise ValueError(self.t('需要擺放類別與至少一項完整屬性'))
        for stat in item.stats:
            if self.definitions.get((stat.name,stat.unit),0)==0:self.store.define(stat.name,stat.unit,1)
        if self.edit_draft_id is not None:item_id=self.store.resolve_draft(self.edit_draft_id,item,True)
        else:
            matching=next((i for i in self.store.items() if i.fingerprint()==item.fingerprint()),None)
            item_id,_=self.store.save(item,confirmed=True,update_id=self.edit_id or (matching.id if matching else None))
        self.finish_editor(item_id=item_id);self.status.set(self.t('已確認並保存 ')+self.t(self.item_map[item_id].label()))
    def finish_editor(self,item_id=None,draft_id=None):
        inventory=self.editor_from_inventory;self.close_editor();self.refresh()
        item=self.draft_map[draft_id] if draft_id is not None else self.item_map[item_id]
        if inventory:
            self.inventory_filter.current(0);self.fill_inventory();key='d:'+str(draft_id) if draft_id is not None else str(item_id)
            self.inv_tree.selection_set(key);self.inv_tree.focus(key);self.inv_tree.see(key);self.inspect_inventory();self.notebook.select(self.inventory_tab)
        else:self.open_item(item)
    def show_scan_text(self):
        self.stop()
        if self.current is not None and self.current.draft_id is not None:
            raw=self.store.draft_text(self.current.draft_id)
            from .ocr import Scan
            self.last_scan=Scan('detail',[self.current],[],raw)
        if not self.last_scan:raise ValueError(self.t('尚無辨識結果；先擷取一次或匯入圖片。'))
        win=self.own(tk.Toplevel(self.root));win.title(self.t('診斷／辨識文字'));win.geometry('520x520')
        text=tk.Text(win,wrap='word',font=(self.font,10));text.pack(fill='both',expand=True)
        messages=self.last_scan.messages+[w for i in self.last_scan.items for w in i.warnings]
        content=self.t('診斷原因')+'\n'+'\n'.join(self.convert(m) for m in dict.fromkeys(messages))+'\n\n'+self.t('解析明細')+'\n'+json.dumps(self.last_scan.diagnostics,ensure_ascii=False,indent=2)+'\n\n'+self.t('辨識文字（未保存截圖）')+'\n'+self.last_scan.text
        text.insert('1.0',content);text.configure(state='disabled')
    def show_definitions(self):
        self.stop();win=self.own(tk.Toplevel(self.root));win.title(self.t('屬性比較規則'));win.geometry('580x500')
        body=ttk.Frame(win,padding=10);body.pack(fill='both',expand=True)
        self.wrapped(body,text='新屬性先標為待核對，確認比較方向後才參與配裝。').pack(fill='x',pady=5)
        self.def_tree=self.make_tree(body,['name','unit','direction'],['屬性','單位','規則'],[310,70,120],height=10)
        bar=ttk.Frame(body);bar.pack(fill='x',pady=8)
        for title,d in [('越高越好',1),('越低越好',-1),('待核對',0)]:self.button(bar,title,lambda d=d:self.set_direction(d)).pack(side='left',padx=(0,8))
        self.refresh_definitions()
    def refresh_definitions(self):
        if self.def_tree is None or not self.def_tree.winfo_exists():return
        self.def_tree.delete(*self.def_tree.get_children());self.def_rows=[]
        for (n,u),d in self.definitions.items():
            if not any(any((s.name,s.unit)==(n,u) for s in i.stats) for i in self.items+self.draft_records):continue
            rid=str(len(self.def_rows));self.def_rows.append((n,u));self.def_tree.insert('','end',iid=rid,values=(self.t(n),u or self.t('數量'),self.t({1:'越高越好',-1:'越低越好',0:'待核對'}[d])))
    def set_direction(self,direction):
        for row in self.def_tree.selection():
            n,u=self.def_rows[int(row)];self.store.define(n,u,direction)
        self.refresh()
    def selected_counts(self):return {i.id:i.quantity for i in self.selected_records() if i.draft_id is None}
    def show_tradeoffs(self):
        selected=self.selected_records()
        if not selected or any(i.draft_id is not None or not i.confirmed for i in selected):raise ValueError(self.t('請選取已確認藏品，再查看用途影響。'))
        if self.plan_mode.current()==1:goals=[(g['name'],g['unit'],g['weight']) for g in self.plan_goals]
        else:
            idx=self.goal.current();goals=[(*self.goal_defs[idx],1)] if 0<=idx<len(self.goal_defs) else []
        result=self.advisor.removal_impact({i.id:i.quantity for i in selected},goals,self.plan_scope())
        win=self.own(tk.Toplevel(self.root));win.title(self.t('用途影響（開啟時資料）'));win.geometry('680x500');win.transient(self.root)
        body=ttk.Frame(win,padding=12);body.pack(fill='both',expand=True)
        self.wrapped(body,text=self.t('假設移除選取的全部件數：')+'、'.join(self.t(i.label()) for i in selected[:3])+(self.t(' 等') if len(selected)>3 else '')).pack(fill='x',pady=5)
        score=result['score']
        caption=(self.t('目前用途範圍的分數：')+score['before']+' → '+score['after']+self.t('；減少 ')+score['loss']) if score else self.t('尚未設定分析用途；下表仍顯示所選欄位的各詞條影響。')
        self.wrapped(body,text=caption).pack(fill='x',pady=4)
        self.wrapped(body,text=self.t('保守判斷：')+'、'.join(dict.fromkeys(self.t(STATES[self.advisor.verdict(i)]) for i in selected))).pack(fill='x',pady=3)
        footer=ttk.Frame(body);footer.pack(side='bottom',fill='x')
        self.wrapped(footer,text='下表依欄位分列，不假定不同組別同時生效。「—」表示無可靠替代或規則未知；pp 為百分點。請依常用用途及可接受損失取捨。').pack(fill='x',pady=5)
        self.button(footer,'關閉',win.destroy).pack(anchor='e')
        tree=self.make_tree(body,['effect','before','after','loss'],['欄位／詞條','移除前最高','移除後最高','減少'],[340,90,90,85],height=8)
        for row in result['effects']:
            unit=row['unit'];display=lambda v:fmt(v,unit) if v is not None else '—'
            tree.insert('','end',values=(self.t(row['category'])+'／'+self.t(row['name']),display(row['before']),display(row['after']),fmt(row['loss'],'pp' if unit=='%' else '') if row['loss'] is not None else '—'))
    def refresh_recycle(self):
        selected=self.selected_records();draft_count=sum(i.draft_id is not None for i in selected)
        counts=self.selected_counts()
        if draft_count:
            self.recycle_text.set(self.t('選取包含 ')+str(draft_count)+self.t(' 筆待核對草稿，不能作為可回收依據；可用刪除紀錄清理。'));return
        if not counts:self.recycle_text.set(self.t('單擊或上下鍵查看；Ctrl 多選檢查整批回收。'));return
        risks=self.advisor.recycle_risks(counts);parts=[]
        for i,reasons in risks.items():
            if reasons:parts.append(f'#{i:04d}: '+self.convert('、'.join({'review':'資料待核對','equipped':'目前生效中','multi':'無單件完整替代／多用途保留'}.get(r,r) for r in reasons)))
        if parts:self.recycle_text.set(self.t('回收後可能失去最佳值／需核對：')+'；'.join(parts[:2])+(self.t('；另有 ')+str(len(parts)-2)+self.t(' 組提醒') if len(parts)>2 else ''))
        else:self.recycle_text.set(self.t('每件均有同欄位單件完整替代；仍須自行確認材料、交易等用途。'))
    def delete_selected(self):
        if any(i.draft_id is not None for i in self.selected_records()):raise ValueError(self.t('待核對草稿請使用刪除紀錄；請先核對物品身分再同步遊戲回收。'))
        counts=self.selected_counts()
        if not counts:raise ValueError(self.t('請先選取已在遊戲回收的紀錄'))
        self.stop();self.ignore_scans=True;risks=self.advisor.recycle_risks(counts)
        win=self.own(tk.Toplevel(self.root));win.title(self.t('同步遊戲回收結果'));win.geometry('510x530');win.transient(self.root);win.grab_set()
        body=ttk.Frame(win,padding=10);body.pack(fill='both',expand=True)
        self.wrapped(body,text='只移除已在遊戲成功回收的件數；0 表示不移除。').pack(fill='x',pady=4)
        tree=self.make_tree(body,['label','owned','recycled'],['藏品','持有','已回收件數'],[260,65,100],height=8)
        for i in counts:
            item=self.item_map[i];tree.insert('','end',iid=str(i),values=(self.t(item.label()),item.quantity,item.quantity))
        self.wrapped(body,text='雙擊一列可修改回收件數。回收後可能失去最佳值的藏品仍會提醒，但已回收的實際結果可以同步。').pack(fill='x',pady=6)
        def change(event=None):
            sel=tree.selection()
            if not sel:return
            from tkinter.simpledialog import askinteger
            i=int(sel[0]);n=askinteger(self.t('回收件數'),self.t(self.item_map[i].label()),initialvalue=int(tree.set(sel[0],'recycled')),minvalue=0,maxvalue=self.item_map[i].quantity,parent=win)
            if n is not None:tree.set(sel[0],'recycled',n)
        tree.bind('<Double-1>',change)
        checked=tk.BooleanVar();ttk.Checkbutton(body,text=self.t('我已確認遊戲回收完成，物品與件數正確'),variable=checked).pack(anchor='w',pady=5)
        def done():
            try:
                chosen={int(k):int(tree.set(k,'recycled')) for k in tree.get_children() if int(tree.set(k,'recycled'))>0}
                if not checked.get() or not chosen:raise ValueError(self.t('請確認回收完成並核對件數'))
                self.store.delete(chosen);self.current=None;self.pending=[];self.last_scan=None;self.result_choice.set('');self.result_choice['values']=[]
                self.compare_tree.delete(*self.compare_tree.get_children());self.verdict_label.configure(text=self.t('已同步刪除'));self.item_heading.configure(text=self.t('重新開始辨識可讀取下一件'))
                self.close_editor();self.refresh();win.destroy();self.status.set(self.t('已刪除回收紀錄並重算；舊的擷取結果不會加回。'))
            except Exception as e:messagebox.showerror(self.t('請核對'),str(e),parent=win)
        self.button(body,'刪除已回收紀錄',done).pack(anchor='w',pady=5)
        flagged=sum(bool(v) for v in risks.values())
        self.wrapped(body,text=self.t('選取 ')+str(len(counts))+self.t(' 組；需留意最佳值／狀態的紀錄：')+str(flagged)).pack(fill='x')
    def pause_writes(self):
        self.stop();self.ignore_scans=True
    def reset_preview(self):
        self.current=None;self.pending=[];self.last_scan=None;self.edit_id=None;self.edit_draft_id=None
        self.reuse_button.configure(state='disabled')
        self.result_choice.set('');self.result_choice['values']=[];self.compare_tree.delete(*self.compare_tree.get_children())
        self.item_heading.configure(text=self.t('等待遊戲藏品詳情'));self.verdict_label.configure(text=self.t('等待辨識'))
        self.scan_notice.set('');self.comparison_detail.set(self.t('資料變更後已重新計算；可重新開始辨識。'))
        self.recycle_text.set(self.t('選取紀錄可檢查回收，或手動刪除助手中的資料。'))
    def manual_delete(self):
        if self.editor_open:raise ValueError(self.t('請先完成核對並關閉核對視窗。'))
        selected=self.selected_records()
        if not selected:raise ValueError(self.t('請先選取要刪除的紀錄；可用 Ctrl 多選。'))
        self.pause_writes()
        names='、'.join(self.t(i.label()) for i in selected[:8])
        if len(selected)>8:names+=self.t(' 等 ')+str(len(selected))+self.t(' 組')
        prompt=self.t('將永久刪除以下助手紀錄並重新計算，遊戲內藏品不受影響：')+'\n\n'+names+'\n\n'+self.t('確定刪除？')
        if not messagebox.askyesno(self.t('刪除選取紀錄'),prompt,parent=self.root):return
        self.store.remove_records(item_ids=[i.id for i in selected if i.draft_id is None],draft_ids=[i.draft_id for i in selected if i.draft_id is not None])
        self.reset_preview();self.refresh();self.refresh_database_summary();self.status.set(self.t('已刪除選取紀錄並重新計算；重新掃描可再次收錄。'))
    def database_manager(self):
        if self.editor_open:raise ValueError(self.t('請先完成核對並關閉核對視窗。'))
        self.pause_writes();win=self.own(tk.Toplevel(self.root));win.title(self.t('資料管理'));win.geometry('490x390');win.transient(self.root);win.grab_set()
        body=ttk.Frame(win,padding=14);body.pack(fill='both',expand=True)
        self.database_summary=tk.StringVar();self.wrapped(body,textvariable=self.database_summary).pack(fill='x',pady=(0,12));self.refresh_database_summary()
        self.wrapped(body,text='清空持有庫會刪除藏品和草稿，編號從 0001 重起；保留語言、選區與屬性規則。').pack(fill='x',pady=5)
        self.button(body,'清空持有庫與待核對資料',lambda:self.clear_inventory(win)).pack(anchor='w',pady=7)
        ttk.Separator(body).pack(fill='x',pady=8)
        self.wrapped(body,text='重設資料庫會再清除自訂屬性、類別與擷取設定，回到初始狀態；不影響遊戲。').pack(fill='x',pady=5)
        self.button(body,'重設資料庫（含設定）',lambda:self.clear_inventory(win,reset=True)).pack(anchor='w',pady=7)
        self.wrapped(body,text='刪除不會自動保存舊資料副本；若要保留，請先在設定匯出持有庫。').pack(fill='x',pady=8)
        self.button(body,'關閉',win.destroy).pack(anchor='e')
    def refresh_database_summary(self):
        if not hasattr(self,'database_summary'):return
        self.database_summary.set(self.t('藏品紀錄 ')+str(len(self.store.items()))+self.t(' 組；待核對草稿 ')+str(len(self.store.drafts()))+self.t(' 組。'))
    def clear_inventory(self,parent=None,reset=False):
        from tkinter import simpledialog
        self.pause_writes()
        marker='RESET' if reset else 'CLEAR'
        prompt=self.t('此操作將清除全部藏品與待核對草稿')+self.t('，以及自訂規則、類別與設定。' if reset else '，保留目前設定。')+'\n'+self.t('正式紀錄與草稿編號均從 0001 重新開始。')+'\n'+self.t('資料不會自動備份。請輸入 ')+marker+self.t(' 確認：')
        answer=simpledialog.askstring(self.t('重設資料庫' if reset else '清空持有庫'),prompt,parent=parent or self.root)
        if answer is None:return
        if answer!=marker:raise ValueError(self.t('確認文字不符，沒有刪除任何資料。'))
        self.store.clear(reset_rules=reset,reset_settings=reset)
        if reset:
            self.region=None;self.plan_goals=[];self.plan_mode.current(0);self.plan_group.current(0);self.autosave.set(True);self.auto_dock.set(True);self.topmost.set(False);self.root.attributes('-topmost',False)
        self.reset_preview();self.refresh();self.refresh_database_summary()
        self.status.set(self.t('資料庫已重設，請重新選區；編號已歸零。' if reset else '藏品和草稿已清空，設定保留；編號已歸零。'))
    def ensure_idle(self):
        if self.worker and self.worker.is_alive():raise ValueError(self.t('目前正在辨識；請按停止並稍候完成。'))
        if self.editor_open:raise ValueError(self.t('請先完成核對並關閉核對視窗。'))
    def toggle_monitor(self):
        if self.monitoring:self.stop()
        else:self.start()
    def worker_ui(self):
        busy=bool(self.worker and self.worker.is_alive());self.start_button.configure(text=self.t('停止' if self.monitoring else '開始辨識'))
        self.run_label.set(self.t('監看中' if self.monitoring else '處理中' if busy else '已停止'))
    def launch(self,work):
        self.ensure_idle();self.cancel.clear();self.ignore_scans=False
        while not self.events.empty():
            try:
                old=self.events.get_nowait()
                if old[0]=='capture':old[1].cancelled=True;old[1].finish(error=RuntimeError('Capture cancelled'))
            except queue.Empty:break
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start();self.worker_ui()
    def category_map(self):return NAME_CATEGORIES|{i.name:i.category for i in self.store.items() if i.confirmed and i.name}
    def run_image(self,image,source,mapping):
        with self.engine_lock:lines=self.engine.recognize(image)
        result=parse(lines,(mapping|self.known_categories).get)
        for i in result.items:i.source=source if source=='import' else result.mode
        return result
    def capture_once(self):
        self.ensure_idle()
        if not self.region:self.notebook.select(self.settings_tab);raise ValueError(self.t('先在設定選取遊戲中央完整面板。'))
        region=dict(self.region);mapping=self.category_map()
        def work():
            try:
                image=request_frame(self.events,region,self.cancel);result=self.run_image(image,'capture',mapping)
                if not self.cancel.is_set():self.events.put(('scan',result,1))
            except Exception as e:
                if not self.cancel.is_set():self.events.put(('error',str(e)));self.log.exception('Capture failed')
            finally:self.events.put(('done',))
        self.launch(work);self.status.set(self.t('正在擷取；助手若重疊會先避開。'))
    def start(self):
        self.ensure_idle()
        if not self.region:self.notebook.select(self.settings_tab);raise ValueError(self.t('先在設定選取遊戲中央完整面板。'))
        region=dict(self.region);mapping=self.category_map()
        def work():
            try:
                from PIL import ImageChops,ImageStat
                previous=None;stable=0;seen='';repeat=0;finished=False
                while not self.cancel.is_set():
                    image=request_frame(self.events,region,self.cancel);thumb=image.resize((96,128)).convert('L')
                    diff=255 if previous is None else ImageStat.Stat(ImageChops.difference(thumb,previous)).mean[0];previous=thumb
                    if diff>.7:stable=0;finished=False;seen='';repeat=0
                    else:stable+=1
                    if stable>=1 and not finished:
                        result=self.run_image(image,'capture',mapping);signature='|'.join(i.fingerprint() for i in result.items)
                        repeat=repeat+1 if signature and signature==seen else 1;seen=signature
                        if not self.cancel.is_set():self.events.put(('scan',result,repeat))
                        if repeat>=2 or not result.items:finished=True
                    if self.cancel.wait(1):break
            except Exception as e:
                if not self.cancel.is_set():self.events.put(('error',str(e)));self.log.exception('Monitor failed')
            finally:self.events.put(('done',))
        self.monitoring=True
        try:self.launch(work)
        except Exception:self.monitoring=False;raise
        self.status.set(self.t('自動辨識中；可隨時按停止。'))
    def stop(self):
        self.cancel.set();self.monitoring=False
        if hasattr(self,'status'):self.status.set(self.t('已停止；若正在辨識，會等待本次工作結束。'))
        if hasattr(self,'start_button'):self.worker_ui()
    def _window_rect(self,window):
        return {'left':window.winfo_rootx()-10,'top':window.winfo_rooty()-32,'width':window.winfo_width()+20,'height':window.winfo_height()+42}
    def handle_capture(self,ticket):
        if self.closed or ticket.cancelled or self.cancel.is_set():ticket.finish(error=RuntimeError('Capture cancelled'));return
        if self.root.grab_current() is not None:ticket.finish(error=RuntimeError(self.t('請先關閉目前的確認對話框。')));return
        self.tickets.append(ticket);hidden=[];changed=False
        try:
            self.root.update_idletasks()
            if self.auto_dock.get() and self.root.state()=='normal' and overlaps(self._window_rect(self.root),ticket.region):
                screen={'left':0,'top':0,'width':self.root.winfo_screenwidth(),'height':self.root.winfo_screenheight()}
                rect=self._window_rect(self.root);pos=safe_position(ticket.region,rect['width'],rect['height'],screen)
                if pos:self.root.geometry(f'{pos[0]+10:+d}{pos[1]+32:+d}');self.root.update_idletasks();changed=True
            for win in list(self.owned_windows):
                if win.winfo_exists() and win.state()=='normal' and overlaps(self._window_rect(win),ticket.region):
                    hidden.append(win);win.withdraw();changed=True
        except Exception as e:
            for w in hidden:
                if w.winfo_exists():w.deiconify()
            self.tickets.remove(ticket);ticket.finish(error=e);return
        def grab():
            self.capture_afters.pop(ticket,None)
            image=None;error=None
            try:
                if ticket.cancelled or self.closed or self.cancel.is_set():raise RuntimeError('Capture cancelled')
                from PIL import Image
                import mss
                with mss.mss() as sct:
                    shot=sct.grab(ticket.region);image=Image.frombytes('RGB',shot.size,shot.rgb)
            except Exception as e:error=e
            finally:
                for win in hidden:
                    try:
                        if not self.closed and win.winfo_exists():win.deiconify()
                    except tk.TclError:pass
                if ticket in self.tickets:self.tickets.remove(ticket)
                ticket.finish(image,error)
        self.capture_afters[ticket]=self.root.after(180 if changed else 0,grab)
    def prepare_item(self,item):
        defs=self.store.definitions()
        for stat in item.stats:
            if (stat.name,stat.unit) not in defs:
                similar=[n for n,u in defs if u==stat.unit and n!=stat.name and SequenceMatcher(None,n,stat.name).ratio()>=.78]
                item.warnings.append('疑似辨識差異：'+stat.name+' → '+' / '.join(similar[:2]) if similar else '新屬性規則待核對：'+stat.name)
            elif defs[(stat.name,stat.unit)]==0:item.warnings.append('屬性規則待核對：'+stat.name)
    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event[0]=='capture':self.handle_capture(event[1])
                elif event[0]=='error':self.status.set(self.t('辨識失敗：')+event[1])
                elif event[0]=='done':self.monitoring=False;self.worker_ui()
                elif event[0]=='scan':
                    if self.ignore_scans:continue
                    scan,repeat=event[1:];saved=False
                    for item in scan.items:
                        self.prepare_item(item)
                        auto=repeat>=2 and self.autosave.get() and item.complete and not item.warnings
                        kind,record_id=self.store.observe(item,confirmed=bool(auto),raw_text=scan.text)
                        if kind=='item':item.id=record_id;item.draft_id=None
                        else:item.draft_id=record_id;item.id=None
                        saved=True
                    if saved:self.refresh()
                    if not self.editor_open:self.display_scan(scan)
                    if not scan.items:self.status.set(self.convert(scan.messages[0]) if scan.messages else self.t('尚無可配對屬性，未寫入持有庫。'))
                    else:
                        known={i.fingerprint() for i in self.items if i.confirmed}
                        confirmed=sum(i.id in self.item_map and self.item_map[i.id].confirmed for i in scan.items)
                        self.status.set(self.t('讀到 ')+str(len(scan.items))+self.t(' 件；已確認 ')+str(confirmed)+self.t(' 件，待核對 ')+str(len(scan.items)-confirmed)+self.t(' 件。'))
        except queue.Empty:pass
        except Exception as e:self.log.exception('Result handling failed');self.status.set(self.t('處理失敗：')+str(e))
        self.poll_after=self.root.after(100,self.poll)
    def close(self):
        self.cancel.set();self.closed=True
        for job in [self.poll_after,self.selection_after,*self.capture_afters.values()]:
            if job is not None:
                try:self.root.after_cancel(job)
                except tk.TclError:pass
        for ticket in self.tickets:ticket.cancelled=True;ticket.finish(error=RuntimeError('Application closed'))
        self.store.close();self.root.destroy()

    def seed_demo(self):
        samples=[('1036247',True,[('木材采集速度','16.26'),('降低太学院开发消耗','2.55'),('建造速度','1.62'),('食物产量','18.8'),('研究速度','1.81')]),
                 ('1004398',False,[('木材采集速度','16.82'),('降低太学院开发消耗','2.55'),('建造速度','2.05'),('降低所有建筑建造消耗','1.75'),('治疗速度','10.61')])]
        for power,equipped,stats in samples:
            self.store.save(Item('偶人','击鼓说唱陶俑','传奇',power,[Stat(n,v) for n,v in stats],equipped=equipped,source='demo'),confirmed=True)


    def export(self):
        self.ensure_idle()
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',initialfile='treasure-inventory.json')
        if not path:return
        payload={'format':'treasure-assistant','version':3,'attributes':[{'name':n,'unit':u,'direction':d} for (n,u),d in self.definitions.items()],
                 'items':[{'category':i.category,'name':i.name,'quality':i.quality,'level':i.level,'power':i.power,'equipped':i.equipped,'confirmed':i.confirmed,'quantity':i.quantity,'stats':[{'name':s.name,'value':s.value,'unit':s.unit} for s in i.stats]} for i in self.items],
                 'drafts':[{'category':i.category,'name':i.name,'quality':i.quality,'level':i.level,'power':i.power,'quantity':i.quantity,'warnings':i.warnings,'raw_text':self.store.draft_text(i.draft_id),
                    'stats':[{'name':a.name,'value':a.value,'unit':a.unit} for a in i.stats]} for i in self.store.drafts()]}
        Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        self.status.set(self.t('已匯出目前持有庫；分享程式時不必分享此檔。'))


    def import_data(self):
        self.ensure_idle()
        path=filedialog.askopenfilename(parent=self.root,filetypes=[('JSON','*.json')])
        if not path:return
        if Path(path).stat().st_size>10_000_000:raise ValueError(self.t('匯入檔案過大'))
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('format')!='treasure-assistant' or data.get('version') not in (1,2,3):raise ValueError(self.t('不支援的持有庫格式'))
        objects=[Item(v['category'],v['name'],v['quality'],str(v['power']),[Stat(**s) for s in v['stats']],equipped=bool(v.get('equipped')),source='import',quantity=int(v.get('quantity',1)),level=str(v.get('level',''))) for v in data['items']]
        draft_objects=[Item(v.get('category',''),v.get('name',''),v.get('quality',''),str(v.get('power','')),[Stat(**a) for a in v.get('stats',[])],source='import',quantity=int(v.get('quantity',1)),warnings=v.get('warnings',[])+['匯入資料待核對'],complete=False,level=str(v.get('level',''))) for v in data.get('drafts',[])]
        definitions=[(norm(d['name']),d['unit'],int(d['direction'])) for d in data.get('attributes',[])]
        if any(d not in (-1,0,1) or u not in ('','%') for n,u,d in definitions):raise ValueError(self.t('屬性規則格式錯誤'))
        if not messagebox.askyesno(self.t('匯入持有庫'),self.t('匯入會合併資料；相同資料不增加件數。匯入藏品先列為待核對，避免舊庫存誤判。'),parent=self.root):return
        self.store.backup(self.data/'before-import.sqlite3')
        for n,u,d in definitions:
            if (n,u) not in self.definitions:self.store.define(n,u,0)
        for item in objects:
            item.complete=False;item.warnings=['匯入資料待核對'];self.store.observe(item,confirmed=False)
        for item,raw in zip(draft_objects,data.get('drafts',[])):self.store.stage(item,raw.get('raw_text',''))
        self.refresh()

    def import_images(self):
        self.ensure_idle();paths=filedialog.askopenfilenames(parent=self.root,filetypes=[('Images','*.png *.jpg *.jpeg *.webp *.bmp')])
        if not paths:return
        mapping=self.category_map()
        def work():
            try:
                from PIL import Image
                for path in paths:
                    if self.cancel.is_set():break
                    with Image.open(path) as source:
                        image=source.convert('RGB');w,h=image.size
                        if w/h>1.5:image=image.crop((int(w*.33),0,int(w*.65),h))
                        result=self.run_image(image,'import',mapping)
                    if not self.cancel.is_set():self.events.put(('scan',result,1))
            except Exception as e:
                self.events.put(('error',str(e)));self.log.exception('Image import failed')
            finally:self.events.put(('done',))
        self.launch(work);self.notebook.select(self.scan_tab);self.status.set(self.t('正在辨識圖片，不保存原圖。'))
    def select_region(self):
        self.ensure_idle();hidden=[]
        for win in self.owned_windows:
            if win.winfo_exists() and win.state()=='normal':hidden.append(win);win.withdraw()
        def restore():
            for win in hidden:
                if win.winfo_exists():win.deiconify()
        def pick():
            try:
                from PIL import Image,ImageTk
                import mss
                with mss.mss() as sct:
                    monitor=sct.monitors[1];shot=sct.grab(monitor);image=Image.frombytes('RGB',shot.size,shot.rgb)
                win=tk.Toplevel(self.root);win.overrideredirect(True);win.geometry(f"{monitor['width']}x{monitor['height']}{monitor['left']:+d}{monitor['top']:+d}");win.attributes('-topmost',True)
                canvas=tk.Canvas(win,highlightthickness=0,cursor='crosshair');canvas.pack(fill='both',expand=True)
                photo=ImageTk.PhotoImage(image);canvas.create_image(0,0,image=photo,anchor='nw');canvas.photo=photo
                canvas.create_rectangle(10,10,min(1000,monitor['width']-10),64,fill='#202922',outline='')
                canvas.create_text(24,36,anchor='w',text=self.t('拖曳框選遊戲中央完整面板：類別、名稱、全部屬性。Esc 取消。'),fill='white',font=(self.font,13))
                start=[];rect=[]
                def down(e):
                    start[:]=[e.x,e.y]
                    if rect:canvas.delete(rect.pop())
                    rect.append(canvas.create_rectangle(e.x,e.y,e.x,e.y,outline='#45dc89',width=3))
                def move(e):
                    if start and rect:canvas.coords(rect[0],start[0],start[1],e.x,e.y)
                def finish(e):
                    if not start:return
                    x1,x2=sorted((start[0],e.x));y1,y2=sorted((start[1],e.y))
                    if x2-x1<180 or y2-y1<180:return
                    self.region={'left':monitor['left']+x1,'top':monitor['top']+y1,'width':x2-x1,'height':y2-y1}
                    self.store.setting('region',self.region);win.destroy();restore();self.region_status();self.status.set(self.t('選區完成；回到即時判斷按擷取一次或開始辨識。'))
                def escape(e):win.destroy();restore()
                canvas.bind('<ButtonPress-1>',down);canvas.bind('<B1-Motion>',move);canvas.bind('<ButtonRelease-1>',finish);win.bind('<Escape>',escape);win.focus_force()
            except Exception as e:restore();self.showerror(str(e))
        self.selection_after=self.root.after(200,pick)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lang',choices=['zh-TW','zh-CN']);parser.add_argument('--data-dir');parser.add_argument('--demo',action='store_true');args=parser.parse_args()
    if __import__('sys').platform=='win32':
        import ctypes
        try:ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:pass
    root=tk.Tk();App(root,args.lang,args.data_dir,args.demo);root.mainloop()
