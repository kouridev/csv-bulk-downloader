
import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
INVALID_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
RETRYABLE_STATUS_CODES = {408, 425, 429}
THREAD_LOCAL = threading.local()
CHUNK_SIZE = 1024 * 128
MANIFEST_NAME = ".download_manifest.json"


class DownloadLimitError(Exception):
    pass


class EmptyFileError(Exception):
    pass


def build_parser():
    parser = argparse.ArgumentParser(
        prog="csv-bulk-downloader",
        description="Download files from URLs found in CSV files."
    )

    parser.add_argument(
        "--csv",
        dest="csv_path",
        type=Path,
        help="Path to the CSV file."
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Directory used to save downloaded files."
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent downloads. Default: 4."
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay before each request in seconds. Default: 0.15."
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Request timeout in seconds. Default: 30."
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries for temporary failures. Default: 3."
    )

    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=0,
        help="Maximum file size in MB. Zero means unlimited."
    )

    parser.add_argument(
        "--error-file",
        type=Path,
        help="Path used to save download errors."
    )

    parser.add_argument(
        "--user-agent",
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        help="Custom HTTP User-Agent."
    )

    return parser


def find_csv(base_dir):
    csv_files = sorted(base_dir.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(
            "No CSV file found next to the script."
        )

    if len(csv_files) == 1:
        return csv_files[0]

    print("\nAvailable CSV files:")

    for index, file in enumerate(csv_files, 1):
        print(f"{index}. {file.name}")

    choice = input("\nSelect a CSV file: ").strip()

    try:
        selected = int(choice)

        if selected < 1 or selected > len(csv_files):
            raise ValueError

        return csv_files[selected - 1]

    except (ValueError, EOFError):
        raise RuntimeError("Invalid CSV selection.")


def extract_urls(value):
    if not value:
        return []

    urls = []

    for match in URL_PATTERN.findall(str(value)):
        url = match.rstrip(".,;:!?)]}")

        try:
            parsed = urlsplit(url)
        except ValueError:
            continue

        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.netloc
            and url not in urls
        ):
            urls.append(url)

    return urls


def collect_urls(csv_path):
    urls = []

    with csv_path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        reader = csv.reader(file)

        for row in reader:
            for cell in row:
                urls.extend(extract_urls(cell))

    return list(dict.fromkeys(urls))


def is_safe_filename(filename):
    return (
        isinstance(filename, str)
        and filename not in {"", ".", ".."}
        and "/" not in filename
        and "\\" not in filename
        and Path(filename).name == filename
        and not INVALID_FILENAME_CHARS.search(filename)
    )


def get_filename(url):
    path = urlsplit(url).path
    filename = unquote(path).replace("\\", "/").rsplit("/", 1)[-1]

    if not filename:
        filename = "downloaded_file"

    filename = INVALID_FILENAME_CHARS.sub("_", filename)
    filename = filename.strip(" .")

    if not filename:
        filename = "downloaded_file"

    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5",
        "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
        "LPT6", "LPT7", "LPT8", "LPT9"
    }

    if filename.split(".", 1)[0].upper() in reserved_names:
        filename = "_" + filename

    if len(filename) > 180:
        suffix = Path(filename).suffix
        stem = filename[:-len(suffix)] if suffix else filename
        filename = stem[:max(1, 180 - len(suffix))] + suffix

    return filename


def add_url_hash(filename, source):
    digest = hashlib.sha256(
        source.encode("utf-8")
    ).hexdigest()[:10]

    suffix = Path(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename

    return f"{stem}-{digest}{suffix}"


def load_manifest(output_dir):
    manifest_path = output_dir / MANIFEST_NAME

    if not manifest_path.is_file():
        return {}

    try:
        data = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return {}

        return {
            url: filename
            for url, filename in data.items()
            if isinstance(url, str) and is_safe_filename(filename)
        }

    except (OSError, json.JSONDecodeError):
        return {}


def save_manifest(output_dir, manifest):
    manifest_path = output_dir / MANIFEST_NAME
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=output_dir,
            prefix=".download_manifest.",
            suffix=".tmp"
        ) as file:
            temp_path = Path(file.name)

            json.dump(
                manifest,
                file,
                ensure_ascii=False,
                indent=2
            )

            file.write("\n")

        temp_path.replace(manifest_path)

    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def build_destinations(urls, output_dir, manifest):
    base_names = [get_filename(url) for url in urls]

    counts = Counter(
        filename.casefold()
        for filename in base_names
    )

    owners = {}

    for owner_url, filename in manifest.items():
        if is_safe_filename(filename):
            owners.setdefault(filename.casefold(), owner_url)

    used = {}
    destinations = {}

    for url, base_name in zip(urls, base_names):
        stored_name = manifest.get(url)

        if is_safe_filename(stored_name):
            filename = stored_name
        else:
            owner = owners.get(base_name.casefold())
            exists = (output_dir / base_name).exists()

            collision = (
                counts[base_name.casefold()] > 1
                or (owner is not None and owner != url)
                or (exists and owner is None)
            )

            filename = (
                add_url_hash(base_name, url)
                if collision
                else base_name
            )

        owner = owners.get(filename.casefold())

        if owner is not None and owner != url:
            filename = add_url_hash(base_name, url)

        candidate = filename
        serial = 0

        while (
            (
                candidate.casefold() in used
                and used[candidate.casefold()] != url
            )
            or (
                candidate.casefold() in owners
                and owners[candidate.casefold()] != url
            )
            or (
                (output_dir / candidate).exists()
                and owners.get(candidate.casefold()) != url
            )
        ):
            serial += 1
            candidate = add_url_hash(
                base_name,
                f"{url}:{serial}"
            )

        filename = candidate
        used[filename.casefold()] = url
        owners.setdefault(filename.casefold(), url)

        destinations[url] = output_dir / filename

    return destinations


def get_session(user_agent):
    session = getattr(THREAD_LOCAL, "session", None)

    if session is None:
        session = requests.Session()

        session.headers.update({
            "User-Agent": user_agent,
            "Accept": "*/*"
        })

        THREAD_LOCAL.session = session

    return session


def cleanup_temp_file(path):
    if path and path.exists():
        path.unlink()


def download_file(
    url,
    destination,
    timeout,
    retries,
    delay,
    max_bytes,
    user_agent
):
    if destination.is_file() and destination.stat().st_size > 0:
        return (
            "skipped",
            destination.name,
            destination.stat().st_size
        )

    for attempt in range(retries + 1):
        temp_path = None

        if delay > 0:
            time.sleep(delay)

        try:
            session = get_session(user_agent)

            with session.get(
                url,
                stream=True,
                timeout=timeout,
                allow_redirects=True
            ) as response:

                response.raise_for_status()

                content_length = response.headers.get(
                    "Content-Length"
                )

                if max_bytes and content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise DownloadLimitError(
                                "File exceeds the configured size limit."
                            )
                    except ValueError:
                        pass

                downloaded = 0

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".part"
                ) as temp_file:

                    temp_path = Path(temp_file.name)

                    for chunk in response.iter_content(
                        chunk_size=CHUNK_SIZE
                    ):
                        if not chunk:
                            continue

                        downloaded += len(chunk)

                        if max_bytes and downloaded > max_bytes:
                            raise DownloadLimitError(
                                "File exceeds the configured size limit."
                            )

                        temp_file.write(chunk)

                if downloaded == 0:
                    raise EmptyFileError("Downloaded file is empty.")

            temp_path.replace(destination)

            return (
                "downloaded",
                destination.name,
                downloaded
            )

        except DownloadLimitError as exc:
            cleanup_temp_file(temp_path)
            return "error", f"{url} | {exc}", 0

        except EmptyFileError as exc:
            cleanup_temp_file(temp_path)
            return "error", f"{url} | {exc}", 0

        except requests.RequestException as exc:
            cleanup_temp_file(temp_path)

            response = getattr(exc, "response", None)
            status_code = (
                response.status_code
                if response is not None
                else None
            )

            retryable = (
                status_code is None
                or status_code in RETRYABLE_STATUS_CODES
                or 500 <= status_code <= 599
            )

            if attempt < retries and retryable:
                time.sleep(min(2 ** attempt, 30))
                continue

            return (
                "error",
                f"{url} | {type(exc).__name__}: {exc}",
                0
            )

        except Exception as exc:
            cleanup_temp_file(temp_path)

            return (
                "error",
                f"{url} | {type(exc).__name__}: {exc}",
                0
            )

    return "error", f"{url} | Maximum retries exceeded.", 0


def format_size(size):
    value = float(size)

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} TB"


def write_error_report(error_file, errors):
    if errors:
        error_file.parent.mkdir(parents=True, exist_ok=True)

        error_file.write_text(
            "\n".join(errors) + "\n",
            encoding="utf-8"
        )

    elif error_file.exists():
        error_file.unlink()


def resolve_input_path(path):
    path = path.expanduser()

    if not path.is_absolute():
        path = Path.cwd() / path

    return path.resolve()


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be at least 1.")

    if args.delay < 0:
        parser.error("--delay cannot be negative.")

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")

    if args.retries < 0:
        parser.error("--retries cannot be negative.")

    if args.max_size_mb < 0:
        parser.error("--max-size-mb cannot be negative.")

    base_dir = Path(__file__).resolve().parent

    try:
        csv_path = (
            resolve_input_path(args.csv_path)
            if args.csv_path
            else find_csv(base_dir)
        )

        if not csv_path.is_file():
            raise FileNotFoundError(
                f"CSV file not found: {csv_path}"
            )

        output_dir = (
            resolve_input_path(args.output)
            if args.output
            else base_dir / "downloads"
        )

        error_file = (
            resolve_input_path(args.error_file)
            if args.error_file
            else base_dir / "download_errors.txt"
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        print("=" * 60)
        print("CSV Bulk Downloader")
        print("=" * 60)
        print(f"CSV: {csv_path.name}")
        print(f"Output: {output_dir}")
        print(f"Workers: {args.workers}")
        print()

        print("Extracting URLs...")

        urls = collect_urls(csv_path)
        total = len(urls)

        print(f"Unique URLs: {total}")

        if not urls:
            print("No HTTP(S) URLs found.")
            write_error_report(error_file, [])
            return 0

        max_bytes = (
            int(args.max_size_mb * 1024 * 1024)
            if args.max_size_mb > 0
            else 0
        )

        manifest = load_manifest(output_dir)

        destinations = build_destinations(
            urls,
            output_dir,
            manifest
        )

        downloaded = 0
        skipped = 0
        errors = 0
        total_bytes = 0
        error_lines = []

        print("\nStarting downloads...\n")

        with ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:

            futures = {
                executor.submit(
                    download_file,
                    url,
                    destinations[url],
                    args.timeout,
                    args.retries,
                    args.delay,
                    max_bytes,
                    args.user_agent
                ): url
                for url in urls
            }

            for index, future in enumerate(
                as_completed(futures),
                1
            ):
                url = futures[future]

                try:
                    status, result, size = future.result()
                except Exception as exc:
                    status = "error"
                    result = (
                        f"{url} | {type(exc).__name__}: {exc}"
                    )
                    size = 0

                if status == "downloaded":
                    downloaded += 1
                    total_bytes += size
                    manifest[url] = destinations[url].name

                    print(
                        f"[{index}/{total}] DOWNLOADED "
                        f"{result} ({format_size(size)})"
                    )

                elif status == "skipped":
                    skipped += 1
                    total_bytes += size
                    manifest[url] = destinations[url].name

                    print(
                        f"[{index}/{total}] SKIPPED "
                        f"{result}"
                    )

                else:
                    errors += 1
                    error_lines.append(result)

                    print(
                        f"[{index}/{total}] ERROR "
                        f"{result}"
                    )

        try:
            save_manifest(output_dir, manifest)
        except OSError as exc:
            print(f"\nManifest warning: {exc}")

        write_error_report(error_file, error_lines)

        print()
        print("=" * 60)
        print("Download completed.")
        print("=" * 60)
        print(f"Total URLs : {total}")
        print(f"Downloaded : {downloaded}")
        print(f"Skipped    : {skipped}")
        print(f"Errors     : {errors}")
        print(f"Data size  : {format_size(total_bytes)}")

        if errors:
            print(f"Error log  : {error_file}")

        print(f"Output     : {output_dir}")

        return 1 if errors else 0

    except (OSError, RuntimeError, csv.Error) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
