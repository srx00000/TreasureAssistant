"""Small row editor for lossless item attributes; no OCR or database side effects."""
import tkinter as tk
from tkinter import ttk
from decimal import InvalidOperation
from .core import Stat,norm,ATTRIBUTE_CATALOG

class StatRows(ttk.Frame):
    def __init__(self,parent,stats,translate,guard):
        super().__init__(parent);self.t=translate;self.guard=guard
        self.names=sorted({n for n,u in ATTRIBUTE_CATALOG})
        controls=ttk.Frame(self);controls.pack(side='bottom',fill='x')
        self.name=tk.StringVar();self.value=tk.StringVar()
        self.name_box=ttk.Combobox(controls,textvariable=self.name,values=[self.t(n) for n in self.names]);self.name_box.pack(fill='x',pady=(4,3))
        self.name_box.bind('<KeyRelease>',self.filter_names)
        row=ttk.Frame(controls);row.pack(fill='x')
        ttk.Label(row,text=self.t('數值')).pack(side='left');ttk.Entry(row,textvariable=self.value,width=12).pack(side='left',padx=5)
        self.unit=ttk.Combobox(row,state='readonly',values=['%',self.t('固定數量')],width=10);self.unit.current(0);self.unit.pack(side='left',padx=4)
        bar=ttk.Frame(controls);bar.pack(fill='x',pady=4)
        for text,action in [('新增詞條',self.add),('更新選取',self.update_row),('刪除選取',self.remove)]:
            ttk.Button(bar,text=self.t(text),command=lambda fn=action:self.guard(fn)).pack(side='left',padx=(0,5))
        frame=ttk.Frame(self);frame.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(frame,columns=('name','value','unit'),show='headings',height=4,selectmode='extended')
        for col,text,width in [('name','詞條名稱',280),('value','數值',80),('unit','單位',75)]:
            self.tree.heading(col,text=self.t(text));self.tree.column(col,width=width,minwidth=45)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(side='left',fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.select);self.tree.bind('<Double-1>',lambda e:self.name_box.focus_set())
        self.set_stats(stats)
    def filter_names(self,event=None):
        query=norm(self.name.get());self.name_box['values']=[self.t(n) for n in self.names if query in norm(n)]
    def read_input(self):
        if not self.name.get().strip() or not self.value.get().strip():raise ValueError(self.t('請填寫詞條名稱與數值。'))
        unit='%' if self.unit.current()==0 else '';value=self.value.get().strip().lstrip('+')
        if unit=='%' and value.endswith('%'):value=value[:-1]
        try:return Stat(self.name.get(),value,unit)
        except (ValueError,InvalidOperation):raise ValueError(self.t('數值請填有效數字；百分比與固定數量在單位欄選擇。')) from None
    def values(self,stat):return self.t(stat.name),stat.value,stat.unit
    def validate(self,stat,except_row=None):
        if any(norm(self.tree.set(k,'name'))==stat.name and self.tree.set(k,'unit')==stat.unit for k in self.tree.get_children() if k!=except_row):
            raise ValueError(self.t('同名同單位詞條已存在，請選取後更新。'))
    def add(self):
        stat=self.read_input();self.validate(stat);key=self.tree.insert('','end',values=self.values(stat));self.tree.selection_set(key);self.tree.see(key)
    def update_row(self):
        rows=self.tree.selection()
        if len(rows)!=1:raise ValueError(self.t('請選取一項詞條再更新。'))
        stat=self.read_input();self.validate(stat,rows[0]);self.tree.item(rows[0],values=self.values(stat))
    def remove(self):
        self.tree.delete(*self.tree.selection());self.name.set('');self.value.set('')
    def select(self,event=None):
        rows=self.tree.selection()
        if len(rows)!=1:return
        key=rows[0];self.name.set(self.tree.set(key,'name'));self.value.set(self.tree.set(key,'value'));self.unit.current(0 if self.tree.set(key,'unit')=='%' else 1)
    def set_stats(self,stats):
        self.name.set('');self.value.set('')
        self.tree.delete(*self.tree.get_children())
        for stat in stats:self.tree.insert('','end',values=self.values(stat))
    def get_stats(self):
        if self.name.get().strip() or self.value.get().strip():
            if len(self.tree.selection())==1:self.update_row()
            elif not self.tree.selection():self.add()
        return [Stat(self.tree.set(k,'name'),self.tree.set(k,'value'),self.tree.set(k,'unit')) for k in self.tree.get_children()]
