"""One-off stock audit PDF for Matoshree store. Does not modify app code."""
import os
import sys
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not ROOT.endswith('mac2'):
    ROOT = r'C:\Users\win10\Downloads\mac2 (2)\mac2'
sys.path.insert(0, ROOT)

from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from fpdf import FPDF

DB = os.path.join(
    ROOT, 'config', 'stores',
    'Store_Matoshree_Medical_Veterinary_Pet_Shop', 'veterinary.db',
)
OUT = os.path.join(ROOT, 'Matoshree_Stock_Audit_Report.pdf')


def tps_for(mtype, unit):
    if is_strip_count_type(mtype or ''):
        t = parse_tablets_per_stripe(unit or '1')
        return t if t > 0 else 1
    return 1


def main():
    c = sqlite3.connect(DB)

    def purchase_lines(mid):
        return c.execute(
            '''
            SELECT COALESCE(p.purchase_no,''), COALESCE(p.bill_number,''),
                   p.purchase_date, COALESCE(pi.qty,0), COALESCE(pi.free_qty,0),
                   COALESCE(pi.batch_no,'')
            FROM purchase_items pi
            JOIN purchases p ON p.id=pi.purchase_id
            WHERE pi.medicine_id=? AND COALESCE(pi.deleted,0)=0
              AND COALESCE(p.deleted,0)=0
            ORDER BY p.purchase_date, p.id
            ''',
            (mid,),
        ).fetchall()

    def sale_lines(mid):
        return c.execute(
            '''
            SELECT COALESCE(s.bill_no,''), s.bill_date, COALESCE(si.qty,0)
            FROM sales_items si
            JOIN sales s ON s.id=si.sale_id
            WHERE si.medicine_id=? AND COALESCE(si.deleted,0)=0
              AND COALESCE(s.deleted,0)=0
            ORDER BY s.bill_date, s.id
            ''',
            (mid,),
        ).fetchall()

    problems = []
    meds = c.execute(
        '''
        SELECT id, name, type, COALESCE(unit,''), COALESCE(batch_no,''),
               COALESCE(stock_qty,0)
        FROM medicines WHERE COALESCE(deleted,0)=0
        '''
    ).fetchall()

    for mid, name, mtype, unit, batch, stock in meds:
        tps = tps_for(mtype, unit)
        purch_strips = float(
            c.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(qty,0)+COALESCE(free_qty,0)),0)
                FROM purchase_items
                WHERE medicine_id=? AND COALESCE(deleted,0)=0
                ''',
                (mid,),
            ).fetchone()[0]
        )
        sold = float(
            c.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(qty,0)),0) FROM sales_items
                WHERE medicine_id=? AND COALESCE(deleted,0)=0
                ''',
                (mid,),
            ).fetchone()[0]
        )
        sret = float(
            c.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(qty,0)),0) FROM sales_return_items
                WHERE medicine_id=? AND COALESCE(deleted,0)=0
                ''',
                (mid,),
            ).fetchone()[0]
        )
        pret = float(
            c.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(qty,0)),0) FROM purchase_return_items
                WHERE medicine_id=? AND COALESCE(deleted,0)=0
                ''',
                (mid,),
            ).fetchone()[0]
        )
        disp = float(
            c.execute(
                '''
                SELECT COALESCE(SUM(COALESCE(quantity,0)),0)
                FROM stock_disposals WHERE medicine_id=?
                ''',
                (mid,),
            ).fetchone()[0]
        )
        purch_stock = purch_strips * tps
        expected = purch_stock - sold + sret - pret * tps - disp
        if purch_stock == 0 and sold == 0 and float(stock) == 0:
            continue
        diff = float(stock) - expected
        if abs(diff) <= 0.01:
            continue
        never_reduced = sold > 0 and abs(float(stock) - purch_stock) <= 0.01
        problems.append(
            {
                'id': mid,
                'name': name,
                'type': mtype or '',
                'unit': unit,
                'batch': batch or '',
                'stock': float(stock),
                'purch_stock': purch_stock,
                'purch_qty': purch_strips,
                'sold': sold,
                'expected': expected,
                'diff': diff,
                'tps': tps,
                'never_reduced': never_reduced,
                'purchases': purchase_lines(mid),
                'sales': sale_lines(mid),
            }
        )

    problems.sort(
        key=lambda x: (-int(x['never_reduced']), -abs(x['diff']), x['name'])
    )

    class PDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 11)
            self.cell(
                0,
                6,
                'Stock Audit Report - Matoshree Medical Veterinary Pet Shop',
                ln=1,
            )
            self.set_font('Helvetica', '', 8)
            self.cell(
                0,
                5,
                'Generated: %s | Source: Drive-synced store database'
                % datetime.now().strftime('%Y-%m-%d %H:%M'),
                ln=1,
            )
            self.ln(2)

        def footer(self):
            self.set_y(-10)
            self.set_font('Helvetica', 'I', 8)
            self.cell(0, 8, 'Page %s' % self.page_no(), align='C')

    pdf = PDF(orientation='L', format='A4')
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font('Helvetica', '', 9)
    pdf.multi_cell(
        0,
        5,
        'Method: Expected stock = (Purchase qty + free) x tablets-per-strip '
        'for Tablet/Bolus/Capsule (else purchase qty) - Sales qty + sales returns '
        '- purchase returns - disposals. Sales qty is as stored in sales_items '
        '(same units the app uses when deducting stock). '
        'No application code was changed for this report.',
    )
    pdf.ln(2)
    never_n = sum(1 for p in problems if p['never_reduced'])
    high_n = sum(1 for p in problems if p['diff'] > 0.01)
    low_n = sum(1 for p in problems if p['diff'] < -0.01)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(
        0,
        6,
        'Summary: %d medicines with stock mismatch | %d look like sales did NOT '
        'reduce stock | %d stock too high | %d stock too low'
        % (len(problems), never_n, high_n, low_n),
        ln=1,
    )
    pdf.ln(3)

    cols = [
        ('Medicine', 55),
        ('Batch', 28),
        ('Type', 18),
        ('Stock', 16),
        ('From Purchase', 22),
        ('Sold', 14),
        ('Should Be', 18),
        ('Diff', 14),
        ('Issue', 40),
    ]
    pdf.set_font('Helvetica', 'B', 8)
    for title, w in cols:
        pdf.cell(w, 6, title, border=1)
    pdf.ln()
    pdf.set_font('Helvetica', '', 7)
    for p in problems:
        issue = (
            'Sale not deducted from stock'
            if p['never_reduced']
            else (
                'Stock higher than expected'
                if p['diff'] > 0
                else 'Stock lower than expected'
            )
        )
        vals = [
            (p['name'][:34], 55),
            ((p['batch'][:16] or '-'), 28),
            (p['type'][:12], 18),
            ('%.0f' % p['stock'], 16),
            ('%.0f' % p['purch_stock'], 22),
            ('%.0f' % p['sold'], 14),
            ('%.0f' % p['expected'], 18),
            ('%+.0f' % p['diff'], 14),
            (issue[:28], 40),
        ]
        if pdf.get_y() > 190:
            pdf.add_page()
            pdf.set_font('Helvetica', 'B', 8)
            for title, w in cols:
                pdf.cell(w, 6, title, border=1)
            pdf.ln()
            pdf.set_font('Helvetica', '', 7)
        for text, w in vals:
            pdf.cell(w, 5, text, border=1)
        pdf.ln()

    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 7, 'Detailed lines (Purchase serial / Sale bill)', ln=1)
    pdf.ln(1)

    for p in problems:
        if pdf.get_y() > 170:
            pdf.add_page()
        pdf.set_x(pdf.l_margin)
        pdf.set_font('Helvetica', 'B', 9)
        title = '%s | Batch: %s | Type: %s | Unit/TPS: %s (x%d)' % (
            p['name'],
            p['batch'] or '-',
            p['type'],
            p['unit'] or '-',
            p['tps'],
        )
        pdf.multi_cell(0, 5, title.encode('latin-1', 'replace').decode('latin-1'))
        pdf.set_x(pdf.l_margin)
        pdf.set_font('Helvetica', '', 8)
        body = (
            'Current stock: %.0f | From purchases: %.0f '
            '(raw purchase qty %.0f) | Sold: %.0f | '
            'Should be: %.0f | Difference: %+.0f'
            % (
                p['stock'],
                p['purch_stock'],
                p['purch_qty'],
                p['sold'],
                p['expected'],
                p['diff'],
            )
        )
        pdf.multi_cell(0, 4, body)
        pdf.set_x(pdf.l_margin)
        if p['never_reduced']:
            pdf.set_text_color(180, 0, 0)
            pdf.multi_cell(
                0,
                4,
                'FLAG: Stock still equals purchased quantity after sales - '
                'sales likely did not update stock.',
            )
            pdf.set_text_color(0, 0, 0)

        pdf.set_x(pdf.l_margin)
        pdf.set_font('Helvetica', 'B', 8)
        pdf.cell(
            0,
            5,
            'Purchases (serial / bill / date / qty+free / batch):',
            new_x='LMARGIN',
            new_y='NEXT',
        )
        pdf.set_font('Helvetica', '', 7)
        if not p['purchases']:
            pdf.cell(
                0,
                4,
                '  (no purchase lines linked to this medicine id)',
                new_x='LMARGIN',
                new_y='NEXT',
            )
        for pn, bill, dt, qty, free, b in p['purchases']:
            line = (
                '  Serial %s | Bill %s | %s | qty %g+%g | batch %s'
                % (pn or '-', bill or '-', dt or '-', qty, free, b or '-')
            )
            pdf.set_x(pdf.l_margin)
            pdf.cell(
                0,
                4,
                line.encode('latin-1', 'replace').decode('latin-1'),
                new_x='LMARGIN',
                new_y='NEXT',
            )

        pdf.set_x(pdf.l_margin)
        pdf.set_font('Helvetica', 'B', 8)
        pdf.cell(
            0,
            5,
            'Sales (bill no / date / qty):',
            new_x='LMARGIN',
            new_y='NEXT',
        )
        pdf.set_font('Helvetica', '', 7)
        pdf.set_x(pdf.l_margin)
        if not p['sales']:
            pdf.cell(0, 4, '  (no sales)', new_x='LMARGIN', new_y='NEXT')
        else:
            parts = [
                '%s (%s, qty %g)' % (bn, dt, q) for bn, dt, q in p['sales']
            ]
            pdf.multi_cell(
                0,
                4,
                ('  ' + ' | '.join(parts))
                .encode('latin-1', 'replace')
                .decode('latin-1'),
            )
        pdf.ln(2)

    pdf.output(OUT)
    print('Wrote', OUT)
    print('problems', len(problems), 'never_reduced', never_n)


if __name__ == '__main__':
    main()
