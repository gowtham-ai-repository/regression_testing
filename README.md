# Web Page Element Scanner & Change Detector

Scans web pages, captures screenshots of every visible element (with XPaths), detects structural + visual changes between runs, and generates PDF reports.

---

## Setup in Antigravity IDE

### Step 1 — Open Terminal
In Antigravity, open the integrated terminal (`Ctrl + `` ` or `Terminal > New Terminal`).

### Step 2 — Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3 — ChromeDriver
Selenium 4.10+ includes a built-in driver manager, so Chrome + ChromeDriver should resolve automatically. If your office network blocks this, download ChromeDriver manually from:
```
https://googlechromelabs.github.io/chrome-for-testing/
```
Place `chromedriver` in your PATH or in the project folder.

---

## Usage

### Interactive Mode (type URLs in terminal)
```bash
python web_scanner.py
```
You'll be prompted to enter URLs one by one, then type `done`.

### File Mode (read from txt/csv)
```bash
python web_scanner.py --file urls.txt
```

**urls.txt format** — one URL per line:
```
https://example.com
https://google.com
https://news.ycombinator.com
```

**urls.csv format** — first column is the URL:
```csv
https://example.com
https://google.com
```

Lines starting with `#` are treated as comments and skipped.

---

## How It Works

### First Run (Baseline Scan)
1. Opens each URL in headless Chrome
2. Finds all visible elements (buttons, inputs, headings, images, links, etc.)
3. Collects metadata for each: XPath, text, attributes, computed styles, position, size
4. Takes individual PNG screenshots of every element
5. Takes a full-page screenshot
6. Saves everything as a **baseline** in `scan_data/<url_folder>/`
7. Generates a PDF report with element catalog + full-page screenshot

### Second Run (Change Detection)
1. Re-scans the same URL
2. Loads the saved baseline
3. Compares every element:
   - **Structural**: text changes, position shifts, size changes, attribute diffs, style diffs
   - **Visual**: pixel-level image diff of each element's screenshot
4. Creates a **highlighted screenshot** with red borders around changed elements
5. Generates a PDF report with:
   - Change summary table
   - Full-page screenshot
   - Highlighted screenshot showing changes
   - Detailed list of every change (old value → new value)
   - Lists of added/removed elements
6. Saves current scan as the new baseline

---

## Output Structure

```
project_folder/
├── web_scanner.py
├── requirements.txt
├── scan_data/                          # Baseline storage (auto-created)
│   ├── example.com/
│   │   ├── baseline.json               # Element metadata
│   │   ├── full_page.png               # Full-page screenshot
│   │   ├── highlighted.png             # Changes highlighted (after 2nd run)
│   │   └── elements/                   # Individual element screenshots
│   │       ├── element_0000.png
│   │       ├── element_0001.png
│   │       └── ...
│   └── news.ycombinator.com/
│       └── ...
├── reports/                            # PDF output (or your chosen folder)
│   ├── scan_example.com_20260401_143000.pdf
│   └── ...
```

---

## PDF Report Contents

| Section                  | First Run | Subsequent Runs |
|--------------------------|-----------|-----------------|
| URL + timestamp          | ✅        | ✅              |
| Full-page screenshot     | ✅        | ✅              |
| Change summary table     | —         | ✅              |
| Highlighted screenshot   | —         | ✅              |
| Changed element details  | —         | ✅              |
| Added elements list      | —         | ✅              |
| Removed elements list    | —         | ✅              |
| Element catalog (XPaths) | ✅        | ✅              |

---

## Configuration

Edit the constants at the top of `web_scanner.py`:

| Setting                | Default | Purpose                                      |
|------------------------|---------|----------------------------------------------|
| `SCAN_TAGS`            | 30 tags | HTML tags to scan                            |
| `MIN_WIDTH / MIN_HEIGHT`| 10px   | Ignore elements smaller than this            |
| `PIXEL_DIFF_THRESHOLD` | 30      | Sensitivity for visual change (0-255)        |

---

## Troubleshooting

| Issue                          | Fix                                                  |
|--------------------------------|------------------------------------------------------|
| ChromeDriver not found         | Install Chrome or set `chromedriver` in PATH         |
| Timeout on page load           | Increase `set_page_load_timeout(30)` in the script   |
| Too many elements scanned      | Remove less important tags from `SCAN_TAGS`          |
| Visual diff too sensitive      | Increase `PIXEL_DIFF_THRESHOLD` (e.g., to 50)       |
| PDF too large                  | Reduce element catalog limit (currently 100)         |
