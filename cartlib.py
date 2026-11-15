#!/usr/bin/env python3
"""Cart engine + HTML rendering for the local Shrine mirror (no external services)."""
import os, re, json, uuid, time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(BASE_DIR, "site")
TPL = os.path.join(SITE, "_tpl")
CARTS_DIR = os.environ.get("CARTS_DIR", os.path.join("/tmp", "shrine_carts"))
os.makedirs(CARTS_DIR, exist_ok=True)

# ---------------- catalog ----------------
def load_catalog():
    raw = json.load(open(os.path.join(BASE_DIR, "catalog.json")))
    cat = {}
    for p in raw:
        cat[p["handle"]] = p
    return cat

CATALOG = load_catalog()
BY_VARIANT = {str(p["variant_id"]): p for p in CATALOG.values() if p.get("variant_id")}

# ---------------- helpers ----------------
def money(cents):
    return "€" + f"{(cents or 0)/100:.2f}".replace(".", ",")

def read(p):
    return open(p, encoding="utf-8", errors="replace").read()

def cut_div(html, start_marker, placeholder="<!--PRICEBLOCK-->"):
    """Replace the div starting with start_marker (and its whole subtree) by a placeholder."""
    i = html.find(start_marker)
    if i < 0:
        return html
    j = i
    depth = 0
    tag_re = re.compile(r"<div\b|</div>")
    while True:
        m = tag_re.search(html, j)
        if not m:
            return html
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                end = m.end()
                return html[:i] + placeholder + html[end:]
        else:
            depth += 1
        j = m.end()

# ---------------- templates ----------------
def extract_template():
    cart_filled = read(os.path.join(TPL, "cart_filled.html"))
    drawer_filled = read(os.path.join(TPL, "drawer_filled.html"))

    # --- cart page row ---
    k = cart_filled.find('class="cart-item cart-item--product')
    i = cart_filled.rfind("<tr", 0, k)
    j = cart_filled.find("</tr>", k) + 5
    row = cart_filled[i:j]

    # --- drawer item ---
    k = drawer_filled.find('class="cart-drawer-item cart-item')
    i = drawer_filled.rfind("<li", 0, k)
    j = drawer_filled.find("</li>", k) + 5
    li = drawer_filled[i:j]

    row = cut_div(row, '<div class="cart-item__price-wrapper">')
    li = cut_div(li, '<div class="cart-item__price-wrapper">')
    # drop the savings line in drawer (we rebuild it)
    li = re.sub(r"<span class='cart-drawer-item__saving text-color-text'>.*?</span>\s*", "", li, flags=re.S)
    return row, li

ROW_TPL, LI_TPL = extract_template()

# ---------------- item rendering ----------------
def _fill_common(tpl, it, idx):
    h = tpl
    vid = str(it["variant_id"])
    h = h.replace("noir-dusk", it["handle"])
    h = h.replace("Noir Dusk", it["title"])
    h = h.replace("50715323597147:fd054c6a98ea742c41097e9514dd25ce", f"{vid}:{it['key']}")
    h = h.replace("50715323597147", vid)
    # image
    h = re.sub(r'(<img\s+[^>]*src=")[^"]*(")', lambda m: m.group(1) + it["image"] + m.group(2), h, count=1)
    # ids / indexes
    for name in ("CartItem", "CartDrawer-Item", "CartDrawer-Remove", "Drawer-quantity", "Quantity", "Remove"):
        h = h.replace(f"{name}-1", f"{name}-{idx}")
    h = h.replace('data-index="1"', f'data-index="{idx}"')
    h = h.replace('data-quantity="1"', f'data-quantity="{it["qty"]}"')
    h = h.replace('data-cart-quantity="1"', f'data-cart-quantity="{it["qty"]}"')
    h = re.sub(r'(name="updates\[\]"\s*\n?\s*value=")1(")', lambda m: m.group(1) + str(it["qty"]) + m.group(2), h)
    h = h.replace('value="1"', f'value="{it["qty"]}"')
    return h

def price_block(unit_cents, compare_cents, line_cents, qty):
    if compare_cents and compare_cents > unit_cents:
        save = (compare_cents - unit_cents) * qty
        return (
            '<div class="cart-item__price-wrapper">'
            '<div class="cart-item__discounted-prices">'
            '<span class="visually-hidden">Regular price</span>'
            f'<s class="cart-item__old-price price--end compare-price color-foreground-text">{money(compare_cents*qty)}</s>'
            '<span class="visually-hidden">Sale price</span>'
            f'<span class="price--end regular-price accent-color-accent-1">{money(line_cents)}</span>'
            "</div></div>"
        )
    return (
        '<div class="cart-item__price-wrapper">'
        f'<span class="price price--end">{money(line_cents)}</span>'
        "</div>"
    )

def row_html(it, idx):
    h = _fill_common(ROW_TPL, it, idx)
    h = h.replace("<!--PRICEBLOCK-->", price_block(it["price"], it.get("compare"), it["price"] * it["qty"], it["qty"]))
    h = re.sub(r"(<div class=\"product-option\">\s*)[^<]*(\s*</div>)", lambda m: m.group(1) + money(it["price"]) + m.group(2), h)
    return h

def li_html(it, idx):
    h = _fill_common(LI_TPL, it, idx)
    pb = price_block(it["price"], it.get("compare"), it["price"] * it["qty"], it["qty"])
    if it.get("compare") and it["compare"] > it["price"]:
        save = (it["compare"] - it["price"]) * it["qty"]
        pb += ("<span class='cart-drawer-item__saving text-color-text'>"
               f"<strong>(You save {money(save)})</strong></span>")
    h = h.replace("<!--PRICEBLOCK-->", pb)
    return h

# ---------------- dynamic bits ----------------
FREE_SHIPPING_THRESHOLD = 4000  # €40 (threshold shown on the demo store)

def savings_total(cart):
    return sum(((it.get("compare") or it["price"]) - it["price"]) * it["qty"] for it in cart.data["items"])

def drawer_totals_block(cart):
    rows = []
    sav = savings_total(cart)
    if sav > 0:
        rows.append(
            "<p class='cart-drawer__totals__row cart-drawer__totals__row--spaced text-color-accent-1' "
            "style='--text-size:1.6rem;'><span><strong>Économies</strong></span>"
            f"<span><strong>-<span class='cart-drawer__totals__row__money'>{money(sav)}</span></strong></span></p>")
    rows.append(
        "<p class='cart-drawer__totals__row cart-drawer__totals__row--spaced text-color-text' "
        "style='--text-size:2.0rem;'><span><strong>Sous-total</strong></span>"
        f"<span><strong><span class='cart-drawer__totals__row__money'>{money(cart.total)}</span></strong></span></p>")
    return ('<div class="cart-drawer__totals" role="status" style=\'--spacing:1.0rem;\'>'
            + "".join(rows) + "</div>")

def update_progress(html, cart):
    """Update the free-shipping progress bar text + width."""
    remaining = max(0, FREE_SHIPPING_THRESHOLD - cart.total)
    pct = min(100.0, (cart.total / FREE_SHIPPING_THRESHOLD) * 100) if FREE_SHIPPING_THRESHOLD else 100
    def txt(m):
        if remaining <= 0:
            return ('<p class="cart-progress__text">'
                    '<span>Vous avez la LIVRAISON OFFERTE !</span></p>')
        return m.group(0).replace(m.group(1), money(remaining))
    html = re.sub(r'<p class="cart-progress__text">(.*?)</p>',
                  lambda m: txt(m) if "LIVRAISON OFFERTE" in m.group(1) else m.group(0),
                  html, flags=re.S)
    # replace the €xx,xx inside the paragraph
    html = re.sub(r'(<p class="cart-progress__text">)(.*?)(</p>)',
                  lambda m: m.group(0) if "LIVRAISON OFFERTE" not in m.group(2) or remaining <= 0
                  else m.group(1) + re.sub(r'<span>€[\d.,]+</span>', f'<span>{money(remaining)}</span>', m.group(2)) + m.group(3),
                  html, flags=re.S)
    html = re.sub(r'(cart-progress__bar__progress[^"]*"\s+style="width:)\s*[\d.]+%', lambda m: f"{m.group(1)} {pct:.4f}%", html)
    return html

def drawer_heading(html, cart):
    word = "article" if cart.count == 1 else "articles"
    return re.sub(r'(<h2 class="drawer__heading">\s*)((?:Cart|Panier)\s*•\s*\d+\s*\w*)(\s*</h2>)',
                  lambda m: f"{m.group(1)}Panier • {cart.count} {word}{m.group(3)}", html)

# ---------------- cart state ----------------
class Cart:
    def __init__(self, sid):
        self.sid = sid
        self.path = os.path.join(CARTS_DIR, f"{sid}.json")
        self.data = {"items": []}
        if os.path.exists(self.path):
            try:
                self.data = json.load(open(self.path))
            except Exception:
                pass

    def save(self):
        json.dump(self.data, open(self.path, "w"))

    # --- API ---
    def add(self, variant_id, qty=1):
        vid = str(variant_id)
        p = BY_VARIANT.get(vid)
        if not p:
            return False
        for it in self.data["items"]:
            if str(it["variant_id"]) == vid:
                it["qty"] += qty
                self.save()
                return True
        self.data["items"].append({
            "variant_id": vid,
            "handle": p["handle"],
            "title": p["title"],
            "price": int(p["price"]),
            "compare": int(p["compare"]) if p.get("compare") else None,
            "image": p["image"],
            "qty": qty,
            "key": uuid.uuid4().hex[:20],
        })
        self.save()
        return True

    def set_qty(self, key_or_vid, qty):
        for it in list(self.data["items"]):
            if str(it["variant_id"]) == str(key_or_vid) or it["key"] == str(key_or_vid) \
               or f"{it['variant_id']}:{it['key']}" == str(key_or_vid):
                it["qty"] = int(qty)
                if it["qty"] <= 0:
                    self.data["items"].remove(it)
                self.save()
                return True
        return False

    def clear(self):
        self.data = {"items": []}
        self.save()

    @property
    def count(self):
        return sum(i["qty"] for i in self.data["items"])

    @property
    def total(self):
        return sum(i["price"] * i["qty"] for i in self.data["items"])

    def shopify_json(self):
        items = []
        for i in self.data["items"]:
            items.append({
                "id": int(i["variant_id"]), "quantity": i["qty"], "variant_id": int(i["variant_id"]),
                "key": f"{i['variant_id']}:{i['key']}", "title": i["title"], "product_title": i["title"],
                "variant_title": None, "handle": i["handle"], "price": i["price"],
                "original_price": i.get("compare") or i["price"],
                "final_line_price": i["price"] * i["qty"],
                "line_price": i["price"] * i["qty"],
                "image": i["image"], "url": f"/products/{i['handle']}",
                "featured_image": {"url": i["image"], "aspect_ratio": 1.0},
            })
        return {
            "token": self.sid, "note": None, "attributes": {},
            "original_total_price": self.total, "total_price": self.total,
            "total_discount": 0, "total_weight": 0, "item_count": self.count,
            "items": items, "currency": "EUR", "requires_shipping": True,
        }

# ---------------- page rendering ----------------
def render_icon_bubble(count):
    if count <= 0:
        return ""
    word = "item" if count == 1 else "items"
    return (f'<span class="visually-hidden">Cart</span>'
            f'<div class="cart-count-bubble"><span aria-hidden="true">{count}</span>'
            f'<span class="visually-hidden">{count} {word}</span>\n  </div>')

def render_drawer(cart):
    """Full <cart-drawer> element markup for current cart (or None to keep static empty markup)."""
    if cart.count == 0:
        return None
    tpl = read(os.path.join(TPL, "drawer_filled.html"))
    tpl = tpl[tpl.find("<cart-drawer"):]
    end = tpl.rfind("</cart-drawer>")
    if end > 0:
        tpl = tpl[:end + len("</cart-drawer>")]
    # items
    items_html = "\n".join(li_html(it, idx) for idx, it in enumerate(cart.data["items"], 1))
    k = tpl.find('<ul class="cart-items list-unstyled">')
    if k > 0:
        start = k + len('<ul class="cart-items list-unstyled">')
        end_ul = tpl.find("</ul>", start)
        tpl = tpl[:start] + items_html + tpl[end_ul:]
    # counts / totals / progress
    tpl = drawer_heading(tpl, cart)
    tpl = re.sub(r'data-subtotal="\d+"', f'data-subtotal="{cart.total}"', tpl)
    tpl = re.sub(r'<div class="cart-drawer__totals".*?</div>\s*</div>', drawer_totals_block(cart) + "</div>", tpl, count=1, flags=re.S)
    tpl = update_progress(tpl, cart)
    return tpl

def render_cart_page(cart):
    """Cart page using the filled shell + current items."""
    tpl = read(os.path.join(TPL, "cart_filled.html"))
    if cart.count == 0:
        return read(os.path.join(SITE, "cart", "index.html"))
    # rows
    k = tpl.find("<tbody>")
    if k > 0:
        start = k + len("<tbody>")
        end = tpl.find("</tbody>", start)
        rows = "\n".join(row_html(it, idx) for idx, it in enumerate(cart.data["items"], 1))
        tpl = tpl[:start] + rows + tpl[end:]
    # subtotal (+ savings row)
    tpl = re.sub(r'(<p class="totals__subtotal-value">)[^<]*(</p>)',
                 lambda m: m.group(1) + f"{money(cart.total)} EUR" + m.group(2), tpl)
    sav = savings_total(cart)
    if sav > 0:
        tpl = tpl.replace('<div class="totals">',
                          '<div class="totals"><p class="totals__subtotal cart-totals__savings" style="color:#8a7a6f;">'
                          f'Économies <span>-{money(sav)}</span></p>', 1)
    tpl = update_progress(tpl, cart)
    tpl = re.sub(r'((?:Cart|Panier)\s*•\s*)\d+(\s*\w*)',
                 lambda m: f"{m.group(1)}{cart.count} " + ("article" if cart.count == 1 else "articles"), tpl)
    return tpl

def hydrate(html, cart):
    """Inject current cart into a static page: drawer + count bubble."""
    if cart.count == 0:
        return html
    drawer = render_drawer(cart)
    if drawer:
        i = html.find("<cart-drawer")
        if i >= 0:
            j = html.find("</cart-drawer>", i)
            if j > 0:
                html = html[:i] + drawer + html[j + len("</cart-drawer>"):]
    # count bubble in header
    bubble = render_icon_bubble(cart.count)
    html = re.sub(r'(<div id="cart-icon-bubble"[^>]*>).*?(</div>)',
                  lambda m: m.group(1) + bubble + m.group(2), html, flags=re.S)
    return html
