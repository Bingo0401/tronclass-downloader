"""Browser integration tests against a local, cookie-protected course site."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from tronclass_downloader import Downloader, reserve_path, validate_url


PAYLOAD = b"Course attachment\n"


class CourseSite(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/login":
            self.send_response(200)
            self.send_header("Set-Cookie", "session=test; Path=/; HttpOnly")
            body = b"Signed in"
        elif self.headers.get("Cookie") != "session=test":
            self.send_response(403)
            body = b"Sign in first"
        elif self.path == "/file":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="lesson.txt"')
            body = PAYLOAD
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            pages = {
                "/course": '<a href="/file">下載教材</a>',
                "/popup": '<a href="/file" target="_blank">Download</a>',
                "/frame": '<iframe src="/course"></iframe>',
                "/multiple": '<a href="/file">Download one</a><a href="/file">Download two</a>',
                "/delayed": '<script>setTimeout(() => {document.body.innerHTML = \'<a href="/file">Download</a>\'}, 500)</script>',
            }
            body = pages.get(self.path, "No file here").encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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

    async def test_failed_download_removes_partial_file(self):
        download = AsyncMock()
        download.suggested_filename = "lesson.txt"
        download.save_as.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            await self.downloader.save(download)
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
