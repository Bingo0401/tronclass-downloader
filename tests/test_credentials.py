"""Credential storage tests use a fake vault and never access the OS keychain."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, Mock, patch

from credential_store import CredentialStore
from tronclass_downloader import read_credentials


class FakeVault:
    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        del self.items[(service, username)]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.vault = FakeVault()
        self.store = CredentialStore(self.vault)
        self.site = "https://school.example"

    def test_remembered_login_survives_new_store_instance(self):
        self.assertTrue(self.store.save(self.site, "student", " dummy password "))
        restored = CredentialStore(self.vault)
        self.assertEqual(restored.load(self.site), ("student", " dummy password "))
        self.assertIsNone(restored.load("https://other.example"))
        self.assertIsNone(restored.load("http://school.example"))

    def test_replace_and_forget_only_the_current_site(self):
        self.store.save(self.site, "first", "test-one")
        self.store.save("https://other.example", "other", "test-other")
        self.store.save(self.site, "second", "test-two")
        self.assertEqual(self.store.load(self.site), ("second", "test-two"))
        self.assertTrue(self.store.forget(self.site))
        self.assertIsNone(self.store.load(self.site))
        self.assertEqual(self.store.load("https://other.example"), ("other", "test-other"))
        self.assertTrue(self.store.forget(self.site))

    def test_corrupt_entry_falls_back_without_printing_payload(self):
        self.vault.set_password(self.store.service(self.site), "login", "invalid-secret")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertIsNone(self.store.load(self.site))
        self.assertNotIn("invalid-secret", output.getvalue())

    def test_unavailable_vault_does_not_write_a_fallback_file_or_expose_errors(self):
        backend = Mock()
        for method in [backend.get_password, backend.set_password, backend.delete_password]:
            method.side_effect = RuntimeError("sensitive-backend-error")
        store = CredentialStore(backend)
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.open", side_effect=AssertionError("No file writes")):
            self.assertIsNone(store.load(self.site))
            self.assertFalse(store.save(self.site, "student", "test password"))
            self.assertFalse(store.forget(self.site))
        self.assertNotIn("sensitive-backend-error", output.getvalue())


class CredentialPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_account_skips_password_prompt(self):
        store = CredentialStore(FakeVault())
        store.save("https://school.example", "student", "test password")
        with patch("tronclass_downloader.prompt", new=AsyncMock()) as prompt:
            result = await read_credentials(False, store, "https://school.example/course/1")
        prompt.assert_not_called()
        self.assertEqual(result, ("student", "test password"))

    async def test_switch_and_no_remember_prompt_for_new_credentials(self):
        store = Mock()
        for active_store, use_saved in [(store, False), (None, True)]:
            with patch("tronclass_downloader.prompt", new=AsyncMock(side_effect=["second", "test-two"])) as prompt:
                result = await read_credentials(False, active_store, "https://school.example", use_saved)
            self.assertEqual(result, ("second", "test-two"))
            self.assertEqual(prompt.await_args_list[1].kwargs, {"secret": True})
        store.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
