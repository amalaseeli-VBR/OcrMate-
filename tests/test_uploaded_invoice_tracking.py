import ast
import hashlib
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import fitz
import uploaded_invoice_tracking as tracking
from docmate_v18.purchase_invoices import looks_like_dhamecha_invoice

ROOT = Path(__file__).resolve().parents[1]


def functions_from_file(filename, names, namespace):
    # Load pure functions without starting Streamlit, OCR services, or SQL.
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)
    return namespace


class UploadTrackingTests(unittest.TestCase):
    worker_file = "worker_uploadedfiles.py"
    app_file = "docmate_app_v1.19.001_r01_customer.py"

    def test_other_customer_cannot_load_invoice_lines(self):
        queue, db = MagicMock(), MagicMock()
        queue.cursor.return_value.fetchall.return_value = [(976, 'a')]
        db.cursor.return_value.fetchone.return_value = None
        self.assertEqual(tracking.load_invoices(queue, db, 10, 'InvoicesDB', 'OTHER'), [])
        db.cursor.return_value.fetchall.assert_not_called()

    def test_link_failure_does_not_commit_success(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = [None, RuntimeError('link failure')]
        ns = functions_from_file(self.worker_file, {'_update_uploadedfile_tracking'},
            dict(Any=Any, Dict=Dict, Optional=Optional, _upload_tracking=tracking))
        with self.assertRaisesRegex(RuntimeError, 'link failure'):
            ns['_update_uploadedfile_tracking'](conn, 10, 1, None, None, None, None, None, [])
        conn.commit.assert_not_called()

    def test_reload_all_invoices_uses_exact_fingerprints_and_customer(self):
        queue = MagicMock()
        queue.cursor.return_value.fetchall.return_value = [(976, 'a'), (978, 'b'), (976, 'a')]
        db = MagicMock()
        cur = db.cursor.return_value
        cur.description = [('doc_number',)]
        cur.fetchone.side_effect = [('65543',), ('65715',)]
        cur.fetchall.side_effect = [[('line A',)], [('line B',)]]
        result = tracking.load_invoices(queue, db, 10, 'InvoicesDB', 'CT002')
        self.assertEqual([r['header']['doc_number'] for r in result], ['65543', '65715'])
        self.assertEqual(result[1]['items'], [{'doc_number': 'line B'}])
        self.assertEqual(cur.execute.call_args_list[1].args[1], ('a', 'CT002'))
        self.assertEqual(cur.execute.call_args_list[3].args[1], ('b', 'CT002'))
        self.assertNotIn('BETWEEN', ' '.join(c.args[0] for c in cur.execute.call_args_list))

    def test_duplicate_save_still_returns_reference_without_inserting_lines(self):
        sql = MagicMock()
        sql.insert_invoice_header_sqlserver.return_value = (True, 'duplicate')
        sql.get_invoice_header_id.return_value = 976
        sql.get_invoice_line_id_range.return_value = (61522, 61569)
        ns = functions_from_file(self.worker_file, {'_save_worker_result'},
            dict(Any=Any, Dict=Dict, Optional=Optional, Tuple=Tuple,
                 hashlib=hashlib, json=json, _sqlserver=sql,
                 _derive_uploadedfile_doc_refs=lambda *a, **k: ('65543', '2026-09-01')))
        app = SimpleNamespace(_uk_now_iso=lambda: 'now', _normalise_supplier_name=lambda s: s,
                              _invoice_line_content=lambda items: items)
        ref = {}
        result = ns['_save_worker_result'](app, doc_type='Purchase Invoice',
            header={'invoiceNumber': '65543'}, items=[{'code': '123'}], metrics={},
            cfg_save={'database': 'InvoicesDB'}, cms_customer_id='CT002',
            source_file_name='merged.pdf', ocr_file_name='65543.pdf', saved_reference=ref)
        self.assertTrue(result[0])
        self.assertEqual(ref['header_id'], 976)
        self.assertEqual(ref['invoice_number'], '65543')
        sql.insert_invoice_lines_sqlserver.assert_not_called()

    def test_links_and_status_commit_together(self):
        conn = MagicMock()
        ns = functions_from_file(self.worker_file, {'_update_uploadedfile_tracking'},
            dict(Any=Any, Dict=Dict, Optional=Optional, _upload_tracking=tracking))
        ref = dict(database='InvoicesDB', doc_type='Purchase Invoice', header_id=976,
                   fingerprint='a', invoice_number='65543', invoice_date='2026-09-01')
        ns['_update_uploadedfile_tracking'](conn, 10, 1, None, None, None, '65543, 65715', None, [ref])
        calls = conn.cursor.return_value.execute.call_args_list
        self.assertIn('UPDATE dbo.UploadedFiles', calls[0].args[0])
        self.assertIn('DELETE FROM dbo.UploadedFileInvoices', calls[1].args[0])
        self.assertIn('INSERT INTO dbo.UploadedFileInvoices', calls[2].args[0])
        conn.commit.assert_called_once()

    def test_actual_merged_pdf_splits_each_invoice(self):
        ns = functions_from_file(self.app_file,
            {'_safe_filename', '_looks_like_dhamecha_invoice', '_pdf_page_text_single',
             '_split_pdf_self_contained_invoices'},
            dict(Any=Any, Dict=Dict, List=List, fitz=fitz, os=os, re=re,
                 _looks_like_dhamecha_invoice=looks_like_dhamecha_invoice,
                 _resolve_pdftotext_exe=lambda: None))
        path = ROOT / 'Invoices/Dhamecha/CT002_2026_09_07_10_16_31_707.pdf'
        if not path.exists():
            self.skipTest('Local invoice fixture is unavailable')
        docs = ns['_split_pdf_self_contained_invoices'](
            dict(name=path.name, mime='application/pdf', bytes=path.read_bytes()))
        numbers = [d['detected_invoice_number'] for d in docs]
        self.assertIn('65543', numbers)
        self.assertIn('65715', numbers)
        self.assertGreater(len(docs), 1)
        self.assertEqual(len(numbers), len(set(numbers)))
        print('Merged PDF invoice segments:', [(d['detected_invoice_number'], d['source_pages']) for d in docs])


class RootV18UploadTrackingTests(UploadTrackingTests):
    app_file = "docmate_app_v1.18.001_r01_customer.py"


class StandaloneV18UploadTrackingTests(UploadTrackingTests):
    app_file = "docmate_v18/docmate_app_v1.18.001_r01_customer.py"
    worker_file = "docmate_v18/worker_uploadedfiles.py"


if __name__ == "__main__":
    unittest.main()
