"""
Web Page Element Scanner & Change Detector
============================================
Scans web pages, captures screenshots of all visible elements with XPaths,
detects structural + visual changes between runs, and generates PDF reports.

Usage:
    python web_scanner.py                    # Interactive mode
    python web_scanner.py --file urls.txt    # Read URLs from file
"""

import os
import sys
import json
import time
import hashlib
import argparse
import csv
from datetime import datetime
from io import BytesIO
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    WebDriverException,
    NoSuchElementException,
)

from PIL import Image, ImageChops, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
    Table, TableStyle, PageBreak, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


# ─── Configuration ───────────────────────────────────────────────────────────

BASELINE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_data")

# Visible element tags to scan
SCAN_TAGS = [
    "a", "button", "input", "select", "textarea", "img",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "span", "label", "li", "td", "th",
    "nav", "header", "footer", "section", "article",
    "div", "form", "table", "video", "audio", "svg",
    "iframe",
]

# Minimum element size (pixels) to consider for scanning
MIN_WIDTH = 10
MIN_HEIGHT = 10

# Visual diff threshold (0-255); pixel difference above this = changed
PIXEL_DIFF_THRESHOLD = 30


# ─── Utility Functions ───────────────────────────────────────────────────────

def sanitize_url_to_folder(url: str) -> str:
    """Convert URL to a safe folder name."""
    parsed = urlparse(url)
    name = parsed.netloc + parsed.path
    name = name.replace("/", "_").replace(":", "_").replace("?", "_")
    name = name.replace("&", "_").replace("=", "_").strip("_")
    if len(name) > 100:
        name = name[:80] + "_" + hashlib.md5(url.encode()).hexdigest()[:12]
    return name


def get_urls_interactive() -> list:
    """Prompt user to enter URLs interactively."""
    print("\n╔══════════════════════════════════════════════╗")
    print("║   Web Page Element Scanner & Change Detector ║")
    print("╚══════════════════════════════════════════════╝\n")
    print("Enter URLs to scan (one per line). Type 'done' when finished:\n")

    urls = []
    while True:
        url = input(f"  URL [{len(urls)+1}]: ").strip()
        if url.lower() == "done":
            break
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            urls.append(url)

    if not urls:
        print("No URLs provided. Exiting.")
        sys.exit(0)
    return urls


def get_urls_from_file(filepath: str) -> list:
    """Read URLs from a text or CSV file."""
    urls = []
    ext = os.path.splitext(filepath)[1].lower()

    with open(filepath, "r", encoding="utf-8") as f:
        if ext == ".csv":
            reader = csv.reader(f)
            for row in reader:
                if row:
                    url = row[0].strip()
                    if url and not url.startswith("#"):
                        urls.append(url)
        else:
            for line in f:
                url = line.strip()
                if url and not url.startswith("#"):
                    urls.append(url)

    urls = [u if u.startswith(("http://", "https://")) else "https://" + u for u in urls]
    return urls


def get_output_destination() -> str:
    """Ask user where to save PDF reports."""
    print("\nWhere should the PDF reports be saved?")
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    dest = input(f"  Path [{default}]: ").strip()
    if not dest:
        dest = default
    os.makedirs(dest, exist_ok=True)
    return dest


# ─── Browser Setup ───────────────────────────────────────────────────────────

def create_driver() -> webdriver.Chrome:
    """Create and return a Chrome WebDriver."""
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--log-level=3")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(5)
    return driver


# ─── Element Scanning ────────────────────────────────────────────────────────

JS_GET_XPATH = """
function getXPath(el) {
    if (el.id) return '//*[@id="' + el.id + '"]';
    if (el === document.body) return '/html/body';
    var ix = 0;
    var siblings = el.parentNode ? el.parentNode.childNodes : [];
    for (var i = 0; i < siblings.length; i++) {
        var sibling = siblings[i];
        if (sibling === el) {
            return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + (ix+1) + ']';
        }
        if (sibling.nodeType === 1 && sibling.tagName === el.tagName) ix++;
    }
}
return getXPath(arguments[0]);
"""

JS_GET_COMPUTED_STYLES = """
var cs = window.getComputedStyle(arguments[0]);
return JSON.stringify({
    color: cs.color,
    backgroundColor: cs.backgroundColor,
    fontSize: cs.fontSize,
    fontFamily: cs.fontFamily,
    fontWeight: cs.fontWeight,
    border: cs.border,
    display: cs.display,
    visibility: cs.visibility,
    opacity: cs.opacity
});
"""

JS_GET_ATTRIBUTES = """
var attrs = {};
for (var i = 0; i < arguments[0].attributes.length; i++) {
    var a = arguments[0].attributes[i];
    attrs[a.name] = a.value;
}
return JSON.stringify(attrs);
"""


def is_element_visible(driver, element) -> bool:
    """Check if an element is visible in the viewport."""
    try:
        if not element.is_displayed():
            return False
        size = element.size
        if size["width"] < MIN_WIDTH or size["height"] < MIN_HEIGHT:
            return False
        return True
    except StaleElementReferenceException:
        return False


def handle_cookie_consent(driver):
    """Attempt to click common 'Accept All' cookie consent buttons."""
    common_texts = [
        "accept all", "accept all cookies", "allow all", 
        "allow all cookies", "accept", "i accept"
    ]
    try:
        time.sleep(2)
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            try:
                if btn.is_displayed():
                    text = btn.text.strip().lower()
                    if text in common_texts:
                        print(f"  [Cookie Consent] Found '{btn.text.strip()}' button. Clicking it...")
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        return
            except Exception:
                pass
                
        # Also try anchor tags
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            try:
                if link.is_displayed():
                    text = link.text.strip().lower()
                    if text in common_texts:
                        print(f"  [Cookie Consent] Found '{link.text.strip()}' link. Clicking it...")
                        driver.execute_script("arguments[0].click();", link)
                        time.sleep(1)
                        return
            except Exception:
                pass
    except Exception as e:
        pass

def handle_authentication(driver):
    """Detect login forms, ask user for credentials interactively, and auto fill them."""
    try:
        from selenium.webdriver.common.keys import Keys
        time.sleep(2)
        
        passwords = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        pwd_field = None
        for p in passwords:
            if p.is_displayed():
                pwd_field = p
                break
                
        if pwd_field:
            print("\n  [Authentication] Password field detected. Login may be required.")
            username = input("    Enter username/email (or press Enter to skip): ").strip()
            if not username:
                return
            password = input("    Enter password: ").strip()
            
            text_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email']")
            user_field = None
            for inp in text_inputs:
                if inp.is_displayed():
                    user_field = inp
                    break
            
            if user_field:
                user_field.clear()
                user_field.send_keys(username)
                
            pwd_field.clear()
            pwd_field.send_keys(password)
            
            print("  [Authentication] Credentials entered. Submitting...")
            pwd_field.send_keys(Keys.RETURN)
            time.sleep(5)
            
            handle_cookie_consent(driver)
            
    except Exception as e:
        print(f"  [Authentication] Error: {e}")

def scan_elements(driver, url: str) -> list:
    """Scan all visible elements on the page and collect metadata."""
    print(f"\n  Loading: {url}")
    driver.get(url)

    # Handle cookies and possible authentication
    handle_cookie_consent(driver)
    handle_authentication(driver)

    # Wait for page to load
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    time.sleep(2)  # Extra wait for dynamic content

    # Scroll to bottom and back to trigger lazy-loaded content
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    selector = ", ".join(SCAN_TAGS)
    all_elements = driver.find_elements(By.CSS_SELECTOR, selector)

    print(f"  Found {len(all_elements)} total elements. Filtering visible ones...")

    scanned = []
    for idx, elem in enumerate(all_elements):
        try:
            if not is_element_visible(driver, elem):
                continue

            xpath = driver.execute_script(JS_GET_XPATH, elem)
            if not xpath:
                continue

            location = elem.location
            size = elem.size
            tag = elem.tag_name.lower()
            text = elem.text[:500] if elem.text else ""
            attrs_json = driver.execute_script(JS_GET_ATTRIBUTES, elem)
            styles_json = driver.execute_script(JS_GET_COMPUTED_STYLES, elem)

            element_data = {
                "index": len(scanned),
                "tag": tag,
                "xpath": xpath,
                "text": text,
                "attributes": json.loads(attrs_json) if attrs_json else {},
                "styles": json.loads(styles_json) if styles_json else {},
                "location": {"x": location["x"], "y": location["y"]},
                "size": {"width": size["width"], "height": size["height"]},
            }
            scanned.append(element_data)

        except (StaleElementReferenceException, WebDriverException):
            continue

        if (idx + 1) % 100 == 0:
            print(f"    Processed {idx+1}/{len(all_elements)} elements...")

    print(f"  Scanned {len(scanned)} visible elements.")
    return scanned


def capture_element_screenshots(driver, elements: list, save_dir: str) -> dict:
    """Take individual screenshots of each scanned element. Returns {index: filepath}."""
    os.makedirs(save_dir, exist_ok=True)
    screenshots = {}

    for elem_data in elements:
        xpath = elem_data["xpath"]
        idx = elem_data["index"]
        try:
            el = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.15)

            png_bytes = el.screenshot_as_png
            filepath = os.path.join(save_dir, f"element_{idx:04d}.png")
            with open(filepath, "wb") as f:
                f.write(png_bytes)
            screenshots[idx] = filepath

        except (StaleElementReferenceException, WebDriverException):
            continue

    print(f"  Captured {len(screenshots)} element screenshots.")
    return screenshots


def capture_full_page(driver, save_path: str) -> str:
    """Capture a full-page screenshot."""
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)

    # Get full page dimensions
    total_height = driver.execute_script("return document.body.scrollHeight")
    viewport_width = driver.execute_script("return document.documentElement.clientWidth")

    # Resize window to capture full page in one shot
    driver.set_window_size(viewport_width, min(total_height, 10000))
    time.sleep(0.5)

    driver.save_screenshot(save_path)

    # Reset window size
    driver.set_window_size(1920, 1080)
    return save_path


# ─── Baseline Management ─────────────────────────────────────────────────────

def save_baseline(url: str, elements: list, screenshots: dict, full_page_path: str):
    """Save scan data as baseline for future comparison."""
    folder = os.path.join(BASELINE_ROOT, sanitize_url_to_folder(url))
    os.makedirs(folder, exist_ok=True)

    baseline = {
        "url": url,
        "scanned_at": datetime.now().isoformat(),
        "element_count": len(elements),
        "elements": elements,
        "screenshot_map": {str(k): v for k, v in screenshots.items()},
        "full_page": full_page_path,
    }

    with open(os.path.join(folder, "baseline.json"), "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    print(f"  Baseline saved: {folder}")


def load_baseline(url: str) -> dict | None:
    """Load existing baseline for a URL, if available."""
    folder = os.path.join(BASELINE_ROOT, sanitize_url_to_folder(url))
    bfile = os.path.join(folder, "baseline.json")
    if os.path.exists(bfile):
        with open(bfile, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ─── Change Detection ────────────────────────────────────────────────────────

def detect_structural_changes(old_elem: dict, new_elem: dict) -> list:
    """Compare two element metadata dicts and return a list of changes."""
    changes = []

    # Text content
    if old_elem.get("text", "") != new_elem.get("text", ""):
        changes.append({
            "type": "text_changed",
            "field": "text",
            "old": old_elem.get("text", "")[:200],
            "new": new_elem.get("text", "")[:200],
        })

    # Position shift
    old_loc = old_elem.get("location", {})
    new_loc = new_elem.get("location", {})
    dx = abs(old_loc.get("x", 0) - new_loc.get("x", 0))
    dy = abs(old_loc.get("y", 0) - new_loc.get("y", 0))
    if dx > 5 or dy > 5:
        changes.append({
            "type": "position_changed",
            "field": "location",
            "old": f"({old_loc.get('x')}, {old_loc.get('y')})",
            "new": f"({new_loc.get('x')}, {new_loc.get('y')})",
        })

    # Size change
    old_size = old_elem.get("size", {})
    new_size = new_elem.get("size", {})
    dw = abs(old_size.get("width", 0) - new_size.get("width", 0))
    dh = abs(old_size.get("height", 0) - new_size.get("height", 0))
    if dw > 3 or dh > 3:
        changes.append({
            "type": "size_changed",
            "field": "size",
            "old": f"{old_size.get('width')}x{old_size.get('height')}",
            "new": f"{new_size.get('width')}x{new_size.get('height')}",
        })

    # Attribute changes
    old_attrs = old_elem.get("attributes", {})
    new_attrs = new_elem.get("attributes", {})
    all_keys = set(list(old_attrs.keys()) + list(new_attrs.keys()))
    for key in all_keys:
        if key in ("class", "style"):  # These change often, track them
            pass
        old_val = old_attrs.get(key, "<missing>")
        new_val = new_attrs.get(key, "<missing>")
        if old_val != new_val:
            changes.append({
                "type": "attribute_changed",
                "field": f"@{key}",
                "old": str(old_val)[:100],
                "new": str(new_val)[:100],
            })

    # Key style changes
    old_styles = old_elem.get("styles", {})
    new_styles = new_elem.get("styles", {})
    for prop in ("color", "backgroundColor", "fontSize", "fontWeight", "display", "visibility"):
        if old_styles.get(prop) != new_styles.get(prop):
            changes.append({
                "type": "style_changed",
                "field": f"style.{prop}",
                "old": old_styles.get(prop, ""),
                "new": new_styles.get(prop, ""),
            })

    return changes


def detect_visual_changes(old_img_path: str, new_img_path: str) -> tuple:
    """
    Compare two element screenshots pixel-by-pixel.
    Returns (has_changed: bool, diff_image: PIL.Image | None, diff_percent: float).
    """
    try:
        img_old = Image.open(old_img_path).convert("RGB")
        img_new = Image.open(new_img_path).convert("RGB")

        # Resize to same dimensions if different
        if img_old.size != img_new.size:
            img_old = img_old.resize(img_new.size, Image.LANCZOS)

        diff = ImageChops.difference(img_old, img_new)

        # Calculate percentage of pixels that changed significantly
        pixels = list(diff.getdata())
        total = len(pixels)
        changed = sum(1 for p in pixels if max(p) > PIXEL_DIFF_THRESHOLD)
        diff_pct = (changed / total * 100) if total > 0 else 0

        if diff_pct > 0.5:  # More than 0.5% pixels changed
            return True, diff, round(diff_pct, 2)
        return False, None, 0.0

    except Exception:
        return False, None, 0.0


def compare_scans(baseline: dict, new_elements: list, new_screenshots: dict) -> dict:
    """Full comparison between baseline and new scan."""
    old_elements = baseline.get("elements", [])
    old_screenshot_map = baseline.get("screenshot_map", {})

    # Build XPath lookup for old elements
    old_by_xpath = {e["xpath"]: e for e in old_elements}
    new_by_xpath = {e["xpath"]: e for e in new_elements}

    results = {
        "added_elements": [],
        "removed_elements": [],
        "changed_elements": [],
        "unchanged_count": 0,
        "summary": {},
    }

    # Find removed elements
    for xpath, old_e in old_by_xpath.items():
        if xpath not in new_by_xpath:
            results["removed_elements"].append({
                "xpath": xpath,
                "tag": old_e["tag"],
                "text": old_e.get("text", "")[:100],
            })

    # Find added and changed elements
    for xpath, new_e in new_by_xpath.items():
        if xpath not in old_by_xpath:
            results["added_elements"].append({
                "xpath": xpath,
                "tag": new_e["tag"],
                "text": new_e.get("text", "")[:100],
                "index": new_e["index"],
            })
        else:
            old_e = old_by_xpath[xpath]
            structural = detect_structural_changes(old_e, new_e)

            # Visual comparison
            old_img = old_screenshot_map.get(str(old_e["index"]))
            new_img = new_screenshots.get(new_e["index"])
            visual_changed, diff_img, diff_pct = False, None, 0.0
            if old_img and new_img and os.path.exists(old_img) and os.path.exists(new_img):
                visual_changed, diff_img, diff_pct = detect_visual_changes(old_img, new_img)

            if structural or visual_changed:
                results["changed_elements"].append({
                    "xpath": xpath,
                    "tag": new_e["tag"],
                    "old_index": old_e["index"],
                    "new_index": new_e["index"],
                    "structural_changes": structural,
                    "visual_changed": visual_changed,
                    "visual_diff_pct": diff_pct,
                    "location": new_e.get("location", {}),
                    "size": new_e.get("size", {}),
                })
            else:
                results["unchanged_count"] += 1

    results["summary"] = {
        "total_old": len(old_elements),
        "total_new": len(new_elements),
        "added": len(results["added_elements"]),
        "removed": len(results["removed_elements"]),
        "changed": len(results["changed_elements"]),
        "unchanged": results["unchanged_count"],
    }

    return results


# ─── Highlighted Screenshot ──────────────────────────────────────────────────

def create_highlighted_screenshot(full_page_path: str, changes: list, save_path: str) -> str:
    """Draw red rectangles on the full-page screenshot around changed elements."""
    try:
        img = Image.open(full_page_path).convert("RGB")
        draw = ImageDraw.Draw(img)

        for change in changes:
            loc = change.get("location", {})
            size = change.get("size", {})
            x = loc.get("x", 0)
            y = loc.get("y", 0)
            w = size.get("width", 0)
            h = size.get("height", 0)

            if w > 0 and h > 0:
                # Red rectangle with 3px border
                for offset in range(3):
                    draw.rectangle(
                        [x - offset, y - offset, x + w + offset, y + h + offset],
                        outline="red",
                    )
                # Label
                label = f"{change['tag']} [{change.get('visual_diff_pct', 0)}%]"
                draw.text((x, max(0, y - 15)), label, fill="red")

        img.save(save_path)
        return save_path

    except Exception as e:
        print(f"  Warning: Could not create highlighted screenshot: {e}")
        return full_page_path


# ─── PDF Report Generation ───────────────────────────────────────────────────

def _fit_image(img_path: str, max_width: float, max_height: float) -> RLImage:
    """Create a ReportLab Image that fits within max dimensions."""
    try:
        with Image.open(img_path) as img:
            w, h = img.size
    except Exception:
        return None

    aspect = w / h if h else 1
    rw, rh = max_width, max_width / aspect
    if rh > max_height:
        rh = max_height
        rw = rh * aspect

    return RLImage(img_path, width=rw, height=rh)


def generate_pdf_report(
    url: str,
    elements: list,
    screenshots: dict,
    full_page_path: str,
    comparison: dict | None,
    highlighted_path: str | None,
    output_dir: str,
):
    """Generate a comprehensive PDF report for one URL."""
    safe_name = sanitize_url_to_folder(url)[:60]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = os.path.join(output_dir, f"scan_{safe_name}_{timestamp}.pdf")

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "Title2", parent=styles["Title"], fontSize=16, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "ChangeDetail", parent=styles["Normal"], fontSize=8,
        leftIndent=20, textColor=colors.HexColor("#444444"),
    ))
    styles.add(ParagraphStyle(
        "XPathStyle", parent=styles["Normal"], fontSize=7,
        textColor=colors.HexColor("#666666"), wordWrap="CJK",
    ))

    max_img_w = A4[0] - 1 * inch
    max_img_h = 5 * inch

    story = []

    # ── Title Page ──
    story.append(Paragraph("Web Page Scan Report", styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"<b>URL:</b> {url}", styles["Normal"]))
    story.append(Paragraph(f"<b>Scanned at:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    story.append(Paragraph(f"<b>Elements scanned:</b> {len(elements)}", styles["Normal"]))
    story.append(Spacer(1, 12))

    # ── Summary Table (if comparison exists) ──
    if comparison:
        s = comparison["summary"]
        story.append(Paragraph("Change Summary", styles["Title2"]))
        summary_data = [
            ["Metric", "Count"],
            ["Previous Elements", str(s["total_old"])],
            ["Current Elements", str(s["total_new"])],
            ["Added", str(s["added"])],
            ["Removed", str(s["removed"])],
            ["Changed", str(s["changed"])],
            ["Unchanged", str(s["unchanged"])],
        ]
        t = Table(summary_data, colWidths=[3 * inch, 1.5 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#ecf0f1")]),
        ]))
        story.append(t)
        story.append(Spacer(1, 12))

    # ── Full Page Screenshot ──
    story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Full Page Screenshot", styles["Title2"]))
    fp_img = _fit_image(full_page_path, max_img_w, 7 * inch)
    if fp_img:
        story.append(fp_img)
    story.append(PageBreak())

    # ── Highlighted Screenshot (if changes found) ──
    if highlighted_path and comparison:
        story.append(Paragraph("Changes Highlighted (Red Borders)", styles["Title2"]))
        hl_img = _fit_image(highlighted_path, max_img_w, 7 * inch)
        if hl_img:
            story.append(hl_img)
        story.append(PageBreak())

    # ── Changed Elements Detail ──
    if comparison and comparison["changed_elements"]:
        story.append(Paragraph("Changed Elements — Details", styles["Title2"]))
        story.append(Spacer(1, 6))

        for i, ce in enumerate(comparison["changed_elements"][:50]):  # Limit to 50
            story.append(Paragraph(
                f"<b>#{i+1} &lt;{ce['tag']}&gt;</b>  —  Visual diff: {ce['visual_diff_pct']}%",
                styles["Normal"],
            ))
            story.append(Paragraph(f"XPath: {ce['xpath']}", styles["XPathStyle"]))

            for sc in ce.get("structural_changes", []):
                story.append(Paragraph(
                    f"• <b>{sc['field']}</b>: '{sc['old']}' → '{sc['new']}'",
                    styles["ChangeDetail"],
                ))
            story.append(Spacer(1, 8))

        story.append(PageBreak())

    # ── Added Elements ──
    if comparison and comparison["added_elements"]:
        story.append(Paragraph("Newly Added Elements", styles["Title2"]))
        for ae in comparison["added_elements"][:30]:
            story.append(Paragraph(
                f"• <b>&lt;{ae['tag']}&gt;</b> — {ae.get('text', '')[:80]}",
                styles["Normal"],
            ))
            story.append(Paragraph(f"  XPath: {ae['xpath']}", styles["XPathStyle"]))
        story.append(PageBreak())

    # ── Removed Elements ──
    if comparison and comparison["removed_elements"]:
        story.append(Paragraph("Removed Elements", styles["Title2"]))
        for re_ in comparison["removed_elements"][:30]:
            story.append(Paragraph(
                f"• <b>&lt;{re_['tag']}&gt;</b> — {re_.get('text', '')[:80]}",
                styles["Normal"],
            ))
            story.append(Paragraph(f"  XPath: {re_['xpath']}", styles["XPathStyle"]))
        story.append(PageBreak())

    # ── Element Catalog (first run or reference) ──
    story.append(Paragraph("Scanned Element Catalog", styles["Title2"]))
    story.append(Spacer(1, 6))

    catalog_data = [["#", "Tag", "XPath", "Text (preview)"]]
    for elem in elements[:100]:  # Limit catalog to 100 entries
        catalog_data.append([
            str(elem["index"]),
            elem["tag"],
            elem["xpath"][:60] + ("..." if len(elem["xpath"]) > 60 else ""),
            elem.get("text", "")[:40],
        ])

    if len(catalog_data) > 1:
        t = Table(catalog_data, colWidths=[0.4 * inch, 0.6 * inch, 3.2 * inch, 2.5 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9f9")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)

    # Build PDF
    doc.build(story)
    print(f"  PDF saved: {pdf_path}")
    return pdf_path


# ─── Main Orchestrator ───────────────────────────────────────────────────────

def scan_url(driver, url: str, output_dir: str):
    """Full scan pipeline for a single URL."""
    folder = os.path.join(BASELINE_ROOT, sanitize_url_to_folder(url))
    elem_dir = os.path.join(folder, "elements")
    os.makedirs(elem_dir, exist_ok=True)

    # Step 1: Scan elements
    elements = scan_elements(driver, url)
    if not elements:
        print(f"  No visible elements found for {url}. Skipping.")
        return

    # Step 2: Capture screenshots
    screenshots = capture_element_screenshots(driver, elements, elem_dir)

    # Step 3: Full page screenshot
    fp_path = os.path.join(folder, "full_page.png")
    capture_full_page(driver, fp_path)

    # Step 4: Check for existing baseline
    baseline = load_baseline(url)
    comparison = None
    highlighted_path = None

    if baseline:
        print("  Baseline found! Running change detection...")
        comparison = compare_scans(baseline, elements, screenshots)
        s = comparison["summary"]
        print(f"    Added: {s['added']} | Removed: {s['removed']} | "
              f"Changed: {s['changed']} | Unchanged: {s['unchanged']}")

        if comparison["changed_elements"]:
            highlighted_path = os.path.join(folder, "highlighted.png")
            create_highlighted_screenshot(fp_path, comparison["changed_elements"], highlighted_path)
    else:
        print("  No baseline found. This scan will become the baseline.")

    # Step 5: Save current scan as new baseline
    save_baseline(url, elements, screenshots, fp_path)

    # Step 6: Generate PDF report
    generate_pdf_report(
        url, elements, screenshots, fp_path,
        comparison, highlighted_path, output_dir,
    )


def main():
    parser = argparse.ArgumentParser(description="Web Page Element Scanner & Change Detector")
    parser.add_argument("--file", "-f", help="Path to file containing URLs (txt or csv)")
    args = parser.parse_args()

    # Get URLs
    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
        urls = get_urls_from_file(args.file)
        print(f"\nLoaded {len(urls)} URLs from {args.file}")
    else:
        urls = get_urls_interactive()

    print(f"\nURLs to scan ({len(urls)}):")
    for i, u in enumerate(urls, 1):
        print(f"  {i}. {u}")

    # Get output destination
    output_dir = get_output_destination()

    # Create browser
    print("\nStarting browser...")
    driver = create_driver()

    try:
        for i, url in enumerate(urls, 1):
            print(f"\n{'='*60}")
            print(f"  Scanning [{i}/{len(urls)}]: {url}")
            print(f"{'='*60}")
            try:
                scan_url(driver, url, output_dir)
            except Exception as e:
                print(f"  ERROR scanning {url}: {e}")
                continue
    finally:
        driver.quit()
        print("\nBrowser closed.")

    print(f"\n✅ All done! PDF reports saved to: {output_dir}")
    print(f"   Baseline data stored in: {BASELINE_ROOT}")


if __name__ == "__main__":
    main()
