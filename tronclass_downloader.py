"""Download course attachments using an interactive, authenticated browser."""

import argparse
import asyncio
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

from playwright.async_api import Error, TimeoutError as PlaywrightTimeout, async_playwright


DOWNLOAD_LABEL = re.compile(r"download|下載|下载", re.IGNORECASE)


async def prompt(message):
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
        for _ in range(20):
            if not self.downloads.empty():
                return await self.save_pending()
            candidates = await self.controls(page)
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
        return
    fields = page.locator(
        'input[autocomplete="username"], input[name="username"], '
        'input[name="account"], input[name="userName"], input[type="email"]'
    )
    for field in await fields.all():
        if await field.is_visible() and await field.is_editable():
            await field.fill(account)
            return
    print("Enter your account in the browser; this login form was not recognized.")


async def run(args):
    site = validate_url(args.site or await prompt("Your school's TronClass login URL: "))
    account = await prompt("Account / student ID (Enter to type it in the browser): ")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            context = await browser.new_context(accept_downloads=True)
            context.set_default_timeout(args.timeout * 1000)
            downloader = Downloader(context, args.output, args.timeout)
            page = await context.new_page()
            await page.goto(site, wait_until="domcontentloaded")
            await fill_account(page, account)
            print("Complete login in the browser, including your password and any verification.")
            await prompt("Once you can see your courses, press Enter here: ")
            print("Paste a course material URL to download it. Type 'save' for a manual download, or 'quit'.")
            while True:
                value = await prompt("TronClass URL: ")
                if value.lower() in {"quit", "exit", "q"}:
                    await downloader.save_pending()
                    break
                if not value:
                    continue
                try:
                    if value.lower() == "save":
                        await downloader.wait_for_download()
                    else:
                        if page.is_closed():
                            page = await context.new_page()
                        await downloader.open_url(page, value)
                except PlaywrightTimeout:
                    print("The page or download timed out. Check the browser and try again.")
                except (Error, ValueError, OSError) as exc:
                    print(f"Could not download: {exc}")
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="Your school's TronClass login URL")
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
