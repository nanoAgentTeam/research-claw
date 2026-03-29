"""Ol Browser Login Utility"""
##################################################
# MIT License
##################################################
# File: olbrowserlogin.py
# Description: Overleaf Browser Login Utility
# Author: Moritz Glöckl
# License: MIT
# Version: 1.2.0
##################################################

from PySide6.QtCore import *
from PySide6.QtWidgets import *
from PySide6.QtWebEngineWidgets import *
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings, QWebEnginePage

DEFAULT_BASE_URL = "https://sharelatex-lin.cstcloud.cn"
DEFAULT_LOGIN_PATH = "/oidc/login"
DEFAULT_PROJECT_PATH = "/project"
# JS snippet to extract the csrfToken
JAVASCRIPT_CSRF_EXTRACTOR = "document.getElementsByName('ol-csrfToken')[0].content"
DEFAULT_COOKIE_NAMES = ["overleaf.sid", "overleaf_session2", "GCLB"]


class OlBrowserLoginWindow(QMainWindow):
    """
    Overleaf Browser Login Utility
    Opens a browser window to securely login the user and returns relevant login data.
    """

    def __init__(self, base_url=DEFAULT_BASE_URL, login_path=DEFAULT_LOGIN_PATH,
                 project_path=DEFAULT_PROJECT_PATH, cookie_names=None, *args, **kwargs):
        super(OlBrowserLoginWindow, self).__init__(*args, **kwargs)

        self.webview = QWebEngineView()
        self.base_url = base_url.rstrip('/')
        self.login_url = self.base_url + login_path
        self.project_url = self.base_url + project_path
        self.cookie_names = set(cookie_names or DEFAULT_COOKIE_NAMES)

        self._cookies = {}
        self._csrf = ""
        self._login_success = False

        self.profile = QWebEngineProfile(self.webview)
        self.cookie_store = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self.handle_cookie_added)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)

        self.profile.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

        webpage = QWebEnginePage(self.profile, self)
        self.webview.setPage(webpage)
        self.webview.load(QUrl.fromUserInput(self.login_url))
        self.webview.loadFinished.connect(self.handle_load_finished)

        self.setCentralWidget(self.webview)
        self.resize(600, 700)

    def handle_load_finished(self):
        def callback(result):
            if result:
                self._csrf = result
                self._login_success = True
                QCoreApplication.quit()

        if self.webview.url().toString().startswith(self.project_url):
            self.webview.page().runJavaScript(
                JAVASCRIPT_CSRF_EXTRACTOR, 0, callback
            )

    def handle_cookie_added(self, cookie):
        cookie_name = cookie.name().data().decode('utf-8')
        if cookie_name in self.cookie_names:
            self._cookies[cookie_name] = cookie.value().data().decode('utf-8')

    @property
    def cookies(self):
        return self._cookies

    @property
    def csrf(self):
        return self._csrf

    @property
    def login_success(self):
        return self._login_success


def login(base_url=DEFAULT_BASE_URL, login_path=DEFAULT_LOGIN_PATH,
          project_path=DEFAULT_PROJECT_PATH, cookie_names=None):
    from PySide6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules('''\
    qt.webenginecontext.info=false
    ''')

    app = QApplication([])
    ol_browser_login_window = OlBrowserLoginWindow(
        base_url=base_url,
        login_path=login_path,
        project_path=project_path,
        cookie_names=cookie_names,
    )
    ol_browser_login_window.show()
    app.exec()

    if not ol_browser_login_window.login_success:
        return None

    return {"cookie": ol_browser_login_window.cookies, "csrf": ol_browser_login_window.csrf}
