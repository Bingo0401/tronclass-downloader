"""Browser integration tests against a local, cookie-protected course site."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error, async_playwright

from credential_store import CredentialStore
from test_credentials import FakeVault

from tronclass_downloader import (
    Downloader, automatic_login, command_loop, reserve_path, sign_in, trusted_login_url, validate_url,
)


PAYLOAD = b"Course attachment\n"
LOGIN_FORM = b'''<form method="post" action="/authenticate">
<input name="username"><input name="password" type="password">
<button type="submit">Sign in</button></form>'''


class CourseSite(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/signin":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            body = LOGIN_FORM
        elif self.path == "/login":
            self.send_response(200)
            self.send_header("Set-Cookie", "session=test; Path=/; HttpOnly")
            body = b"Signed in"
        elif self.headers.get("Cookie") not in {"session=test", "session=second"}:
            self.send_response(403)
            body = b"Sign in first"
        elif urlsplit(self.path).path == "/file":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="lesson.txt"')
            body = PAYLOAD
            if self.headers.get("Cookie") == "session=second":
                body += b"Second account\n"
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            pages = {
                "/course": '<a href="/file">下載教材</a>',
                "/popup": '<a href="/file" target="_blank">Download</a>',
                "/frame": '<iframe src="/course"></iframe>',
                "/multiple": '<a href="/file?one">Download one</a><a href="/file?two">Download two</a>',
                "/duplicate": '<a href="/file">Download</a><a href="/file">下載</a>',
                "/delayed": '<script>setTimeout(() => {document.body.innerHTML = \'<a href="/file">Download</a>\'}, 500)</script>',
            }
            body = pages.get(self.path, "No file here").encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        credentials = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if credentials in [
            {"username": ["student"], "password": ["test password"]},
            {"username": ["second"], "password": ["test password"]},
        ]:
            self.send_response(303)
            session = "second" if credentials["username"] == ["second"] else "test"
            self.send_header("Set-Cookie", f"session={session}; Path=/; HttpOnly")
            self.send_header("Location", "/course")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(LOGIN_FORM)))
            self.end_headers()
            self.wfile.write(LOGIN_FORM)


class PathTests(unittest.TestCase):
    def test_no_overwrite_or_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            first = reserve_path(directory, "../lesson.txt")
            first.write_bytes(PAYLOAD)
            second = reserve_path(directory, "../lesson.txt")
            self.assertEqual(first.parent, directory)
            self.assertEqual(second.parent, directory)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), PAYLOAD)

    def test_reject_invalid_urls_and_embedded_password(self):
        for url in ["javascript:alert(1)", "file:///tmp/file", "https://", "https://user:secret@school.example"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_markdown_link_preserves_activity_fragment(self):
        url = "https://elearn.nsysu.edu.tw/course/123/learning-activity#/456"
        self.assertEqual(validate_url(f"[Material]({url})"), url)
        self.assertEqual(validate_url(f"<{url}>"), url)

    def test_credentials_only_go_to_expected_login_origin(self):
        site = "https://elearn.nsysu.edu.tw/course/123"
        self.assertTrue(trusted_login_url(site, "https://identity.nsysu.edu.tw/auth/login"))
        self.assertFalse(trusted_login_url(site, "http://identity.nsysu.edu.tw/auth/login"))
        self.assertFalse(trusted_login_url(site, "https://identity.nsysu.edu.tw.attacker.example/"))
        self.assertFalse(trusted_login_url(site, "https://other-school.example/login"))


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CourseSite)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.folder = tempfile.TemporaryDirectory()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(accept_downloads=True)
        self.downloader = Downloader(self.context, Path(self.folder.name), timeout=5)
        self.page = await self.context.new_page()
        await self.page.goto(self.base + "/login")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    async def test_authenticated_downloads(self):
        paths = []
        for route in ["/course", "/file", "/popup", "/frame", "/delayed", "/multiple"]:
            with self.subTest(route=route), patch("tronclass_downloader.prompt", new=AsyncMock(return_value="2")):
                results = await self.downloader.open_url(self.page, self.base + route)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].read_bytes(), PAYLOAD)
                paths.extend(results)
        self.assertEqual(len(set(paths)), len(paths))

    async def test_unauthenticated_response_does_not_save_html(self):
        await self.context.clear_cookies()
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            await self.downloader.open_url(self.page, self.base + "/file")
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    async def test_automatic_login_then_download(self):
        await self.context.clear_cookies()
        await automatic_login(self.page, self.base + "/signin", "student", "test password", 5)
        results = await self.downloader.open_url(self.page, self.base + "/course#/456")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].read_bytes(), PAYLOAD)
        self.assertTrue(self.page.url.endswith("#/456"))

    async def test_wrong_password_stops_without_saving(self):
        await self.context.clear_cookies()
        with self.assertRaisesRegex(ValueError, "Login was not completed") as error:
            await automatic_login(self.page, self.base + "/signin", "student", "wrong-password", 0.5)
        self.assertNotIn("wrong-password", str(error.exception))
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    async def test_duplicate_links_do_not_require_a_choice(self):
        with patch("tronclass_downloader.prompt", new=AsyncMock()) as prompt:
            results = await self.downloader.open_url(self.page, self.base + "/duplicate")
            prompt.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].read_bytes(), PAYLOAD)

    async def test_frame_replaced_while_page_loads(self):
        find_controls = self.downloader.controls
        attempts = 0

        async def unstable_controls(page):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Error("Locator.all: Frame was detached")
            return await find_controls(page)

        with patch.object(self.downloader, "controls", side_effect=unstable_controls):
            results = await self.downloader.open_url(self.page, self.base + "/course")
        self.assertEqual(results[0].read_bytes(), PAYLOAD)

    async def test_failed_download_removes_partial_file(self):
        download = AsyncMock()
        download.suggested_filename = "lesson.txt"
        download.save_as.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            await self.downloader.save(download)
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    async def test_switch_accounts_clears_session_and_preserves_files(self):
        await self.page.evaluate("localStorage.setItem('old-account', 'private')")
        old_popup = await self.context.new_page()
        existing = Path(self.folder.name) / "existing.txt"
        existing.write_bytes(PAYLOAD)
        args = SimpleNamespace(manual_login=False, timeout=5, output=Path(self.folder.name))
        answers = iter(["switch", "second", "test password", self.base + "/course", "quit"])

        async def answer(message, secret=False):
            if message.startswith("Account"):
                self.assertTrue(self.page.is_closed())
                self.assertTrue(old_popup.is_closed())
                self.assertEqual(self.browser.contexts, [])
            if secret:
                self.assertEqual(message, "Password (hidden): ")
            return next(answers)

        with patch("tronclass_downloader.prompt", side_effect=answer):
            await command_loop(self.browser, args, self.base + "/signin", self.downloader, self.page)
        self.assertEqual(existing.read_bytes(), PAYLOAD)
        self.assertEqual((Path(self.folder.name) / "lesson.txt").read_bytes(), PAYLOAD + b"Second account\n")
        new_context = self.browser.contexts[0]
        self.assertEqual([c["value"] for c in await new_context.cookies()], ["second"])
        self.assertIsNone(await new_context.pages[0].evaluate("localStorage.getItem('old-account')"))

    async def test_failed_switch_leaves_no_session_and_can_retry(self):
        args = SimpleNamespace(manual_login=False, timeout=0.5, output=Path(self.folder.name))
        answers = iter([
            "switch", "second", "wrong-password", self.base + "/file",
            "switch account", "second", "test password", self.base + "/file", "quit",
        ])

        async def answer(message, secret=False):
            value = next(answers)
            if value == "switch account":
                self.assertEqual(self.browser.contexts, [])
                self.assertEqual(list(Path(self.folder.name).iterdir()), [])
            return value

        with patch("tronclass_downloader.prompt", side_effect=answer):
            await command_loop(self.browser, args, self.base + "/signin", self.downloader, self.page)
        self.assertEqual((Path(self.folder.name) / "lesson.txt").read_bytes(), PAYLOAD + b"Second account\n")

    async def test_manual_switch_uses_fresh_login_window(self):
        args = SimpleNamespace(manual_login=True, timeout=5, output=Path(self.folder.name))
        answers = iter(["switch", "second", "", self.base + "/file", "quit"])

        async def answer(message, secret=False):
            self.assertFalse(secret)
            if message.startswith("Once"):
                self.assertTrue(self.page.is_closed())
                new_context = self.browser.contexts[0]
                self.assertEqual(await new_context.cookies(), [])
                self.assertEqual(await new_context.pages[0].locator('[name="username"]').input_value(), "second")
                await new_context.add_cookies([{"name": "session", "value": "second", "url": self.base}])
            return next(answers)

        with patch("tronclass_downloader.prompt", side_effect=answer):
            await command_loop(self.browser, args, self.base + "/signin", self.downloader, self.page)
        self.assertEqual((Path(self.folder.name) / "lesson.txt").read_bytes(), PAYLOAD + b"Second account\n")

    async def test_only_successful_login_is_remembered(self):
        store = CredentialStore(FakeVault())
        args = SimpleNamespace(manual_login=False, timeout=0.5, output=Path(self.folder.name), credential_store=store)
        downloader, _ = await sign_in(self.browser, args, self.base + "/signin", "second", "test password")
        await downloader.context.close()
        self.assertEqual(store.load(self.base), ("second", "test password"))
        with self.assertRaisesRegex(ValueError, "Login was not completed"):
            await sign_in(self.browser, args, self.base + "/signin", "student", "incorrect")
        self.assertEqual(store.load(self.base), ("second", "test password"))

    async def test_forget_removes_saved_login_but_keeps_current_session(self):
        store = CredentialStore(FakeVault())
        store.save(self.base, "student", "test password")
        args = SimpleNamespace(manual_login=False, timeout=5, output=Path(self.folder.name), credential_store=store)
        with patch("tronclass_downloader.prompt", new=AsyncMock(side_effect=["forget", self.base + "/file", "quit"])):
            await command_loop(self.browser, args, self.base + "/signin", self.downloader, self.page)
        self.assertIsNone(store.load(self.base))
        self.assertFalse(self.page.is_closed())
        self.assertEqual((Path(self.folder.name) / "lesson.txt").read_bytes(), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
