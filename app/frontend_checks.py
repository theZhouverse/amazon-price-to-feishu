# -*- coding: utf-8 -*-
"""Same-page evidence checks for the product result N:T columns.

The production crawler already freezes one DOM snapshot for price parsing.  This
module deliberately stays pure: it consumes that snapshot and the expected
report size, and returns evidence-rich values that can be cached and tested
without a browser or a network connection.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from decimal import Decimal

from amazon.price_evidence import Tree, eligible


FRONTEND_CHECK_RULE_VERSION = '2026-09-11-v16'
FRONTEND_STATUSES = ('pass', 'fail', 'unknown', 'not_applicable')
FRONTEND_DISPLAY_VALUES = {
    'pass': '✅',
    'fail': '❌',
    # A missing/blocked evidence state must not be presented as a failure or
    # success.  The visible sheet uses a dash while the bundle retains the
    # exact unknown/not_applicable status and reason.
    'unknown': '-',
    'not_applicable': '-',
}
CHECK_KEYS = (
    'product_image',
    'brand_story_image',
    'size_consistent',
    'bsr_badge',
    'parent_child_asin',
    'eco_badge',
    'amazon_choice_badge',
)
FRONTEND_HEADERS = (
    '商品主图',
    '品牌故事',
    '前端尺寸',
    'BSR',
    '父ASIN发散',
    '环保标',
    'AC标',
)

_GATE_STATUSES = {
    'page_not_found', 'crawl_error', 'identity_mismatch', 'currency_error',
    'parse_error', 'source_data_invalid',
}
_GLOBAL_REGION_RE = re.compile(
    r'(^|[-_ ])(?:nav|navbar|header|footer|search|language|skip|signin|sign-in)'
    r'(?:[-_ ]|$)', re.I)
_NON_PRODUCT_CONTEXT_RE = re.compile(
    r'recommend|similar|sponsored|carousel|sims-|desktop-dp-sims|'
    r'a-text-price|review|customer.*question|ask-btf|sns-|subscribe', re.I)


def _unknown(reason: str, *, page_url: str = '', captured_at: str = '') -> dict:
    return {
        'observed': '',
        'status': 'unknown',
        'reason': reason,
        'evidence_locator': '',
        'page_url': page_url,
        'captured_at': captured_at,
        'rule_version': FRONTEND_CHECK_RULE_VERSION,
    }


def unknown_checks(reason: str = '页面证据不可用', *, page_url: str = '',
                   captured_at: str = '') -> dict[str, dict]:
    """Return all seven columns as explicit unknown values."""
    return {
        key: _unknown(reason, page_url=page_url, captured_at=captured_at)
        for key in CHECK_KEYS
    }


def stamp_frontend_checks(checks: dict | None, captured_at: str) -> dict:
    """Attach one DOM-capture timestamp to every existing check evidence item."""
    captured_at = str(captured_at or '').strip()
    if not captured_at:
        return checks or {}
    for evidence in (checks or {}).values():
        if isinstance(evidence, dict):
            evidence['captured_at'] = captured_at
    return checks or {}


def _text(node) -> str:
    if node is None:
        return ''
    parts: list[str] = []
    for child in getattr(node, 'children', ()):
        if isinstance(child, str):
            parts.append(child)
        elif eligible(child):
            parts.append(_text(child))
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def _raw_text(node) -> str:
    """Return text inside a node without applying the price parser's hidden rule.

    This is a structural traversal only.  Visibility is checked separately by
    ``_hidden_evidence`` at the point where a check represents a front-end
    display state.  Script/style content is ignored.
    """
    if node is None:
        return ''
    parts: list[str] = []
    for child in getattr(node, 'children', ()):
        if isinstance(child, str):
            parts.append(child)
        elif str(getattr(child, 'tag', '')).lower() not in {
            'script', 'style', 'template', 'noscript',
        }:
            parts.append(_raw_text(child))
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def _marker(node) -> str:
    attrs = getattr(node, 'attrs', {})
    return ' '.join(str(attrs.get(key) or '') for key in (
        'id', 'class', 'data-feature-name', 'data-hook', 'aria-label',
        'name', 'data-csa-c-item-id', 'data-csa-c-slot-id',
    )).lower()


def _locator(node) -> str:
    attrs = getattr(node, 'attrs', {})
    if attrs.get('id'):
        return f'#{attrs["id"]}'
    if attrs.get('class'):
        first = str(attrs['class']).split()[0]
        if first:
            return f'.{first}'
    return str(getattr(node, 'tag', '') or '')


def _is_global_region(node) -> bool:
    return bool(_GLOBAL_REGION_RE.search(_marker(node)))


def _in_non_product_context(node) -> bool:
    """Reject recommendation/global contexts while preserving product scope."""
    for ancestor in _ancestor_chain(node):
        attrs = getattr(ancestor, 'attrs', {})
        if str(getattr(ancestor, 'tag', '')).lower() in {
                'script', 'style', 'template', 'noscript'}:
            return True
        marker = _marker(ancestor)
        if _GLOBAL_REGION_RE.search(marker) or _NON_PRODUCT_CONTEXT_RE.search(marker):
            return True
    return False


def _nodes(html) -> list:
    tree = html if isinstance(html, Tree) else Tree(html or '')
    return [node for node in tree.nodes if eligible(node)]


def _descendant_images(node) -> list:
    images = []
    for child in getattr(node, 'children', ()):
        if not hasattr(child, 'tag') or not eligible(child):
            continue
        if child.tag.lower() == 'img':
            images.append(child)
        images.extend(_descendant_images(child))
    return images


def _raw_descendant_images(node) -> list:
    """Find images inside an explicitly selected product module.

    The generic price DOM excludes carousel/recommendation subtrees.  A+ brand
    story is itself a carousel, so once its exact product module has been
    selected we must inspect its descendants without applying that unrelated
    global exclusion.
    """
    images = []
    for child in getattr(node, 'children', ()):
        if not hasattr(child, 'tag'):
            continue
        tag = str(child.tag).lower()
        if tag in {'script', 'style', 'template', 'noscript'}:
            continue
        if tag == 'img':
            images.append(child)
        images.extend(_raw_descendant_images(child))
    return images


def _valid_image(node) -> bool:
    attrs = getattr(node, 'attrs', {})
    # Amazon frequently keeps a 1x1 transparent GIF in ``src`` and the real
    # asset in ``data-src``/``data-old-hires``.  Treating the placeholder as a
    # real image would make an empty brand-story module pass.
    sources = (
        attrs.get('data-old-hires'), attrs.get('data-src'),
        attrs.get('data-a-dynamic-image'), attrs.get('src'),
    )
    for source in sources:
        text = str(source or '').strip()
        if not text:
            continue
        if text.lower().startswith('data:image/gif;base64,'):
            continue
        return True
    return False


def _has_image_in_region(node) -> bool:
    return any(_valid_image(img) for img in _descendant_images(node))


def _direct_or_region_image(nodes: list) -> tuple[bool, str]:
    region_re = re.compile(r'imageblock|imagegallery|landingimage|imgblkfront|mainimage')
    for node in nodes:
        marker = _marker(node)
        if region_re.search(marker) and (node.tag.lower() == 'img' and _valid_image(node)
                                         or _has_image_in_region(node)):
            return True, _locator(node)
    # A visible landingImage can be represented as an img without a wrapping
    # image-block element in offline snapshots.
    for node in nodes:
        if node.tag.lower() == 'img' and _valid_image(node) and re.search(
                r'landingimage|imgblkfront|mainimage', _marker(node)):
            return True, _locator(node)
    return False, ''


def _brand_story(nodes: list) -> tuple[bool, str, str]:
    """Match the current product's exact ``From the brand`` A+ module.

    ``aplusBrandStory_feature_div`` is only a container hint: Amazon often
    leaves an empty container in the saved DOM.  A pass additionally requires
    the exact visible ``From the brand`` heading and a real image inside that
    module.  The observed value keeps the two pieces separate for diagnostics.
    """
    candidate_locator = ''
    heading_found = False
    image_found = False
    for node in nodes:
        attrs = getattr(node, 'attrs', {})
        module_id = str(attrs.get('id') or '').lower()
        feature_name = str(attrs.get('data-feature-name') or '').lower()
        if not (module_id == 'aplusbrandstory_feature_div'
                or feature_name == 'aplusbrandstory'):
            continue
        candidate_locator = candidate_locator or _locator(node)
        if _in_non_product_context(node):
            continue
        module_heading = any(
            str(getattr(child, 'tag', '')).lower() in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
            and re.sub(r'\s+', ' ', _raw_text(child)).strip().casefold() == 'from the brand'
            for child in _walk_descendants(node))
        module_image = any(_valid_image(img) for img in _raw_descendant_images(node))
        heading_found = heading_found or module_heading
        image_found = image_found or module_image
        if module_heading and module_image:
            return True, _locator(node), 'heading=present;image=present'
    observed = (
        f'module={"present" if candidate_locator else "missing"};'
        f'heading={"present" if heading_found else "missing"};'
        f'image={"present" if image_found else "missing"}')
    return False, candidate_locator, observed


def normalize_dimension(value) -> str:
    """Normalize the common size spellings without inventing missing units."""
    text = str(value or '').strip().lower()
    text = text.replace('×', 'x').replace('✕', 'x').replace('＊', 'x')
    text = re.sub(r"(\d(?:[\d.,]*\d)?)\s*['’]", r'\1 ft', text)
    text = re.sub(r'[“”"″]', 'in', text)
    text = re.sub(r'[’‘\']', "'", text)
    text = re.sub(r'\s*([x*])\s*', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*(inches|inch)\b', 'in', text)
    text = re.sub(r'\s*(centimeters|centimetres|centimeter|centimetre)\b', 'cm', text)
    text = re.sub(r'\s*(feet|foot)\b', 'ft', text)
    text = re.sub(r'(\d)(in|cm|ft)\b', r'\1 \2', text)
    return text.strip(' ,;:')


def _dimension_signature(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return canonical inch values for unit-bearing dimensions.

    Amazon may render 2.5 feet as ``2'6\"``.  Comparing the visible numbers
    directly would incorrectly treat those as three components (2, 6, 8), so
    compound feet/inches components are converted to one canonical value.
    Unitless source values remain raw and are handled conservatively by the
    caller for backwards compatibility with existing weekly sheets.
    """
    normalized = normalize_dimension(value)
    raw_numbers = tuple(re.findall(r'\d+(?:\.\d+)?', normalized))
    raw_units = tuple(re.findall(r'(?<![a-z])(in|cm|ft|m)(?![a-z])', normalized))
    if not raw_units:
        return raw_numbers, ()

    parts = re.split(r'\s*[x*]\s*', normalized, maxsplit=1)
    default_unit = next((unit for part in parts
                         for unit in ('ft', 'in', 'cm', 'm')
                         if re.search(rf'\b{unit}\b', part)), '')

    def parse_part(part: str):
        part = re.sub(r'\s*\([^)]*\).*$', '', part).strip()
        number = r'(\d+(?:\.\d+)?)'
        match = re.match(rf'^{number}\s*ft\s*{number}\s*in\b', part)
        if match:
            return Decimal(match.group(1)) * 12 + Decimal(match.group(2))
        match = re.match(rf'^{number}\s*ft\b', part)
        if match:
            return Decimal(match.group(1)) * 12
        match = re.match(rf'^{number}\s*in\b', part)
        if match:
            return Decimal(match.group(1))
        match = re.match(rf'^{number}\s*cm\b', part)
        if match:
            return Decimal(match.group(1)) * Decimal('0.3937007874015748')
        match = re.match(rf'^{number}\s*m\b', part)
        if match:
            return Decimal(match.group(1)) * Decimal('39.37007874015748')
        match = re.match(rf'^{number}', part)
        if not match or not default_unit:
            return None
        value = Decimal(match.group(1))
        return {
            'ft': value * 12,
            'in': value,
            'cm': value * Decimal('0.3937007874015748'),
            'm': value * Decimal('39.37007874015748'),
        }[default_unit]

    values = [parse_part(part) for part in parts]
    if not values or any(value is None for value in values):
        return raw_numbers, raw_units
    return tuple(format(value.normalize(), 'f') for value in values), tuple('in' for _ in values)


def _raw_dimension_numbers(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r'\d+(?:\.\d+)?', normalize_dimension(value)))


def _expanded_dimension_units(numbers: tuple[str, ...],
                              units: tuple[str, ...]) -> tuple[str, ...]:
    """Expand one trailing unit to each dimension component.

    Amazon uses both ``8 x 10 ft`` and ``8' x 10'`` for the same rug. The
    former has one unit after the whole expression while the latter has one
    unit after each number. Mixed-unit expressions remain strict.
    """
    if len(units) == 1 and len(numbers) > 1:
        return units * len(numbers)
    return units


def _looks_like_dimension(value: str) -> bool:
    normalized = normalize_dimension(value)
    return bool(re.search(r'\d+(?:\.\d+)?\s*x\s*\d+', normalized)
                or re.search(r'\d+(?:\.\d+)?\s*(?:in|cm|ft|m)\b', normalized))


def _selected_dimension(nodes: list) -> tuple[str, str]:
    """Read the selected value from Amazon's inline twister first."""
    for node in nodes:
        node_id = str(getattr(node, 'attrs', {}).get('id') or '').lower()
        if not node_id.startswith('inline-twister-expander-header-'):
            continue
        for child in _walk_descendants(node):
            child_id = str(getattr(child, 'attrs', {}).get('id') or '').lower()
            if child_id.startswith('inline-twister-expanded-dimension-text-'):
                value = _text(child)
                if value and _looks_like_dimension(value):
                    return value, _locator(child)
        aria_label = str(getattr(node, 'attrs', {}).get('aria-label') or '')
        match = re.search(r'\bselected\s+[^ ]+\s+is\s+(.+?)(?:\.\s*tap\b|$)', aria_label, re.I)
        if match and _looks_like_dimension(match.group(1)):
            return match.group(1).strip(), _locator(node)

    # Conservative fallback for older Amazon layouts and small unit fixtures.
    pattern = re.compile(r'size|dimension|variation|twister|swatch|style|color')
    selected_pattern = re.compile(r'selected|checked|current|active')
    candidates: list[tuple[str, str]] = []
    for node in nodes:
        marker = _marker(node)
        attrs = getattr(node, 'attrs', {})
        if not pattern.search(marker):
            continue
        text = _text(node)
        if not text or len(text) > 180 or not _looks_like_dimension(text):
            continue
        selected = (str(attrs.get('aria-selected') or '').lower() == 'true'
                    or str(attrs.get('aria-checked') or '').lower() == 'true'
                    or 'selected' in str(attrs.get('class') or '').lower()
                    or selected_pattern.search(marker))
        candidates.append((text, _locator(node), selected))
    selected = [(text, locator) for text, locator, is_selected in candidates if is_selected]
    if selected:
        return selected[0]
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1]
    return '', ''


def _walk_descendants(node):
    for child in getattr(node, 'children', ()):
        if hasattr(child, 'tag'):
            yield child
            yield from _walk_descendants(child)


def _raw_tree_nodes(html) -> tuple[Tree, list]:
    tree = html if isinstance(html, Tree) else Tree(html or '')
    return tree, list(tree.nodes)


def _ancestor_chain(node):
    current = node
    while current is not None:
        yield current
        current = getattr(current, 'parent', None)


def _product_detail_root(node):
    for ancestor in _ancestor_chain(node):
        attrs = getattr(ancestor, 'attrs', {})
        ident = str(attrs.get('id') or '').lower()
        classes = str(attrs.get('class') or '').lower().split()
        if ident == 'proddetails':
            return ancestor
        if ident == 'productdetails_feature_div':
            return ancestor
        if 'proddettable' in classes:
            return ancestor
    return None


def _page_product_asin(raw_nodes: list) -> str:
    """Read the ASIN of the page's primary product, not a recommendation card.

    Amazon can keep several product cards and preloaded recommendation modules
    in one HTML document.  The title widget is the narrowest stable anchor for
    the product actually being displayed; ``#ASIN`` is a fallback for older
    layouts.  This is intentionally separate from per-field evidence so a
        mismatched page can be stopped before any of the seven columns are inferred.
    """
    for node in raw_nodes:
        attrs = getattr(node, 'attrs', {})
        if str(attrs.get('id') or '').lower() != 'title_feature_div':
            continue
        value = str(attrs.get('data-csa-c-asin') or attrs.get('data-asin') or '').upper().strip()
        if value:
            return value
    for node in raw_nodes:
        attrs = getattr(node, 'attrs', {})
        if str(attrs.get('id') or '').upper() != 'ASIN':
            continue
        value = str(attrs.get('value') or '').upper().strip()
        if re.fullmatch(r'[A-Z0-9]{10}', value):
            return value
    return ''


def _page_url_asin(page_url: str) -> str:
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)',
                      str(page_url or ''), re.I)
    return match.group(1).upper() if match else ''


def _hidden_evidence(node) -> bool:
    """Reject hidden/preloaded explanatory DOM beneath a product widget."""
    for ancestor in _ancestor_chain(node):
        attrs = getattr(ancestor, 'attrs', {})
        classes = set(str(attrs.get('class') or '').lower().split())
        style = re.sub(r'\s+', '', str(attrs.get('style') or '').lower())
        if (str(attrs.get('aria-hidden') or '').lower() == 'true'
                or 'hidden' in attrs
                or classes.intersection({'aok-hidden', 'aok-offscreen', 'a-popover-preload'})
                or 'display:none' in style
                or 'visibility:hidden' in style):
            return True
    return False


def _belongs_to_asin(node, asin: str) -> bool:
    """Require an explicit current-ASIN anchor for product-level evidence."""
    target = str(asin or '').upper()
    if not target:
        return True
    seen: set[str] = set()
    for ancestor in _ancestor_chain(node):
        attrs = getattr(ancestor, 'attrs', {})
        for key in ('data-csa-c-asin', 'data-asin'):
            value = str(attrs.get(key) or '').upper().strip()
            if value:
                seen.add(value)
    return target in seen


def _detail_table_asin(root) -> str:
    """Read the ASIN row belonging to one product-details table.

    A product-details table may contain nested markup or embedded tables from
    experiments.  When ``root`` is a table, only rows whose nearest table is
    that exact root are eligible; an ASIN discovered in a nested table must
    never be used to bind the outer BSR row.
    """
    root_tag = str(getattr(root, 'tag', '')).lower()
    for child in _walk_descendants(root):
        if str(getattr(child, 'tag', '')).lower() != 'tr':
            continue
        if root_tag == 'table':
            nearest_table = None
            current = child
            while current is not None:
                if str(getattr(current, 'tag', '')).lower() == 'table':
                    nearest_table = current
                    break
                current = getattr(current, 'parent', None)
            if nearest_table is not root:
                continue
        headers = [
            _raw_text(candidate).casefold()
            for candidate in (child, *_walk_descendants(child))
            if str(getattr(candidate, 'tag', '')).lower() == 'th'
        ]
        if not any(re.sub(r'\s+', ' ', header).strip() == 'asin' for header in headers):
            continue
        values = re.findall(r'\b[A-Z0-9]{10}\b', _raw_text(child).upper())
        if values:
            return values[0]
    return ''


def _product_bsr(raw_nodes: list, asin: str = '') -> tuple[bool, str, str]:
    """Match a *visible* BSR field in the current product-information table.

    Amazon commonly serializes the product-details table inside a collapsed
    ``.a-expander-content`` with ``display:none``.  That row is useful
    diagnostic evidence, but it is not a currently displayed front-end BSR
    marker.  A pass therefore requires all of the following:

    * exact ``Best Sellers Rank`` header in a ``prodDetSectionEntry`` row;
    * the nearest product-details table is not a recommendation/global region;
    * the same table has an ``ASIN`` row equal to the requested ASIN (or an
      explicit current-ASIN binding when the table has no ASIN row);
    * the BSR row and its ancestors are visible in the captured DOM.
    """
    hidden_candidate = None
    for node in raw_nodes:
        if _in_non_product_context(node):
            continue
        attrs = getattr(node, 'attrs', {})
        classes = str(attrs.get('class') or '').lower().split()
        if str(getattr(node, 'tag', '')).lower() != 'th' or 'proddetsectionentry' not in classes:
            continue
        if re.sub(r'\s+', ' ', _raw_text(node)).strip().casefold() != 'best sellers rank':
            continue
        root = _product_detail_root(node)
        if root is None or _in_non_product_context(root):
            continue
        detail_asin = _detail_table_asin(root)
        if (asin and detail_asin and detail_asin != str(asin).upper()) or (
                asin and not detail_asin and not _belongs_to_asin(node, asin)):
            continue
        row = node
        while getattr(row, 'parent', None) is not None and str(getattr(row, 'tag', '')).lower() != 'tr':
            row = row.parent
        observed = _raw_text(row)
        if _hidden_evidence(row):
            # Keep the fact for diagnostics, but do not count a collapsed
            # product-details row as a visible front-end marker.  Continue in
            # case an experiment rendered a second visible, correctly-bound
            # row later in the DOM.
            hidden_candidate = (
                False, f'{_locator(root)} [hidden]', observed)
            continue
        return True, _locator(root), observed
    if hidden_candidate is not None:
        return hidden_candidate
    return False, '', ''


def _choice_text(value: object) -> str:
    """Normalize the small visible label used by Amazon's Choice badges."""
    text = str(value or '').replace('\u200b', '').replace('\u200c', '') \
        .replace('\u200d', '').replace('\ufeff', '')
    return re.sub(r'\s+', ' ', text).strip()


def _product_choice(nodes: list, asin: str = '',
                    live_evidence: dict | None = None) -> tuple[bool, str, str]:
    """Match the visible current-product Amazon's Choice badge.

    ``#acBadge_feature_div`` often contains a hidden preloaded explanation
    saying that Amazon's Choice highlights highly rated products.  That text
    is not the badge.  The normal desktop DOM is a visible
    ``span.a-size-small`` inside an ``mvt-ac-badge-rectangle``; require an
    actual descendant whose normalized text is exactly ``Amazon's Choice``
    and ignore hidden/preloaded descendants.  The class is treated as a
    diagnostic shape rather than a hard gate because Amazon experiments use
    several equivalent badge class names.
    """
    choice_re = re.compile(r"^amazon['’]?s\s+choice$", re.I)
    # Modern Amazon layouts can render the badge inside an open Shadow DOM.
    # ``document.documentElement.outerHTML`` does not contain that subtree, so
    # the crawler supplies a separately captured, visibility-checked marker.
    # Never accept it without an explicit current-ASIN binding.
    if isinstance(live_evidence, dict):
        evidence_asin = str(live_evidence.get('asin') or '').upper().strip()
        evidence_text = _choice_text(live_evidence.get('text'))
        if (live_evidence.get('visible') is True
                and evidence_asin
                and evidence_asin == str(asin or '').upper().strip()
                and choice_re.fullmatch(evidence_text)):
            return True, str(live_evidence.get('locator') or
                             '#acBadge_feature_div (shadow DOM)'), evidence_text
    for root in nodes:
        ident = str(getattr(root, 'attrs', {}).get('id') or '').lower()
        if (ident != 'acbadge_feature_div'
                or _in_non_product_context(root)
                or _hidden_evidence(root)
                or not _belongs_to_asin(root, asin)):
            continue
        for node in _walk_descendants(root):
            if _hidden_evidence(node):
                continue
            text = _choice_text(_text(node))
            if choice_re.fullmatch(text):
                return True, _locator(root), text
    return False, '', ''


def _product_eco(raw_nodes: list, asin: str = '') -> tuple[bool, str, str]:
    """Match the complete current-product Climate Pledge badge chain.

    Empty ATF/BTF/A+ placeholders and generic sustainability copy are common in
    Amazon HTML.  A pass requires the product-scoped ATF module, its badge/card,
    the Climate Pledge trigger and program-name text, plus an actual icon image.
    """
    for node in raw_nodes:
        if _in_non_product_context(node):
            continue
        ident = str(getattr(node, 'attrs', {}).get('id') or '').lower()
        if ident != 'climatepledgefriendlyatf_feature_div':
            continue
        attrs = getattr(node, 'attrs', {})
        module_asin = str(attrs.get('data-csa-c-asin') or '').upper().strip()
        expected_asin = str(asin or '').upper().strip()
        # The product-level module must explicitly bind itself to the current
        # requested ASIN. A missing binding is not safe evidence because the
        # same page can contain other product cards and recommendation modules.
        if not expected_asin or not module_asin or module_asin != expected_asin:
            continue
        descendants = list(_walk_descendants(node))
        badge = next((child for child in descendants
                      if str(getattr(child, 'attrs', {}).get('id') or '').lower()
                      == 'climatepledgefriendlybadge'), None)
        card = next((child for child in descendants
                     if str(getattr(child, 'attrs', {}).get('id') or '').lower()
                     == 'cpf-atf-card'), None)
        if badge is None or card is None or _hidden_evidence(card):
            continue
        card_descendants = list(_walk_descendants(card))
        trigger = next((child for child in card_descendants
                        if 'climatepledgefriendlyatf' in str(
                            getattr(child, 'attrs', {}).get('class') or '').lower().split()), None)
        program = next((child for child in card_descendants
                       if 'climatepledgefriendlyprogramname' in str(
                           getattr(child, 'attrs', {}).get('class') or '').lower().split()
                       and _raw_text(child)), None)
        has_image = trigger is not None and any(
            _valid_image(img) for img in _raw_descendant_images(trigger))
        if trigger is not None and program is not None and has_image:
            return True, _locator(node), _raw_text(program)
    return False, '', ''


def _asin_values(node) -> set[str]:
    attrs = getattr(node, 'attrs', {})
    raw = ' '.join(str(attrs.get(key) or '') for key in (
        'data-asin', 'data-defaultasin', 'data-csa-c-item-id', 'href',
    )) + ' ' + getattr(node, 'html', lambda: '')()
    return {item.upper() for item in re.findall(
        r'(?:/dp/|/gp/product/|ASIN[=:" ]+)([A-Z0-9]{10})', raw, re.I)}


def _variant_check(nodes: list, asin: str) -> tuple[str, str, str]:
    variation_re = re.compile(r'^inline-twister-expander-content-.+', re.I)
    regions = [node for node in nodes if variation_re.search(
        str(getattr(node, 'attrs', {}).get('id') or ''))]
    if not regions:
        return 'unknown', '', '无法确认变体区域'
    values: set[str] = set()
    locator = _locator(regions[0])
    for region in regions:
        for child in _walk_descendants(region):
            classes = str(getattr(child, 'attrs', {}).get('class') or '').lower().split()
            if getattr(child, 'tag', '').lower() == 'li' and 'inline-twister-swatch' in classes:
                values.update(_asin_values(child))
    values.discard(str(asin or '').upper())
    if values:
        return 'pass', ','.join(sorted(values)), locator
    return 'fail', '0', locator


def _result(key: str, observed: str, status: str, reason: str, locator: str,
            page_url: str) -> dict:
    return {
        'observed': observed,
        'status': status if status in FRONTEND_STATUSES else 'unknown',
        'reason': reason,
        'evidence_locator': locator,
        'page_url': page_url,
        'captured_at': '',
        'rule_version': FRONTEND_CHECK_RULE_VERSION,
    }


def inspect_frontend(html, expected_size: str = '', asin: str = '', *,
                     page_ready: bool = True, page_status: str = 'ok',
                     page_url: str = '', captured_at: str = '',
                     live_ac_badge: dict | None = None) -> dict[str, dict]:
    """Inspect all seven checks from one already-loaded DOM snapshot."""
    if not page_ready or page_status in _GATE_STATUSES:
        return unknown_checks(
            f'页面门禁: {page_status or "页面不可用"}', page_url=page_url,
            captured_at=captured_at)
    tree, raw_nodes = _raw_tree_nodes(html)
    nodes = _nodes(tree)
    if not nodes:
        return unknown_checks('DOM为空或未加载完成', page_url=page_url,
                              captured_at=captured_at)

    requested_asin = str(asin or '').upper().strip()
    page_asin = _page_product_asin(raw_nodes)
    url_asin = _page_url_asin(page_url)
    if requested_asin and page_asin and page_asin != requested_asin:
        return unknown_checks(
            f'页面主商品 ASIN 与请求 ASIN 不一致: 请求 {requested_asin}，页面 {page_asin}',
            page_url=page_url, captured_at=captured_at)
    if requested_asin and url_asin and url_asin != requested_asin:
        return unknown_checks(
            f'页面URL ASIN 与请求 ASIN 不一致: 请求 {requested_asin}，URL {url_asin}',
            page_url=page_url, captured_at=captured_at)
    if requested_asin and not page_asin and not url_asin:
        return unknown_checks(
            f'无法确认页面主商品 ASIN: 页面缺少主商品身份锚点，无法安全匹配请求 ASIN {requested_asin}',
            page_url=page_url, captured_at=captured_at)

    main_image, main_locator = _direct_or_region_image(nodes)
    brand_image, brand_locator, brand_observed = _brand_story(nodes)

    expected = normalize_dimension(expected_size)
    observed, size_locator = _selected_dimension(nodes)
    observed_norm = normalize_dimension(observed)
    expected_numbers, expected_units = _dimension_signature(expected)
    observed_numbers, observed_units = _dimension_signature(observed_norm)
    if not expected_units:
        # Existing source rows without units are compared by their displayed
        # numeric components, preserving the historical 2.5x8 vs 2.5' x 8'
        # compatibility rule.
        dimensions_match = (
            bool(_raw_dimension_numbers(expected))
            and _raw_dimension_numbers(expected) == _raw_dimension_numbers(observed_norm)
        )
    else:
        dimensions_match = (
            bool(expected_numbers) and expected_numbers == observed_numbers
            and bool(observed_units)
            and _expanded_dimension_units(expected_numbers, expected_units)
            == _expanded_dimension_units(observed_numbers, observed_units)
        )
    if not expected or not observed_norm or not observed_numbers:
        size_status, size_reason = 'unknown', '缺少明确的周报预期尺寸或当前选中尺寸'
    elif dimensions_match:
        size_status, size_reason = 'pass', '当前选中尺寸与周报预期一致'
    else:
        size_status, size_reason = 'fail', '当前选中尺寸与周报预期不一致'

    bsr, bsr_locator, bsr_observed = _product_bsr(raw_nodes, asin)
    choice, choice_locator, choice_observed = _product_choice(
        nodes, asin, live_evidence=live_ac_badge)
    variant_status, variant_observed, variant_locator = _variant_check(nodes, asin)
    eco, eco_locator, eco_observed = _product_eco(raw_nodes, asin)

    bsr_status = 'pass' if bsr else 'fail'
    if bsr:
        bsr_reason = '当前商品详情表存在且当前DOM可见Best Sellers Rank字段'
    elif '[hidden]' in bsr_locator:
        bsr_reason = '当前商品详情表存在Best Sellers Rank字段，但当前DOM处于折叠隐藏状态'
    else:
        bsr_reason = '当前商品详情表未发现当前可见Best Sellers Rank字段'
    choice_status = 'pass' if choice else 'fail'
    choice_reason = ("当前商品存在Amazon's Choice实际badge" if choice
                     else "当前商品的Amazon's Choice容器为空或不存在")
    # BSR and Amazon's Choice are independent presence checks.  Amazon's
    # product details table can legitimately contain ``Best Sellers Rank`` on
    # a page that also displays the AC badge.  Do not treat that normal DOM
    # combination as an ASIN conflict or overwrite a correctly detected AC
    # badge.  Both checks already have their own current-ASIN scope and
    # recommendation/hidden-content gates above.
    size_result = _result(
        'size_consistent', observed or '', size_status, size_reason, size_locator, page_url)
    size_result['expected'] = expected

    results = {
        'product_image': _result(
            'product_image', str(main_image).lower(),
            'pass' if main_image else 'fail',
            '当前商品主图存在有效图片证据' if main_image else '当前商品主图图片证据缺失',
            main_locator, page_url),
        'brand_story_image': _result(
            'brand_story_image', brand_observed,
            'pass' if brand_image else 'fail',
            'From the brand模块标题和图片均存在'
            if brand_image else 'From the brand模块标题或其图片证据缺失',
            brand_locator, page_url),
        'size_consistent': size_result,
        'bsr_badge': _result(
            'bsr_badge', bsr_observed or str(bsr).lower(),
            bsr_status, bsr_reason,
            bsr_locator, page_url),
        'parent_child_asin': _result(
            'parent_child_asin', variant_observed, variant_status,
            '页面存在至少一个不同子体/变体ASIN' if variant_status == 'pass'
            else ('页面正常但未发现子体/变体ASIN' if variant_status == 'fail'
                  else '无法确认变体区域'), variant_locator, page_url),
        'eco_badge': _result(
            'eco_badge', eco_observed or str(eco).lower(), 'pass' if eco else 'fail',
            '当前商品存在明确环保/可持续模块' if eco else '当前商品未发现明确环保/可持续模块',
            eco_locator, page_url),
        'amazon_choice_badge': _result(
            'amazon_choice_badge', choice_observed or str(choice).lower(),
            choice_status, choice_reason,
            choice_locator, page_url),
    }
    return stamp_frontend_checks(results, captured_at)


def frontend_status_values(checks: dict | None, *, page_status: str = 'ok') -> list[str]:
    """Return raw statuses for counting and bundle-level audit."""
    if page_status in _GATE_STATUSES:
        return ['unknown'] * len(CHECK_KEYS)
    checks = checks or {}
    return [str((checks.get(key) or {}).get('status') or 'unknown')
            if str((checks.get(key) or {}).get('status') or 'unknown') in FRONTEND_STATUSES
            else 'unknown' for key in CHECK_KEYS]


def frontend_columns(checks: dict | None, *, page_status: str = 'ok') -> list[str]:
    """Return visible N:T values as checkmarks/crosses, fail-closed on gates."""
    return [FRONTEND_DISPLAY_VALUES[status]
            for status in frontend_status_values(checks, page_status=page_status)]


def frontend_status_counts(results) -> dict[str, int]:
    counts = Counter({status: 0 for status in FRONTEND_STATUSES})
    for result in results:
        for status in frontend_status_values(
                getattr(result, 'frontend_checks', None),
                page_status=getattr(getattr(result, 'status', None), 'value', '')):
            counts[status] += 1
    return dict(counts)


def frontend_evidence_digest(checks: dict | None) -> str:
    """Stable digest for audit comparisons without storing page HTML in bundles."""
    payload = '|'.join(
        f'{key}:{(checks or {}).get(key, {}).get("status", "unknown")}:'
        f'{(checks or {}).get(key, {}).get("observed", "")}'
        for key in CHECK_KEYS)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
