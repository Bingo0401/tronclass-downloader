"""Remember one login per site in the native OS credential store, never in files."""

import json
import sys


class CredentialStore:
    def __init__(self, backend=None):
        self.backend = backend
        self.initialized = backend is not None

    def _backend(self):
        if not self.initialized:
            self.initialized = True
            try:
                # Select native backends explicitly; never use a configurable
                # third-party backend that might store passwords in plain text.
                if sys.platform == "darwin":
                    from keyring.backends.macOS import Keyring
                    self.backend = Keyring()
                elif sys.platform == "win32":
                    from keyring.backends.Windows import WinVaultKeyring
                    self.backend = WinVaultKeyring()
            except Exception:
                self.backend = None
            if self.backend is None:
                print("Secure saved logins are unavailable. Credentials will only be used for this session.")
        return self.backend

    @staticmethod
    def service(site_origin):
        return f"tronclass-downloader:{site_origin}"

    def load(self, site_origin):
        backend = self._backend()
        if backend is None:
            return None
        try:
            value = backend.get_password(self.service(site_origin), "login")
            if value is None:
                return None
            saved = json.loads(value)
            if not isinstance(saved, dict):
                raise ValueError("Invalid saved login")
            account, password = saved.get("account"), saved.get("password")
            if not isinstance(account, str) or not account or not isinstance(password, str) or not password:
                raise ValueError("Invalid saved login")
            return account, password
        except Exception:
            # Backend errors must not expose the stored credential payload.
            print("Could not read the saved login. Enter your credentials to continue.")
            return None

    def save(self, site_origin, account, password):
        backend = self._backend()
        if backend is None:
            return False
        try:
            backend.set_password(
                self.service(site_origin), "login",
                json.dumps({"account": account, "password": password}),
            )
        except Exception:
            print("Login succeeded, but the OS credential store could not save it.")
            return False
        print("Login remembered in the OS credential store. Type 'forget' to remove it.")
        return True

    def forget(self, site_origin):
        backend = self._backend()
        if backend is None:
            print("Could not access saved logins. Remove the entry in your OS credential manager.")
            return False
        try:
            service = self.service(site_origin)
            if backend.get_password(service, "login") is not None:
                backend.delete_password(service, "login")
        except Exception:
            print("Could not remove the saved login. Check your OS credential manager.")
            return False
        print("Saved login removed for this site. The current session remains signed in.")
        return True
