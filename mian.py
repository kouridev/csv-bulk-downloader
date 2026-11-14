import csv
import re
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests


# ============================================================
# تنظیمات
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "downloads"
ERROR_FILE = BASE_DIR / "download_errors.txt"

# اگر اسم CSV را می‌دانی اینجا وارد کن.
# اگر None باشد، اولین فایل CSV کنار اسکریپت انتخاب می‌شود.
CSV_FILE = None

REQUEST_DELAY = 0.15
TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)


# ============================================================
# پیدا کردن فایل CSV
# ============================================================

def find_csv():
    if CSV_FILE:
        path = BASE_DIR / CSV_FILE

        if not path.exists():
            raise FileNotFoundError(f"CSV پیدا نشد: {path}")

        return path

    csv_files = list(BASE_DIR.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(
            "هیچ فایل CSV کنار فایل پایتون پیدا نشد."
        )

    if len(csv_files) > 1:
        print("چند فایل CSV پیدا شد:")

        for i, file in enumerate(csv_files, 1):
            print(f"{i}. {file.name}")

        choice = input("شماره فایل موردنظر را وارد کن: ").strip()

        try:
            return csv_files[int(choice) - 1]
        except (ValueError, IndexError):
            raise RuntimeError("انتخاب فایل CSV نامعتبر است.")

    return csv_files[0]


# ============================================================
# استخراج URLها
# ============================================================

def extract_urls(text):
    if not text:
        return []

    # URLهای مستقیم
    urls = re.findall(
        r'https?://[^\s<>"\')\],]+',
        text
    )

    # در صورت وجود Markdown:
    # [https://example.com/file.webp](https://example.com/file.webp)
    markdown_urls = re.findall(
        r'\]\((https?://[^)\s]+)\)',
        text
    )

    urls.extend(markdown_urls)

    # تمیزکاری
    cleaned = []

    for url in urls:
        url = url.strip()
        url = url.rstrip(".,;")

        if url not in cleaned:
            cleaned.append(url)

    return cleaned


# ============================================================
# خواندن CSV
# ============================================================

def collect_urls(csv_path):
    urls = []

    with open(
        csv_path,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.reader(f)

        for row in reader:
            for cell in row:
                urls.extend(extract_urls(cell))

    # حذف تکراری‌ها بدون تغییر ترتیب
    unique_urls = list(dict.fromkeys(urls))

    return unique_urls


# ============================================================
# تعیین نام فایل
# ============================================================

def get_filename(url):
    path = urlparse(url).path
    filename = unquote(Path(path).name)

    if not filename:
        filename = "downloaded_file"

    return filename


# ============================================================
# دانلود
# ============================================================

def download_file(session, url, output_dir):
    filename = get_filename(url)
    destination = output_dir / filename

    # اگر قبلاً دانلود شده
    if destination.exists() and destination.stat().st_size > 0:
        return "skipped", filename

    temp_file = destination.with_suffix(
        destination.suffix + ".part"
    )

    try:
        with session.get(
            url,
            stream=True,
            timeout=TIMEOUT,
            allow_redirects=True,
        ) as response:

            response.raise_for_status()

            with open(temp_file, "wb") as f:
                for chunk in response.iter_content(
                    chunk_size=1024 * 128
                ):
                    if chunk:
                        f.write(chunk)

        temp_file.replace(destination)

        return "downloaded", filename

    except Exception as e:

        if temp_file.exists():
            temp_file.unlink()

        return "error", f"{url} | {type(e).__name__}: {e}"


# ============================================================
# اجرای اصلی
# ============================================================

def main():

    print("=" * 60)
    print("Bulk File Downloader")
    print("=" * 60)

    try:
        csv_path = find_csv()
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nEnter برای خروج...")
        return

    print(f"\nCSV: {csv_path.name}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("در حال خواندن لینک‌ها...")

    urls = collect_urls(csv_path)

    print(f"تعداد URLهای یکتا: {len(urls)}")
    print(f"محل ذخیره: {OUTPUT_DIR}")
    print()

    if not urls:
        print("هیچ URLای پیدا نشد.")
        input("\nEnter برای خروج...")
        return

    session = requests.Session()

    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    })

    downloaded = 0
    skipped = 0
    errors = 0

    error_lines = []

    total = len(urls)

    for index, url in enumerate(urls, 1):

        status, result = download_file(
            session,
            url,
            OUTPUT_DIR
        )

        if status == "downloaded":
            downloaded += 1
            print(
                f"[{index}/{total}] OK      {result}"
            )

        elif status == "skipped":
            skipped += 1
            print(
                f"[{index}/{total}] SKIP    {result}"
            )

        else:
            errors += 1
            error_lines.append(result)

            print(
                f"[{index}/{total}] ERROR   {result}"
            )

        time.sleep(REQUEST_DELAY)

    # ذخیره خطاها
    if error_lines:
        with open(
            ERROR_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            for line in error_lines:
                f.write(line + "\n")

    print()
    print("=" * 60)
    print("تمام شد.")
    print("=" * 60)
    print(f"کل لینک‌ها : {total}")
    print(f"دانلود شد  : {downloaded}")
    print(f"قبلاً بود   : {skipped}")
    print(f"خطا         : {errors}")

    if errors:
        print(f"\nلیست خطاها: {ERROR_FILE}")

    print(f"\nفایل‌ها در: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()