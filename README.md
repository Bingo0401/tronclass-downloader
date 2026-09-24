# TronClass downloader

Made by students at National Sun Yat-sen University (NSYSU), primarily for use by
NSYSU students. This is an independent student project, not an official university
tool.

A Python command-line tool that accepts a course material URL, signs in,
downloads the file, and prints its download URL. It supports
NSYSU's TronClass SSO login. If a page has several different downloadable files,
you choose which one to use.

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

1. Run `bash run.sh`.
2. Paste your TronClass material page URL, including its `#/...` activity ID.
3. On the first login for that site, enter your account or student ID and password.
   The password prompt is hidden (no characters appear as you type).
4. After a successful automatic login, the account is remembered in your OS
   credential store. Later runs reuse it for that site.
5. The program signs in and saves the file under `downloads/`. Paste another URL,
   type `switch` to change accounts, or type `quit`.

To switch accounts, type `switch` (or `switch account`) at the URL prompt. The
program saves any pending downloads, closes the previous browser session, and
asks for the next account and hidden password on the same site. After login,
paste a material URL. Already downloaded files are kept. In `--manual-login` mode,
complete the new account's login in a fresh browser window instead. If the new
login fails, type `switch` to try again; the previous session is not reused.
A successful switch replaces the remembered account for that site.

For example, you can supply the material URL on launch and then enter your account
and password when prompted:

```sh
bash run.sh 'https://elearn.nsysu.edu.tw/course/36825/learning-activity#/110631'
```

Plain URLs, Markdown links (`[Material](https://...)`), and URLs inside angle
brackets are accepted. Use `--output ./materials` to change the destination.
Automatic mode runs without opening a browser window; add `--show-browser` to
watch it work.

For a school with a different SSO provider, or if login requires interactive
verification, use the browser login mode:

```sh
bash run.sh --manual-login
```

In that mode, enter your material URL and account, complete login in the browser,
then press Enter in the terminal. The optional `--site` flag specifies a separate
login URL when needed.

Existing files are preserved by adding a numbered suffix to new filenames.
Download URLs may expire or require the same session, so the saved file is the
durable result.

## Remembered accounts and Git

Both the account name and password are stored outside the repository in macOS
Keychain or Windows Credential Manager through [keyring](https://keyring.readthedocs.io/en/latest/).
No credentials file is created in the project, so saved logins are not included
in Git commits or pushes. The OS may prompt you to allow credential-store access.

One successful automatic login is remembered per site, under the service name
`tronclass-downloader:<site-origin>` (for example,
`tronclass-downloader:https://elearn.nsysu.edu.tw`), with the entry name `login`.
Credentials are never shared between different site origins. Browser sessions
are still temporary; each launch signs in again with the remembered credentials.

Type `forget` at the URL prompt to remove the remembered login for the current
site. This keeps the current session open. You can also delete the entry through
your OS credential manager.

To enter credentials without reading or updating saved logins:

```sh
bash run.sh --no-remember
```

If a remembered password has changed, use `--no-remember` to sign in with the new
password, then type `forget` to remove the old saved login. The next normal launch
will prompt for credentials and remember the successful login.

Manual browser login does not read or store passwords. If the native credential
store is unavailable or access is denied, the program uses interactive login;
it never falls back to a plaintext password file. There is no password command-line
option. Downloads and Python environment files remain excluded by `.gitignore`.

## Compatibility

Automatic login supports NSYSU's `elearn.nsysu.edu.tw` to
`identity.nsysu.edu.tw` SSO redirect and conventional login forms on the same origin
as the supplied login URL. Other SSO providers require `--manual-login`. A failed
login stops without retrying the password automatically.

The account-to-download flow has been verified on macOS against the NSYSU
material URL above, successfully saving its PDF. Windows and other university
installations have not been verified.

The downloader recognizes explicit English and Chinese download controls, HTML
download links, and direct attachment URLs once signed in. It also watches
downloads in newly opened tabs and preserves the activity ID after SSO redirects.

If a control is not recognized, rerun with `--manual-login`, click Download in the
browser, and type `save` in the terminal. A preview-only page (including an inline PDF) may require using its
own download button. Files without a permitted download action are not extracted
from previews. If your session expires, sign in again in the same browser and
paste the URL again.

Browser sessions and downloads use [Playwright](https://playwright.dev/python/docs/downloads).

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests run Chromium headlessly against a local HTTP server. They cover automatic
login, failed credentials, trusted login origins, authenticated downloads, attachment
URLs, popups, replaced frames, delayed controls, multiple choices, duplicate links
and filenames, and failed downloads. Credential tests use a fake vault to check
remembering, forgetting, site isolation, and failures. Tests do not contact
TronClass or access your OS credential store.

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
