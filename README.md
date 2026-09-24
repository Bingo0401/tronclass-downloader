# TronClass downloader

A Python command-line tool that opens your school's login page, asks for your
account, and then accepts course material URLs. It clicks an available download
control, saves the file, and prints the download URL. If a page has several download
controls, you choose which one to use.

## Setup

Requires Python 3.10+ and a desktop environment. On macOS, use Terminal; on
Windows, run these Bash scripts in Git Bash.

```sh
bash setup.sh
```

This creates `.venv`, installs the Python requirements, and downloads Chromium.
Run it once initially, or again when requirements change. To start the program:

```sh
bash run.sh
```

Both scripts work from any directory when called by their path. No virtual
environment activation is needed. Downloads default to the project's `downloads/`
folder. Windows Command Prompt and PowerShell do not run Bash scripts directly.

## Use

1. Enter your university's full TronClass login URL.
2. Enter your account or student ID. The tool fills common username fields when
   it recognizes them. Complete your password, SSO, and verification in the browser.
3. When you can see your courses, press Enter in the terminal.
4. Paste a course material page URL or an attachment URL that starts a download.
5. Files are saved under `downloads/`. Paste another URL, or type `quit`.

You can supply your login URL and destination on launch:

```sh
bash run.sh --site https://YOUR-SCHOOL-TRONCLASS-HOST/ --output ./materials
```

Existing files are preserved by adding a numbered suffix to new filenames. The
password is entered only in the browser. The tool does not persist your login
session; closing it requires signing in next time. Download URLs may expire or
require the same session, so the saved file is the durable result.

## Compatibility

This is a generic browser-based implementation, not yet verified against a
particular university's TronClass installation. It recognizes explicit English
and Chinese download controls, HTML download links, and direct attachment URLs.
It also watches downloads in newly opened tabs.

If a control is not recognized, click Download in the browser and type `save` in
the terminal. A preview-only page (including an inline PDF) may require using its
own download button. Files without a permitted download action are not extracted
from previews. If your session expires, sign in again in the same browser and
paste the URL again.

Browser sessions and downloads use [Playwright](https://playwright.dev/python/docs/downloads).
The login and selectors can be tailored once your school's URL and an example
material page are known.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests run Chromium headlessly against a local HTTP server. They cover authenticated
downloads, attachment URLs, popups, frames, delayed controls, multiple choices,
duplicate filenames, and failed downloads. They do not contact TronClass.

## Legal disclaimer

This is an independent project and is not affiliated with, endorsed by, or
sponsored by TronClass or any educational institution.

Use this tool only with accounts and materials you are authorized to access and
download. You are responsible for complying with applicable laws, copyright and
licensing restrictions, platform terms of service, and your institution's policies.
Do not use it to bypass access controls or download restrictions, share credentials,
or redistribute materials without permission.

The software is provided "as is," without warranties of any kind. To the extent
permitted by applicable law, the authors and contributors disclaim liability for
any loss, damage, or other consequences arising from its use. Use it at your own
risk.
