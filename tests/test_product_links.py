# -*- coding: utf-8 -*-
import unittest

from product_links import (MARKETPLACES, ProductLinkError, audit_manifest_links,
                            normalize_product, source_product_url)
from unittest.mock import Mock


class TestProductLinks(unittest.TestCase):
    def test_plain_asin_us_and_ca(self):
        self.assertEqual(normalize_product(' b0abcdef12 ', 'US'),
                         ('B0ABCDEF12', 'https://www.amazon.com/dp/B0ABCDEF12'))
        self.assertEqual(normalize_product('B0ABCDEF12', 'CA')[1],
                         'https://www.amazon.ca/dp/B0ABCDEF12')
        self.assertEqual(MARKETPLACES['CA'].currency_code, 'CAD')

    def test_url_and_formula_strip_tracking(self):
        url = 'https://www.amazon.com/gp/product/B0ABCDEF12/ref=x?tag=tracking'
        self.assertEqual(normalize_product(url, 'US')[1],
                         'https://www.amazon.com/dp/B0ABCDEF12')
        formula = '=HYPERLINK("https://www.amazon.ca/dp/B0ZZZZZZ99?tag=x","商品")'
        self.assertEqual(normalize_product(formula, 'CA')[0], 'B0ZZZZZZ99')

    def test_rich_link(self):
        value = [{'type': 'mention', 'text': '商品',
                  'link': 'https://amazon.ca/dp/B0ZZZZZZ99'}]
        self.assertEqual(normalize_product(value, 'CA')[0], 'B0ZZZZZZ99')

    def test_source_url_preserves_variant_query_after_validation(self):
        value = '=HYPERLINK("https://www.amazon.ca/dp/B0ZZZZZZ99?th=1&psc=1","商品")'
        self.assertEqual(
            source_product_url(value, 'CA', 'B0ZZZZZZ99'),
            'https://www.amazon.ca/dp/B0ZZZZZZ99?th=1&psc=1')

    def test_source_url_rejects_asin_mismatch(self):
        with self.assertRaises(ProductLinkError) as ctx:
            source_product_url('https://www.amazon.ca/dp/B0ZZZZZZ99?th=1',
                               'CA', 'B0ABCDEF12')
        self.assertEqual(ctx.exception.reason, 'asin_mismatch')

    def test_rejects_malicious_and_cross_marketplace(self):
        for value, reason in [
            ('https://amazon.com.evil.example/dp/B0ABCDEF12', 'invalid_domain'),
            ('https://www.amazon.com/dp/B0ABCDEF12', 'cross_marketplace'),
        ]:
            with self.assertRaises(ProductLinkError) as ctx:
                normalize_product(value, 'CA')
            self.assertEqual(ctx.exception.reason, reason)

    def test_explicit_bad_url_cannot_fallback_to_display_asin(self):
        value = {'link': 'https://evil.example/dp/B0ABCDEF12', 'text': 'B0ABCDEF12'}
        with self.assertRaises(ProductLinkError) as ctx:
            normalize_product(value, 'US')
        self.assertEqual(ctx.exception.reason, 'invalid_domain')

    def test_invalid_and_empty(self):
        for value, reason in [(None, 'empty'), ('not-an-asin', 'asin_not_found')]:
            with self.assertRaises(ProductLinkError) as ctx:
                normalize_product(value, 'US')
            self.assertEqual(ctx.exception.reason, reason)

    def test_audit_skips_non_product_labels_but_keeps_malformed_asin_invalid(self):
        fc = Mock()
        fc.read_values.return_value = [['B0ABCDEF12'], ['仓储费'], ['B0BAD']]
        manifest = {'period_id': 'seq-1', 'snapshot': {'spreadsheet_token': 'snap'},
                    'sheet_mappings': [{'source_sheet': 'PD03', 'source_sheet_id': 's1',
                                        'marketplace': 'US', 'header_row': 2, 'asin_col': 1}]}
        report = audit_manifest_links(fc, manifest)
        self.assertEqual(report['valid_count'], 1)
        self.assertEqual(report['skipped_non_product_count'], 1)
        self.assertEqual(report['invalid_count'], 1)


if __name__ == '__main__':
    unittest.main()
