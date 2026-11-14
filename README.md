# CSV Bulk Downloader

A lightweight Python CLI tool for downloading files from HTTP(S) URLs extracted from CSV files.

**CSV Bulk Downloader** scans every cell in a CSV file, extracts unique URLs, and downloads the corresponding files into a local directory.

## Features

- Extract HTTP(S) URLs from all CSV columns and rows
- Support direct URLs and Markdown links
- Remove duplicate URLs while preserving their original order
- Download multiple files concurrently
- Stream large files without loading them entirely into memory
- Automatically retry temporary network failures
- Detect filename collisions and generate unique filenames
- Keep a download manifest to identify previously downloaded files
- Skip previously downloaded non-empty files
- Save files atomically to prevent incomplete downloads
- Configure request timeout, retry count, delay, and maximum file size
- Generate an error report for failed downloads
- Support custom output directories and HTTP User-Agent headers

## Requirements

- Python 3.9 or newer
- pip

## Installation

Clone the repository:

```bash
git clone https://github.com/USERNAME/csv-bulk-downloader.git
cd csv-bulk-downloader
```

Create a virtual environment:

**Windows**

```bash
py -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Usage

Place your CSV file next to `downloader.py`.

Run the program:

```bash
python downloader.py
```

If there is only one CSV file, it will be selected automatically.

If multiple CSV files are found, the program will ask you to select one.

You can also specify a CSV file directly:

```bash
python downloader.py --csv products.csv
```

## Command-line Options

| Option | Default | Description |
|---|---|---|
| `--csv` | Auto-detect | CSV file path |
| `--output` | `downloads/` | Download destination |
| `--workers` | `4` | Number of concurrent downloads |
| `--delay` | `0.15` | Delay before each request, in seconds |
| `--timeout` | `30` | Request timeout, in seconds |
| `--retries` | `3` | Number of additional attempts |
| `--max-size-mb` | `0` | Maximum size per file in MB; zero means unlimited |
| `--error-file` | `download_errors.txt` | Error report path |
| `--user-agent` | Default browser UA | Custom HTTP User-Agent |

Display all available options:

```bash
python downloader.py --help
```

## Examples

Download a CSV using the default settings:

```bash
python downloader.py --csv products.csv
```

Download files into a custom directory:

```bash
python downloader.py --csv products.csv --output ./media
```

Use eight concurrent workers:

```bash
python downloader.py --csv products.csv --workers 8
```

Limit each downloaded file to 100 MB:

```bash
python downloader.py --csv products.csv --max-size-mb 100
```

Customize timeout and retry settings:

```bash
python downloader.py --csv products.csv --timeout 60 --retries 5
```

Combine multiple options:

```bash
python downloader.py --csv products.csv --output ./downloads --workers 4 --delay 0.2 --timeout 30 --retries 3 --max-size-mb 250
```

## CSV Format

The program does not require specific column names or a predefined CSV structure.

It scans every cell in every row and extracts supported HTTP(S) URLs.

Example:

| Product | Image URL | Additional URL |
|---|---|---|
| Product 1 | https://example.com/image-01.webp | https://example.com/image-02.jpg |
| Product 2 | https://example.com/image-03.png | https://example.com/file.pdf |

URLs embedded in Markdown links are also supported.

## Output Structure

After running the program, the output directory will contain the downloaded files and an internal manifest:

```text
csv-bulk-downloader/
│
├── downloader.py
├── requirements.txt
├── README.md
├── LICENSE
│
├── downloads/
│   ├── image-01.webp
│   ├── image-02.jpg
│   ├── image-03.png
│   └── .download_manifest.json
│
└── download_errors.txt
```

The error report is created only when downloads fail. An outdated error report is removed after a successful run without errors.

## Download Management

The downloader uses a manifest to associate URLs with their downloaded filenames.

This helps prevent filename conflicts between different URLs, including URLs with identical filenames on different domains.

Previously downloaded non-empty files are skipped when their URL and filename are recorded in the manifest.

Incomplete downloads are stored temporarily and are only moved to their final destination after successful completion.

## Error Handling

Temporary network errors are retried using exponential backoff.

HTTP 408, 425, 429, and 5xx responses are eligible for retries.

Other HTTP errors are reported without unnecessary retries.

Failed downloads are recorded in `download_errors.txt`, including their URL and error details.

## Notes

- Only HTTP and HTTPS URLs are supported.
- The program downloads directly from supplied URLs and does not crawl websites.
- File extensions are taken from the URL path.
- The program does not verify the actual content type of downloaded files.
- Files with an existing non-empty destination are not downloaded again when their URL is correctly recorded in the manifest.
- Concurrent downloads should be configured responsibly to avoid overloading remote servers.
- Download only files that you are authorized to access and store.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.