"""Overleaf Client"""
##################################################
# MIT License
##################################################
# File: olclient.py
# Description: Overleaf API Wrapper
# Author: Moritz Glöckl
# License: MIT
# Version: 1.2.0
##################################################

import requests as reqs
from bs4 import BeautifulSoup
import json
import uuid
import ssl
from socketIO_client import SocketIO
import time
from urllib.parse import urlencode

DEFAULT_BASE_URL = "https://latex.cstcloud.cn"
DEFAULT_LOGIN_PATH = "/oidc/login"
DEFAULT_PROJECT_PATH = "/project"
DEFAULT_COOKIE_NAMES = ["overleaf.sid", "overleaf_session2", "GCLB"]
PATH_SEP = "/"  # Use hardcoded path separator for both windows and posix system

class OverleafClient(object):
    """
    Overleaf API Wrapper
    Supports login, querying all projects, querying a specific project, downloading a project and
    uploading a file to a project.
    """

    @staticmethod
    def filter_projects(json_content, more_attrs=None):
        more_attrs = more_attrs or {}
        for p in json_content:
            if not p.get("archived") and not p.get("trashed"):
                if all(p.get(k) == v for k, v in more_attrs.items()):
                    yield p

    def __init__(self, cookie=None, csrf=None, base_url=DEFAULT_BASE_URL,
                 login_path=DEFAULT_LOGIN_PATH, project_path=DEFAULT_PROJECT_PATH,
                 cookie_names=None):
        self._cookie = cookie  # Store the cookie for authenticated requests
        self._csrf = csrf  # Store the CSRF token since it is needed for some requests
        self._base_url = base_url.rstrip('/')
        self._login_path = login_path
        self._project_path = project_path
        self._cookie_names = list(cookie_names or DEFAULT_COOKIE_NAMES)

    @property
    def login_url(self):
        return self._base_url + self._login_path

    @property
    def project_url(self):
        return self._base_url + self._project_path

    def _project_download_url(self, project_id):
        return self._base_url + "/project/{}/download/zip".format(project_id)

    def _project_upload_url(self, project_id):
        return self._base_url + "/project/{}/upload".format(project_id)

    def _project_folder_url(self, project_id):
        return self._base_url + "/project/{}/folder".format(project_id)

    def _project_delete_url(self, project_id, doc_id):
        return self._base_url + "/project/{}/doc/{}".format(project_id, doc_id)

    def _project_compile_url(self, project_id):
        return self._base_url + "/project/{}/compile?enable_pdf_caching=true".format(project_id)

    def _project_cached_compile_meta_url(self, project_id):
        return self._base_url + "/project/{}/output/cached/output.overleaf.json".format(project_id)

    def _absolute_url(self, url_or_path):
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            return url_or_path
        return self._base_url + url_or_path

    def _download_pdf_from_compile_result(self, project_id, compile_result, headers):
        output_files = compile_result.get("outputFiles") or []
        pdf_file = next(
            (
                f
                for f in output_files
                if f.get("path") == "output.pdf" or f.get("type") == "pdf"
            ),
            None,
        )
        if pdf_file is None:
            raise reqs.HTTPError("No PDF output file found in compile result")

        download_candidates = []

        download_url = pdf_file.get("downloadURL")
        if download_url:
            download_candidates.append(self._absolute_url(download_url))

        pdf_url = pdf_file.get("url")
        if pdf_url:
            download_candidates.append(self._absolute_url(pdf_url))

        build_id = pdf_file.get("build")
        if build_id:
            params = {}
            compile_group = compile_result.get("compileGroup")
            clsi_server_id = compile_result.get("clsiServerId")
            if compile_group:
                params["compileGroup"] = compile_group
            if clsi_server_id:
                params["clsiserverid"] = clsi_server_id
            query = "?{}".format(urlencode(params)) if params else ""
            download_candidates.append(
                self._base_url
                + "/download/project/{}/build/{}/output/output.pdf{}".format(project_id, build_id, query)
            )

        seen = set()
        for candidate in download_candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            download_req = reqs.get(candidate, cookies=self._cookie, headers=headers)
            if download_req.ok:
                return pdf_file.get("path", "output.pdf"), download_req.content

        raise reqs.HTTPError("All PDF download URLs failed: {}".format(download_candidates))

    def _poll_cached_compile_result(self, project_id, headers, timeout_seconds=60, poll_interval_seconds=2):
        deadline = time.time() + timeout_seconds
        last_status = None

        while time.time() < deadline:
            cache_resp = reqs.get(
                self._project_cached_compile_meta_url(project_id),
                cookies=self._cookie,
                headers=headers,
            )
            last_status = cache_resp.status_code

            if cache_resp.ok:
                try:
                    cached_compile_result = cache_resp.json()
                except ValueError:
                    cached_compile_result = None

                if cached_compile_result and cached_compile_result.get("status") == "success":
                    return cached_compile_result

            # 404: no cached compile yet, 410: cached build is stale and being refreshed.
            if cache_resp.status_code in (404, 410):
                time.sleep(poll_interval_seconds)
                continue

            break

        raise reqs.HTTPError(
            "Cached output fallback unavailable (status {})".format(last_status)
        )

    def _refresh_csrf_token(self):
        projects_page = reqs.get(self.project_url, cookies=self._cookie)
        if not projects_page.ok:
            return None
        csrf_meta = BeautifulSoup(projects_page.content, 'html.parser').find('meta', {'name': 'ol-csrfToken'})
        if csrf_meta:
            self._csrf = csrf_meta.get('content')
        return self._csrf

    def _load_projects_from_page_meta(self):
        projects_page = reqs.get(self.project_url, cookies=self._cookie)
        projects_meta = BeautifulSoup(projects_page.content, 'html.parser').find('meta', {'name': 'ol-projects'})
        if projects_meta is None:
            raise reqs.HTTPError("Could not parse projects list from project page")
        return json.loads(projects_meta.get('content'))

    def _load_projects_from_api(self):
        if not self._csrf:
            self._refresh_csrf_token()

        if not self._csrf:
            return None

        headers = {"X-Csrf-Token": self._csrf}
        body = {"sort": {"by": "lastUpdated", "order": "desc"}, "page": {"size": 2000}}
        projects_request = reqs.post(
            self._base_url + "/api/project",
            cookies=self._cookie,
            headers=headers,
            json=body,
        )

        if not projects_request.ok:
            return None

        payload = projects_request.json()
        if isinstance(payload, dict) and isinstance(payload.get("projects"), list):
            return payload["projects"]
        return None

    def _load_projects(self):
        projects = self._load_projects_from_api()
        if projects is not None:
            return projects
        return self._load_projects_from_page_meta()

    def login(self, username, password):
        """
        WARNING - DEPRECATED - Not working as Overleaf introduced captchas
        Login to the Overleaf Service with a username and a password
        Params: username, password
        Returns: Dict of cookie and CSRF
        """

        get_login = reqs.get(self.login_url)
        self._csrf = BeautifulSoup(get_login.content, 'html.parser').find(
            'input', {'name': '_csrf'}).get('value')
        login_json = {
            "_csrf": self._csrf,
            "email": username,
            "password": password
        }
        post_login = reqs.post(self.login_url, json=login_json,
                               cookies=get_login.cookies)

        # On a successful authentication the Overleaf API returns a new authenticated cookie.
        # If the cookie is different than the cookie of the GET request the authentication was successful
        if post_login.status_code == 200 and get_login.cookies["overleaf_session2"] != post_login.cookies[
            "overleaf_session2"]:
            self._cookie = post_login.cookies

            # Enrich cookie with GCLB cookie from GET request above
            self._cookie['GCLB'] = get_login.cookies['GCLB']

            # CSRF changes after making the login request, new CSRF token will be on the projects page
            projects_page = reqs.get(self.project_url, cookies=self._cookie)
            self._csrf = BeautifulSoup(projects_page.content, 'html.parser').find('meta', {'name': 'ol-csrfToken'}) \
                .get('content')

            return {"cookie": self._cookie, "csrf": self._csrf}

    def all_projects(self):
        """
        Get all of a user's active projects (= not archived and not trashed)
        Returns: List of project objects
        """
        json_content = self._load_projects()
        return list(OverleafClient.filter_projects(json_content))

    def get_project(self, project_name):
        """
        Get a specific project by project_name
        Params: project_name, the name of the project
        Returns: project object
        """

        json_content = self._load_projects()
        return next(OverleafClient.filter_projects(json_content, {"name": project_name}), None)

    def download_project(self, project_id):
        """
        Download project in zip format
        Params: project_id, the id of the project
        Returns: bytes string (zip file)
        """
        r = reqs.get(self._project_download_url(project_id),
                     stream=True, cookies=self._cookie)
        return r.content

    def create_folder(self, project_id, parent_folder_id, folder_name):
        """
        Create a new folder in a project

        Params:
        project_id: the id of the project
        parent_folder_id: the id of the parent folder, root is the project_id
        folder_name: how the folder will be named

        Returns: folder id or None
        """

        params = {
            "parent_folder_id": parent_folder_id,
            "name": folder_name
        }
        headers = {
            "X-Csrf-Token": self._csrf
        }
        r = reqs.post(self._project_folder_url(project_id),
                      cookies=self._cookie, headers=headers, json=params)

        if r.ok:
            return json.loads(r.content)
        elif r.status_code == str(400):
            # Folder already exists
            return
        else:
            raise reqs.HTTPError()

    def get_project_infos(self, project_id):
        """
        Get detailed project infos about the project

        Params:
        project_id: the id of the project

        Returns: project details
        """
        project_infos = None

        # Callback function for the joinProject emitter
        def set_project_infos(*args):
            # Set project_infos variable in outer scope.
            # Different socket.io server/client combinations pass slightly different callback args.
            nonlocal project_infos
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                if isinstance(arg.get('project'), dict):
                    project_infos = arg['project']
                    return
                if arg.get('rootFolder') is not None:
                    project_infos = arg
                    return

        # Convert cookie map to Cookie header format for socket authentication.
        cookie_items = ["{}={}".format(name, value) for name, value in self._cookie.items() if name in self._cookie_names]
        cookie = "; ".join(cookie_items)

        # socketIO-client expects websocket.SSLError on some code paths.
        # Newer websocket-client versions may not expose that attribute.
        try:
            import websocket
            if not hasattr(websocket, "SSLError"):
                websocket.SSLError = ssl.SSLError
        except Exception:
            pass

        # Some reverse proxies/self-hosted deployments close websocket upgrades.
        # Retry with xhr-polling transport as a compatibility fallback.
        last_error = None
        transport_attempts = [
            ('websocket', 'xhr-polling'),
            ('xhr-polling',),
        ]

        for transports in transport_attempts:
            socket_io = None
            try:
                socket_io = SocketIO(
                    self._base_url,
                    params={'t': int(time.time()), 'projectId': project_id},
                    headers={'Cookie': cookie},
                    transports=transports,
                )

                # Wait until we connect to the socket
                socket_io.on('connect', lambda: None)
                socket_io.wait_for_callbacks(seconds=8)

                # Modern Overleaf triggers joinProjectResponse when projectId is in params
                socket_io.on('joinProjectResponse', set_project_infos)

                # Send the joinProject event and receive the project infos (for older servers)
                socket_io.emit('joinProject', {'project_id': project_id}, set_project_infos)
                socket_io.wait_for_callbacks(seconds=20)

                if project_infos:
                    return project_infos
                raise TimeoutError('joinProject callback timed out for transports={}'.format(transports))
            except Exception as exc:
                last_error = exc
            finally:
                if socket_io and socket_io.connected:
                    socket_io.disconnect()

        if last_error:
            raise last_error

        return project_infos

    def upload_file(self, project_id, project_infos, file_name, file_size, file):
        """
        Upload a file to the project

        Params:
        project_id: the id of the project
        file_name: how the file will be named
        file_size: the size of the file in bytes
        file: the file itself

        Returns: True on success, False on fail
        """

        # Set the folder_id to the id of the root folder
        folder_id = project_infos['rootFolder'][0]['_id']

        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            local_folders = file_name.split(PATH_SEP)[:-1]  # Remove last item since this is the file name
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']  # Set the current remote folder

            for local_folder in local_folders:
                exists_on_remote = False
                for remote_folder in current_overleaf_folder:
                    # Check if the folder exists on remote, continue with the new folder structure
                    if local_folder.lower() == remote_folder['name'].lower():
                        exists_on_remote = True
                        folder_id = remote_folder['_id']
                        current_overleaf_folder = remote_folder['folders']
                        break
                # Create the folder if it doesn't exist
                if not exists_on_remote:
                    new_folder = self.create_folder(project_id, folder_id, local_folder)
                    current_overleaf_folder.append(new_folder)
                    folder_id = new_folder['_id']
                    current_overleaf_folder = new_folder['folders']
        params = {
            "folder_id": folder_id,
            "_csrf": self._csrf,
            "qquuid": str(uuid.uuid4()),
            "qqtotalfilesize": file_size,
        }
        data = {
            "name": file_name
        }
        files = {
            "qqfile": file
        }

        # Upload the file to the predefined folder
        r = reqs.post(self._project_upload_url(project_id), cookies=self._cookie, params=params, data=data, files=files)

        return r.status_code == str(200) and json.loads(r.content)["success"]

    def delete_file(self, project_id, project_infos, file_name):
        """
        Deletes a project's file

        Params:
        project_id: the id of the project
        file_name: how the file will be named

        Returns: True on success, False on fail
        """

        file = None

        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            local_folders = file_name.split(PATH_SEP)[:-1]  # Remove last item since this is the file name
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']  # Set the current remote folder

            for local_folder in local_folders:
                for remote_folder in current_overleaf_folder:
                    if local_folder.lower() == remote_folder['name'].lower():
                        file = next((v for v in remote_folder['docs'] if v['name'] == file_name.split(PATH_SEP)[-1]),
                                    None)
                        current_overleaf_folder = remote_folder['folders']
                        break
        # File is in root folder
        else:
            file = next((v for v in project_infos['rootFolder'][0]['docs'] if v['name'] == file_name), None)

        # File not found!
        if file is None:
            return False

        headers = {
            "X-Csrf-Token": self._csrf
        }

        r = reqs.delete(
            self._project_delete_url(project_id, file['_id']),
            cookies=self._cookie,
            headers=headers,
            json={}
        )

        return r.status_code == str(204)

    def download_pdf(self, project_id):
        """
        Compiles and returns a project's PDF

        Params:
        project_id: the id of the project

        Returns: PDF file name and content on success
        """
        headers = {
            "X-Csrf-Token": self._csrf
        }

        body = {
            "check": "silent",
            "draft": False,
            "incrementalCompilesEnabled": True,
            "rootDoc_id": "",
            "stopOnFirstError": False
        }

        r = None
        compile_result = None
        compile_error = None
        try:
            r = reqs.post(self._project_compile_url(project_id), cookies=self._cookie, headers=headers, json=body, timeout=3)
        except reqs.exceptions.ReadTimeout:
            # Overleaf sends "102 Processing" which causes requests to hang or timeout.
            pass

        # If the proxy chain does not forward the final JSON response, requests only sees 102.
        if r is None or (r.status_code == 102 and not r.content):
            import subprocess, re
            cookie_str = "; ".join(["{}={}".format(k, v) for k, v in self._cookie.items()])
            cmd = [
                "curl", "-s", "-X", "POST", self._project_compile_url(project_id),
                "--http1.1",
                "-H", "Cookie: {}".format(cookie_str),
                "-H", "X-Csrf-Token: {}".format(self._csrf),
                "-H", "Content-Type: application/json",
                "-d", json.dumps(body)
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and result.stdout:
                    match = re.search(r'(\{.*\})', result.stdout, re.DOTALL)
                    if match:
                        compile_result = json.loads(match.group(1))
                        compile_error = None
            except Exception:
                pass
        elif r is not None and not r.ok:
            compile_error = "Compile request failed with status {}: {}".format(
                r.status_code,
                r.text[:240].replace("\n", " ")
            )
        else:
            try:
                compile_result = json.loads(r.content)
            except ValueError:
                compile_error = (
                    "Compile endpoint returned non-JSON response with status {}: {}".format(
                        r.status_code,
                        r.text[:240].replace("\n", " ")
                    )
                )

        if compile_result and compile_result.get("status") == "success":
            return self._download_pdf_from_compile_result(project_id, compile_result, headers)

        # Fallback path: read latest successful compile metadata from cache and download output.pdf.
        cache_error = None
        try:
            cached_compile_result = self._poll_cached_compile_result(project_id, headers)
            return self._download_pdf_from_compile_result(
                project_id, cached_compile_result, headers
            )
        except reqs.HTTPError as err:
            cache_error = str(err)

        error_hint = (
            "Please set COMPILE_HEARTBEAT_MS=0 on the Overleaf web service or "
            "adjust reverse proxy 1xx forwarding settings."
        )
        raise reqs.HTTPError(
            "{}; {}. {}".format(
                compile_error or "Compile did not succeed",
                cache_error or "cached output fallback unavailable",
                error_hint,
            )
        )
