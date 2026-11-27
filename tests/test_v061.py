"""Synthetic regressions for dim comparison titles, wrapped rows and Tcl grabs."""
import threading,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from treasure.ocr import Line,parse,preferred_preview
from treasure.capture import CaptureTicket,modal_blocks_capture
from treasure.app import App


def text(value,x,y,width=80,height=18,confidence=.99):
    return Line(value,confidence,x-width/2,y-height/2,x+width/2,y+height/2)


def comparison(right_quality='稀有',left_header='燕品属性',right_header='藏品犀性'):
    lines=[text('替换藏品',230,30),text('传奇测试左',115,100,140),text(right_quality+'测试右',350,100,140),
           text('100001',120,130),text('200002',355,130),text(left_header,115,160,60),text(right_header,350,160,60)]
    for j in range(5):
        y=190+j*30;lines.extend([text('效果'+str(j),75,y),text('+'+str(10+j)+'%',200,y,45)])
    right=['食物产量','兵营训练速度','食物采集速度'] if right_quality=='稀有' else ['训练骑兵资源消耗减少','训练步兵资源消耗减少']
    for j,label in enumerate(right):
        y=190+j*30
        if right_quality=='精良':
            # Every term wraps, with the gap to the next term almost as small as its own wrap.
            lines.extend([text(label[:-2],320,y-7,110),text(label[-2:],280,y+7,30,16)])
        else:lines.append(text(label,315,y,100))
        lines.append(text('+'+str(4+j)+'%',435,y,45))
    lines.append(text('穿戴',230,500))
    return lines


class ComparisonTitleTests(unittest.TestCase):
    def test_two_dim_titles_recover_both_sides_and_prefer_right(self):
        scan=parse(comparison(),lambda _: '偶人')
        self.assertEqual(scan.mode,'compare');self.assertEqual(len(scan.items),2)
        self.assertEqual([len(i.stats) for i in scan.items],[5,3]);self.assertEqual(preferred_preview(scan),1)
        self.assertEqual([i.screen_side for i in scan.items],['left','right'])
        self.assertTrue(all(h['recovered'] for h in scan.diagnostics['attribute_headers']))
    def test_one_exact_one_dim_title_remains_comparison(self):
        scan=parse(comparison(left_header='藏品属性'),lambda _: '偶人')
        self.assertEqual(scan.mode,'compare');self.assertEqual(len(scan.items),2)
        self.assertEqual([h['recovered'] for h in scan.diagnostics['attribute_headers']],[False,True])
    def test_fine_has_two_complete_separate_wrapped_terms(self):
        scan=parse(comparison('精良'),lambda _: '偶人');right=scan.items[1]
        self.assertEqual(right.quality,'精良');self.assertTrue(right.complete)
        self.assertEqual([(s.name,s.value) for s in right.stats],[('训练骑兵资源消耗减少','4'),('训练步兵资源消耗减少','5')])
    def test_low_confidence_wrapped_label_still_requires_review(self):
        lines=comparison('精良')
        for line in lines:
            if line.text=='减少':line.confidence=.7
        right=parse(lines,lambda _: '偶人').items[1]
        self.assertEqual(len(right.stats),2);self.assertTrue(any('不清楚' in w for w in right.warnings))
    def test_fuzzy_title_without_identity_does_not_create_panel(self):
        lines=[l for l in comparison() if not l.text.startswith(('传奇','稀有'))]
        self.assertFalse(parse(lines).items)
    def test_fuzzy_title_without_value_rows_does_not_create_panel(self):
        lines=[l for l in comparison() if not l.text.startswith('+')]
        self.assertFalse(parse(lines).items)
    def test_fuzzy_title_too_far_from_identity_is_rejected(self):
        lines=[text('传奇测试',100,30),text('燕品属性',100,500,60),text('+4%',200,530,45)]
        self.assertFalse(parse(lines).items)
    def test_multiple_wrong_glyphs_and_appraisal_header_not_accepted(self):
        for header in ('燕品犀性','属性','鉴定评分'):
            self.assertFalse(parse(comparison(left_header=header,right_header=header)).items)
    def test_single_detail_dim_title_with_context_is_supported(self):
        lines=[l for l in comparison(left_header='藏品犀性') if l.x<230 and l.text!='替换藏品']
        scan=parse(lines,lambda _: '偶人');self.assertEqual(scan.mode,'detail');self.assertEqual(len(scan.items[0].stats),5)


class NativeGrabTests(unittest.TestCase):
    def root(self,current='',kind='ComboboxPopdown'):
        root=SimpleNamespace(_w='.',tk=Mock());grabs=[current]
        def call(*args):
            if args[:2]==('grab','current'):return grabs[0]
            if args==('winfo','class',current):return kind
            if args==('winfo','parent',current):return '.choice'
            if args==('winfo','class','.choice'):return 'TCombobox'
            if args==('ttk::combobox::Unpost','.choice'):grabs[0]='';return ''
            raise AssertionError(args)
        root.tk.call.side_effect=call;return root
    def test_no_grab_can_capture(self):self.assertFalse(modal_blocks_capture(self.root()))
    def test_native_popdown_is_closed_without_python_widget_lookup(self):
        root=self.root('.choice.popdown');self.assertFalse(modal_blocks_capture(root))
        root.tk.call.assert_any_call('ttk::combobox::Unpost','.choice')
    def test_real_modal_is_kept_and_blocks_capture(self):
        root=self.root('.dialog','Toplevel');self.assertTrue(modal_blocks_capture(root))
        self.assertFalse(any(c.args[0]=='ttk::combobox::Unpost' for c in root.tk.call.call_args_list))
    def test_grab_errors_finish_ticket_instead_of_leaving_worker_waiting(self):
        app=App.__new__(App);app.closed=False;app.cancel=threading.Event();app.root=self.root()
        ticket=CaptureTicket({})
        with patch('treasure.app.modal_blocks_capture',side_effect=RuntimeError('Tcl grab failed')):app.handle_capture(ticket)
        self.assertTrue(ticket.done.is_set());self.assertIsInstance(ticket.error,RuntimeError)
    def test_modal_finishes_ticket_immediately_and_remains_open(self):
        app=App.__new__(App);app.closed=False;app.cancel=threading.Event();app.root=self.root('.dialog','Toplevel');app.t=lambda s:s
        ticket=CaptureTicket({});app.handle_capture(ticket)
        self.assertTrue(ticket.done.is_set());self.assertIn('確認對話框',str(ticket.error))

if __name__=='__main__':unittest.main()
