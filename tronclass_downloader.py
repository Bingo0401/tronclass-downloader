"""Download course attachments using an interactive, authenticated browser."""

import argparse
import asyncio
import getpass
from pathlib import Path
import re
import sys
import threading
import warnings
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error, TimeoutError as PlaywrightTimeout, async_playwright

from credential_store import CredentialStore


DOWNLOAD_LABEL = re.compile(r"download|下載|下载", re.IGNORECASE)


async def prompt(message, secret=False):
    if secret:
        # Read on the main thread so getpass can restore terminal echo on Ctrl-C.
        # No downloads run while a password is requested.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                return getpass.getpass(message)
        except getpass.GetPassWarning:
            raise ValueError("Hidden password input requires an interactive terminal.") from None
    # Process browser events while reading input; a daemon thread also lets Ctrl-C
    # exit without waiting for a blocked executor input() call to finish.
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def finish(value, error):
        if not result.done():
            if error is not None:
                result.set_exception(error)
            else:
                result.set_result(value)

    def read():
        value, error = None, None
        try:
            value = input(message).strip()
        except (EOFError, OSError) as exc:
            error = exc
        try:
            loop.call_soon_threadsafe(finish, value, error)
        except RuntimeError:
            pass  # The user interrupted and the event loop has already closed.

    threading.Thread(target=read, daemon=True).start()
    return await result


def validate_url(value):
    value = value.strip()
    markdown = re.fullmatch(r"\[[^\]]*\]\((https?://.+)\)", value)
    if markdown:
        value = markdown.group(1)
    elif value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Paste a complete http:// or https:// URL.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs must not contain login credentials.")
    return value


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .")
    if not name:
        name = "download"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    # Keep names within common filesystem byte limits, including a collision suffix.
    return name.encode("utf-8")[:200].decode("utf-8", errors="ignore")


def reserve_path(directory, filename):
    """Reserve a unique path without overwriting an existing file or symlink."""
    directory.mkdir(parents=True, exist_ok=True)
    original = Path(safe_filename(filename))
    number = 0
    while True:
        suffix = f" ({number})" if number else ""
        destination = directory / f"{original.stem}{suffix}{original.suffix}"
        try:
            with destination.open("xb"):
                pass
            return destination
        except FileExistsError:
            number += 1


class Downloader:
    def __init__(self, context, directory, timeout=30):
        self.context = context
        self.directory = directory
        self.timeout = timeout
        self.downloads = asyncio.Queue()
        context.on("page", self.watch_page)
        for page in context.pages:
            self.watch_page(page)

    def watch_page(self, page):
        # Downloads can start in a new tab as well as in the original page.
        page.on("download", self.downloads.put_nowait)

    async def save(self, download):
        destination = reserve_path(self.directory, download.suggested_filename)
        try:
            await download.save_as(destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        print(f"\nSaved: {destination.resolve()}")
        print(f"Download URL: {download.url}")
        print("The URL may expire or require your signed-in session.\n")
        return destination

    async def save_pending(self):
        paths = []
        while not self.downloads.empty():
            paths.append(await self.save(self.downloads.get_nowait()))
        return paths

    async def wait_for_download(self):
        try:
            download = await asyncio.wait_for(self.downloads.get(), self.timeout)
        except asyncio.TimeoutError:
            print("No file download started. Check the page in the browser.")
            return []
        paths = [await self.save(download)]
        paths.extend(await self.save_pending())
        return paths

    async def controls(self, page):
        """Find explicit download controls, including controls inside frames."""
        candidates = []
        seen_links = set()
        for frame in page.frames:
            elements = frame.locator('a, button, [role="button"], input[type="button"]')
            for element in await elements.all():
                if not await element.is_visible() or not await element.is_enabled():
                    continue
                label = " ".join(filter(None, [
                    await element.text_content(),
                    await element.get_attribute("aria-label"),
                    await element.get_attribute("title"),
                    await element.get_attribute("value"),
                ]))
                if DOWNLOAD_LABEL.search(label) or await element.get_attribute("download") is not None:
                    href = await element.get_attribute("href")
                    if href and not href.startswith(("#", "javascript:")):
                        link = urljoin(frame.url, href)
                        if link in seen_links:
                            continue
                        seen_links.add(link)
                    candidates.append((element, " ".join(label.split())[:160] or "Download file"))
        return candidates

    async def open_url(self, page, url):
        # Flush any manually initiated download before associating events with this URL.
        await self.save_pending()
        try:
            response = await page.goto(validate_url(url), wait_until="domcontentloaded")
        except Error as exc:
            # Chromium reports a failed navigation when navigation becomes a download.
            if "ERR_ABORTED" in str(exc) or "Download is starting" in str(exc):
                return await self.wait_for_download()
            raise
        if response and response.status >= 400:
            raise ValueError(f"The server returned HTTP {response.status}. Check your login and file access.")
        if not self.downloads.empty():
            return await self.save_pending()

        # TronClass pages can render their controls after DOMContentLoaded.
        candidates = []
        deadline = asyncio.get_running_loop().time() + self.timeout
        while asyncio.get_running_loop().time() < deadline:
            if not self.downloads.empty():
                return await self.save_pending()
            try:
                candidates = await self.controls(page)
            except Error as exc:
                if not any(message in str(exc) for message in (
                    "Frame was detached", "Execution context was destroyed",
                    "Cannot find context with specified id",
                )):
                    raise
                # SSO and the activity app can replace frames while rendering.
                await asyncio.sleep(0.25)
                continue
            if candidates:
                break
            await asyncio.sleep(0.25)

        if not candidates:
            print("No download control found. If the page is still loading, try this URL again.")
            print("You can also click Download in the browser, then enter 'save' here.")
            print("If login has expired, sign in again in the browser first.")
            return []

        index = 0
        if len(candidates) > 1:
            for i, (_, label) in enumerate(candidates, 1):
                print(f"  {i}. {label}")
            choice = await prompt("Choose a file number (Enter to skip): ")
            if not choice:
                return []
            if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
                raise ValueError("Choose one of the listed numbers.")
            index = int(choice) - 1
        print(f"Downloading: {candidates[index][1]}")
        await candidates[index][0].click()
        return await self.wait_for_download()


async def fill_account(page, account):
    if not account:
        return False
    fields = page.locator(
        'input[autocomplete="username"], input[name="username"], '
        'input[name="account"], input[name="userName"], input[type="email"]'
    )
    for field in await fields.all():
        if await field.is_visible() and await field.is_editable():
            await field.fill(account)
            return True
    return False


def origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def trusted_login_url(site, login_url):
    allowed = {origin(site)}
    if origin(site) == "https://elearn.nsysu.edu.tw":
        allowed.add("https://identity.nsysu.edu.tw")
    return origin(login_url) in allowed


async def automatic_login(page, site, account, password, timeout):
    """Submit one login attempt, only on the requested site or NSYSU's SSO."""
    await page.goto(site, wait_until="domcontentloaded")
    password_field = page.locator('input[type="password"]:visible').first
    try:
        await password_field.wait_for(state="visible", timeout=timeout * 1000)
    except PlaywrightTimeout:
        raise ValueError("No supported login form found. Try --manual-login.") from None
    if not trusted_login_url(site, page.url):
        raise ValueError("This site's SSO provider is not configured. Use --manual-login.")

    try:
        if not await fill_account(page, account):
            raise ValueError("Account field not recognized. Use --manual-login.")
        await password_field.fill(password)
        form = password_field.locator("xpath=ancestor::form").first
        submit = form.locator('button[type="submit"], input[type="submit"]').first
        await submit.click()
    except Error:
        # Playwright errors can include filled values; never expose those errors.
        raise ValueError("Could not submit the login form. Try --manual-login.") from None
    finally:
        password = None

    try:
        await page.wait_for_function(
            """expected => location.origin === expected &&
                !Array.from(document.querySelectorAll('input[type="password"]'))
                    .some(e => e.getClientRects().length)""",
            arg=origin(site), timeout=timeout * 1000,
        )
    except PlaywrightTimeout:
        raise ValueError(
            "Login was not completed. Check your credentials, or use --manual-login "
            "if verification is required. No automatic retry was attempted."
        ) from None
    print("Signed in.")


async def read_credentials(manual_login, store=None, site=None, use_saved=True):
    if not manual_login and store is not None and use_saved:
        saved = store.load(origin(site))
        if saved is not None:
            print(f"Using saved account: {saved[0]}")
            return saved
    account = await prompt("Account / student ID: ")
    password = None
    if not manual_login:
        if not account:
            raise ValueError("An account is required for automatic login.")
        password = await prompt("Password (hidden): ", secret=True)
        if not password:
            raise ValueError("A password is required for automatic login.")
    return account, password


async def sign_in(browser, args, site, account, password):
    """Use a fresh context so cookies and browser storage never cross accounts."""
    context = await browser.new_context(accept_downloads=True)
    try:
        context.set_default_timeout(args.timeout * 1000)
        downloader = Downloader(context, args.output, args.timeout)
        page = await context.new_page()
        if args.manual_login:
            await page.goto(site, wait_until="domcontentloaded")
            await fill_account(page, account)
            print("Complete login in the browser, including your password and any verification.")
            await prompt("Once you can see your courses, press Enter here: ")
        else:
            print("Signing in...")
            await automatic_login(page, site, account, password, args.timeout)
            store = getattr(args, "credential_store", None)
            if store is not None:
                store.save(origin(site), account, password)
        return downloader, page
    except BaseException:
        await context.close()
        raise
    finally:
        password = None


async def command_loop(browser, args, site, downloader, page):
    print("Paste another URL, or type 'switch', 'forget', 'save', or 'quit'.")
    while True:
        value = await prompt("TronClass URL / command: ")
        command = value.lower()
        if command in {"quit", "exit", "q"}:
            if downloader is not None:
                await downloader.save_pending()
            break
        if not value:
            continue
        if command == "forget":
            store = getattr(args, "credential_store", None) or CredentialStore()
            store.forget(origin(site))
            continue
        if command in {"switch", "switch account"}:
            try:
                if downloader is not None:
                    await downloader.save_pending()
                    await downloader.context.close()
                downloader, page = None, None
                print("Previous session cleared. Enter the next account for this site.")
                account, password = await read_credentials(args.manual_login, use_saved=False)
                try:
                    downloader, page = await sign_in(browser, args, site, account, password)
                finally:
                    password = None
                print("Account switched. Paste a material URL to download.")
            except (Error, ValueError, OSError) as exc:
                print(f"Could not switch accounts: {exc}")
                print("Type 'switch' to try again, or 'quit'.")
            continue
        if downloader is None:
            print("No account is signed in. Type 'switch' to sign in, or 'quit'.")
            continue
        try:
            if command == "save":
                await downloader.wait_for_download()
            else:
                if page.is_closed():
                    page = await downloader.context.new_page()
                await downloader.open_url(page, value)
        except PlaywrightTimeout:
            print("The page or download timed out. Check the browser and try again.")
        except (Error, ValueError, OSError) as exc:
            print(f"Could not download: {exc}")


async def run(args):
    target = validate_url(args.url or await prompt("TronClass material URL: "))
    site = validate_url(args.site) if args.site else target
    args.credential_store = None if args.no_remember or args.manual_login else CredentialStore()
    account, password = await read_credentials(args.manual_login, args.credential_store, site)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not (args.manual_login or args.show_browser))
        try:
            try:
                downloader, page = await sign_in(browser, args, site, account, password)
            finally:
                password = None
            print("Downloading the requested material...")
            # Navigate again because SSO redirects can discard the activity fragment.
            await downloader.open_url(page, target)
            await command_loop(browser, args, site, downloader, page)
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="Material URL (prompted if omitted)")
    parser.add_argument("--site", help="Your school's TronClass login URL")
    parser.add_argument("--manual-login", action="store_true", help="Sign in manually in a visible browser")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser during automatic login and downloading")
    parser.add_argument("--no-remember", action="store_true", help="Do not read or save remembered credentials")
    parser.add_argument("--output", type=Path, default=Path("downloads"), help="Download folder (default: downloads)")
    parser.add_argument("--timeout", type=float, default=30, help="Page/download-start timeout in seconds")
    args = parser.parse_args()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a finite positive number")
    try:
        asyncio.run(run(args))
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye.")
    except (Error, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
