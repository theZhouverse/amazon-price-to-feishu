import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from frontend_checks import (  # noqa: E402
    CHECK_KEYS,
    FRONTEND_CHECK_RULE_VERSION,
    frontend_columns,
    frontend_status_counts,
    inspect_frontend,
    normalize_dimension,
)


class FrontendChecksTest(unittest.TestCase):
    def test_same_snapshot_produces_seven_evidence_rich_values(self):
        html = '''
        <div id="imageBlock_feature_div"><img id="landingImage" src="main.jpg"></div>
        <div id="aplusBrandStory_feature_div"><h2>From the brand</h2><div class="apm-brand-story-carousel"><img src="brand.jpg"></div></div>
        <div id="inline-twister-expander-header-size_name" aria-label="Selected Size is 8 x 10 ft. Tap to collapse.">
          <span id="inline-twister-expanded-dimension-text-size_name">8 × 10 ft</span>
        </div>
        <div id="inline-twister-expander-content-size_name">
          <li class="inline-twister-swatch" data-asin="B000000002"></li>
        </div>
        <div id="prodDetails" data-csa-c-asin="B000000001"><table class="prodDetTable"><tr>
          <th class="prodDetSectionEntry">Best Sellers Rank</th><td>#1 in Patio</td>
        </tr></table></div>
        <div id="climatePledgeFriendlyATF_feature_div" data-csa-c-asin="B000000001">
          <div id="climatePledgeFriendlyBadge">
            <span id="CPF-ATF-Card">
              <a class="climatePledgeFriendlyATF"><img src="eco.svg"></a>
              <span class="climatePledgeFriendlyProgramName">1 sustainability feature</span>
            </span>
          </div>
        </div>
        '''
        checks = inspect_frontend(html, '8 x 10 ft', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')

        self.assertEqual(set(checks), set(CHECK_KEYS))
        self.assertEqual(checks['product_image']['status'], 'pass')
        self.assertEqual(checks['brand_story_image']['status'], 'pass')
        self.assertEqual(checks['size_consistent']['status'], 'pass')
        self.assertEqual(checks['bsr_badge']['status'], 'pass')
        self.assertEqual(checks['amazon_choice_badge']['status'], 'fail')
        self.assertEqual(checks['parent_child_asin']['status'], 'pass')
        self.assertEqual(checks['eco_badge']['status'], 'pass')
        self.assertEqual(checks['size_consistent']['rule_version'], FRONTEND_CHECK_RULE_VERSION)
        self.assertEqual(checks['size_consistent']['expected'], '8x10 ft')
        self.assertNotEqual(checks['product_image']['evidence_locator'], '')
        self.assertNotEqual(checks['brand_story_image']['evidence_locator'], '')

    def test_capture_timestamp_is_retained_for_all_checks(self):
        checks = inspect_frontend(
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>',
            '', 'B000000001', page_url='https://www.amazon.com/dp/B000000001',
            captured_at='2026-09-09 12:34:56')
        self.assertEqual(
            {item['captured_at'] for item in checks.values()},
            {'2026-09-09 12:34:56'},
        )

    def test_product_image_and_brand_story_image_are_separate(self):
        checks = inspect_frontend(
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>'
            '<div id="aplusBrandStory_feature_div"><h2>From the brand</h2></div>',
            '', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['product_image']['status'], 'pass')
        self.assertEqual(checks['brand_story_image']['status'], 'fail')
        self.assertIn('image=missing', checks['brand_story_image']['observed'])

        checks = inspect_frontend(
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>'
            '<div id="aplusBrandStory_feature_div"><img src="brand.jpg"></div>',
            '', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['product_image']['status'], 'pass')
        self.assertEqual(checks['brand_story_image']['status'], 'fail')
        self.assertIn('heading=missing', checks['brand_story_image']['observed'])

    def test_bsr_and_choice_together_make_asin_invalid(self):
        checks = inspect_frontend(
            '<div id="prodDetails" data-csa-c-asin="B000000001"><table class="prodDetTable"><tr>'
            '<th class="prodDetSectionEntry">Best Sellers Rank</th><td>#1 in Patio</td>'
            '</tr></table></div>'
            '<div id="acBadge_feature_div" data-csa-c-asin="B000000001">'
            '<span class="mvt-ac-badge-rectangle">Amazon\'s Choice</span></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['bsr_badge']['status'], 'fail')
        self.assertEqual(checks['amazon_choice_badge']['status'], 'fail')
        self.assertIn('ASIN不合法', checks['bsr_badge']['reason'])
        self.assertIn('ASIN不合法', checks['amazon_choice_badge']['reason'])

    def test_choice_passes_when_bsr_is_absent(self):
        checks = inspect_frontend(
            '<div id="acBadge_feature_div" data-csa-c-asin="B000000001">'
            '<span class="mvt-ac-badge-rectangle">Amazon\'s Choice</span></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['bsr_badge']['status'], 'fail')
        self.assertEqual(checks['amazon_choice_badge']['status'], 'pass')

    def test_product_badges_must_belong_to_requested_asin(self):
        checks = inspect_frontend(
            '<div id="title_feature_div" data-csa-c-asin="B000000001"></div>'
            '<div id="prodDetails" data-csa-c-asin="B000000002"><table class="prodDetTable"><tr>'
            '<th class="prodDetSectionEntry">Best Sellers Rank</th><td>#1 in Patio</td>'
            '</tr></table></div>'
            '<div id="acBadge_feature_div" data-csa-c-asin="B000000002">'
            '<span class="mvt-ac-badge-rectangle">Amazon\'s Choice</span></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['bsr_badge']['status'], 'fail')
        self.assertEqual(checks['amazon_choice_badge']['status'], 'fail')

    def test_hidden_choice_explanation_is_not_an_amazon_choice_badge(self):
        checks = inspect_frontend(
            '<div id="title_feature_div" data-csa-c-asin="B000000001"></div>'
            '<div id="acBadge_feature_div" data-csa-c-asin="B000000001">'
            '<div class="a-popover-preload" id="a-popover-amazons-choice-popover">'
            "Amazon's Choice highlights highly rated products"
            '</div></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['amazon_choice_badge']['status'], 'fail')

    def test_page_product_asin_mismatch_makes_all_checks_unknown(self):
        checks = inspect_frontend(
            '<div id="title_feature_div" data-csa-c-asin="B000000002"></div>'
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>'
            '<div id="climatePledgeFriendlyATF_feature_div" data-csa-c-asin="B000000002">'
            '<div id="climatePledgeFriendlyBadge"><span id="CPF-ATF-Card">'
            '<a class="climatePledgeFriendlyATF"><img src="leaf.svg"></a>'
            '<span class="climatePledgeFriendlyProgramName">1 sustainability feature</span>'
            '</span></div></div>'
            '<div id="prodDetails"><table class="prodDetTable"><tr>'
            '<th class="prodDetSectionEntry">Best Sellers Rank</th><td>#1</td></tr>'
            '</table></div>',
            '8x10', 'B000000001')
        self.assertEqual({item['status'] for item in checks.values()}, {'unknown'})
        self.assertTrue(all('ASIN' in item['reason'] for item in checks.values()))

    def test_recommendation_eco_badge_is_not_current_product_evidence(self):
        checks = inspect_frontend(
            '<div id="title_feature_div" data-csa-c-asin="B000000001"></div>'
            '<div class="a-carousel"><div class="a-carousel-card">'
            '<div class="climatePledgeFriendlyATF_feature_div" data-csa-c-asin="B000000002">'
            '<div id="climatePledgeFriendlyBadge"><span id="CPF-ATF-Card">'
            '<a class="climatePledgeFriendlyATF"><img src="leaf.svg"></a>'
            '<span class="climatePledgeFriendlyProgramName">1 sustainability feature</span>'
            '</span></div></div></div></div>',
            '8x10', 'B000000001')
        self.assertEqual(checks['eco_badge']['status'], 'fail')

    def test_eco_badge_requires_explicit_current_asin_binding(self):
        checks = inspect_frontend(
            '<div id="title_feature_div" data-csa-c-asin="B000000001"></div>'
            '<div id="climatePledgeFriendlyATF_feature_div">'
            '<div id="climatePledgeFriendlyBadge"><span id="CPF-ATF-Card">'
            '<a class="climatePledgeFriendlyATF"><img src="leaf.svg"></a>'
            '<span class="climatePledgeFriendlyProgramName">1 sustainability feature</span>'
            '</span></div></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['eco_badge']['status'], 'fail')

    def test_page_gate_is_unknown_even_when_dom_contains_positive_evidence(self):
        checks = inspect_frontend(
            '<div id="imageBlock"><img src="main.jpg"></div><div id="brandstory"><img src="brand.jpg"></div>',
            '8x10', 'B000000001', page_ready=False, page_status='identity_mismatch')
        self.assertEqual({item['status'] for item in checks.values()}, {'unknown'})
        self.assertTrue(all('identity_mismatch' in item['reason'] for item in checks.values()))

    def test_missing_page_identity_anchor_makes_all_checks_unknown(self):
        checks = inspect_frontend(
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>',
            '', 'B000000001')
        self.assertEqual({item['status'] for item in checks.values()}, {'unknown'})
        self.assertTrue(all('缺少主商品身份锚点' in item['reason'] for item in checks.values()))

    def test_dimension_normalization_is_conservative(self):
        self.assertEqual(normalize_dimension(' 8 × 10 Inches '), '8x10 in')
        self.assertEqual(normalize_dimension('8 x 10 ft'), '8x10 ft')
        self.assertEqual(normalize_dimension("2.5' x 8'"), '2.5 ftx8 ft')
        self.assertEqual(normalize_dimension(''), '')

    def test_global_navigation_text_is_not_product_evidence(self):
        checks = inspect_frontend(
            '<div id="nav-main">Best Sellers EN Climate Pledge Friendly</div>'
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>',
            '2.5x8', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['size_consistent']['status'], 'unknown')
        self.assertEqual(checks['bsr_badge']['status'], 'fail')
        self.assertEqual(checks['eco_badge']['status'], 'fail')

    def test_dimension_units_do_not_block_source_without_units(self):
        checks = inspect_frontend(
            '<div id="inline-twister-expander-header-size_name" aria-label="Selected Size is 2.5\' x 8\'.">'
            '<span id="inline-twister-expanded-dimension-text-size_name">2.5\' x 8\'</span></div>',
            '2.5x8', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(checks['size_consistent']['status'], 'pass')

    def test_visible_columns_use_checkmarks_but_counts_keep_raw_statuses(self):
        checks = inspect_frontend(
            '<div id="imageBlock_feature_div"><img src="main.jpg"></div>'
            '<div id="aplusBrandStory_feature_div"><h2>From the brand</h2><img src="brand.jpg"></div>'
            '<div id="inline-twister-expander-header-size_name" aria-label="Selected Size is 8x10.">'
            '<span id="inline-twister-expanded-dimension-text-size_name">8x10</span></div>'
            '<div id="prodDetails" data-csa-c-asin="B000000001"><table class="prodDetTable"><tr>'
            '<th class="prodDetSectionEntry">Best Sellers Rank</th><td>#1 in Patio</td>'
            '</tr></table></div>',
            '8x10', 'B000000001', page_url='https://www.amazon.com/dp/B000000001')
        visible = frontend_columns(checks)
        self.assertIn('✅', visible)
        self.assertIn('❌', visible)
        self.assertEqual(frontend_status_counts([type('R', (), {
            'frontend_checks': checks,
            'status': type('S', (), {'value': 'ok'})(),
        })()])['pass'], 4)


if __name__ == '__main__':
    unittest.main()
