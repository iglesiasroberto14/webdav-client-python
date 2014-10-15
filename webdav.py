# -*- coding: utf-8

import pycurl
import re
import os
#import xattr
import threading
import xml.etree.ElementTree as ET

from io import BytesIO
from urllib.parse import unquote, quote

class Urn:
    '''
    Protection from attack
    server_root_1 = /user1
    server_root_2 = /user2
    client_1 = webdav.Client(server_root=server_root_1)
    client_1.clear("../user2")
    '''
    separate = "/"

    def __init__(self, str, directory=False):
        self._path = quote(str)
        expression = "{begin}{end}".format(begin=Urn.separate, end="+")
        self._path = re.sub(expression, Urn.separate, self._path)
        self._path = re.sub(expression, Urn.separate, self._path)
        if self._path[0] != Urn.separate:
            self._path = "{begin}{end}".format(begin=Urn.separate, end=self._path)

        if directory and not self._path.endswith(Urn.separate):
            self._path = "{begin}{end}".format(begin=self._path, end=Urn.separate)

    def path(self):
        return unquote(self._path)

    def unquote(self):
        return self._path

    def filename(self):
        path_split = self._path.split(Urn.separate)
        name = path_split[-2] + Urn.separate if path_split[-1] == '' else path_split[-1]
        return unquote(name)

    def parent(self):
        path_split = self._path.split(Urn.separate)
        nesting_level = self.nesting_level()
        parent_path_split = path_split[:nesting_level]
        parent = self.separate.join(parent_path_split) if nesting_level != 1 else Urn.separate
        return unquote(parent + Urn.separate)

    def nesting_level(self):
        return self._path.count(Urn.separate, 0, -1)

    def is_directory(self):
        return self._path[:-1] == Urn.separate

class WebDavException(Exception):
    pass

class NotFound(WebDavException):
    pass

class LocalResourceNotFound(NotFound):
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return "Local file: {path} not found".format(path=self.path)

class RemoteResourceNotFound(NotFound):
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return "Remote resource: {path} not found".format(path=self.path)

class RemoteParentNotFound(NotFound):
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return "Remote parent for: {path} not found".format(path=self.path)

class InvalidOption(WebDavException):
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __str__(self):
        return "Option ({name}:{value}) have invalid name or value".format(name=self.name, value=self.value)

class NotConnection(WebDavException):
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text

class NotEnoughSpace(WebDavException):
    def __init__(self):
        pass

    def __str__(self):
        return "Not enough space on the server"

class Client:

    root = '/'

    http_header = {
        'list':         ["Accept: */*", "Depth: 1"],
        'free':         ["Accept: */*", "Depth: 0", "Content-Type: text/xml"],
        'copy':         ["Accept: */*"],
        'move':         ["Accept: */*"],
        'mkdir':        ["Accept: */*", "Connection: Keep-Alive"],
        'clear':        ["Accept: */*", "Connection: Keep-Alive"],
        'info':         ["Accept: */*", "Depth: 1"],
        'get_metadata': ["Accept: */*", "Depth: 1", "Content-Type: application/x-www-form-urlencoded"],
        'set_metadata': ["Accept: */*", "Depth: 1", "Content-Type: application/x-www-form-urlencoded"]
    }

    requests = {
        'copy':         "COPY",
        'move':         "MOVE",
        'mkdir':        "MKCOL",
        'clear':        "DELETE",
        'list':         "PROPFIND",
        'free':         "PROPFIND",
        'info':         "PROPFIND",
        'get_metadata': "PROPFIND",
        'publish':      "PROPPATCH",
        'unpublish':    "PROPPATCH",
        'published':    "PROPPATCH",
        'set_metadata': "PROPPATCH"
    }

    def __init__(self, options):
        self.options = options
        self.server_hostname = options.get("server_hostname", '')
        self.server_login = options.get("server_login", '')
        self.server_password = options.get("server_password", '')
        self.proxy_hostname = options.get("proxy_hostname", '')
        self.proxy_login = options.get("proxy_login", '')
        self.proxy_password = options.get("proxy_password", '')

        server_root = options.get("server_root", '')
        self.server_root = Urn(server_root).unquote() if server_root else ''
        self.server_root = self.server_root.rstrip(Urn.separate)

        pycurl.global_init(pycurl.GLOBAL_DEFAULT)

        self.default_options = {}

    def __del__(self):
        pycurl.global_cleanup()

    def __str__(self):
        return "client with options {options}".format(options=self.options)

    def Request(self, options=None):

        curl = pycurl.Curl()

        self.default_options.update({
            'SSL_VERIFYPEER':   0,
            'SSL_VERIFYHOST':   0,
            'URL':              self.server_hostname,
            'USERPWD':          '{login}:{password}'.format(login=self.server_login, password=self.server_password),
        })

        if self.proxy_login:
            if not self.proxy_password:
                self.default_options['PROXYUSERNAME'] = self.proxy_login
            else:
                self.default_options['PROXYUSERPWD'] = '{login}:{password}'.format(login=self.proxy_login, password=self.proxy_password)

        if self.default_options:
            Client._add_options(curl, self.default_options)

        if options:
            Client._add_options(curl, options)

        return curl

    def check_connection(self) -> int:

        request = self.Request()
        request.perform()
        code = request.getinfo(pycurl.HTTP_CODE)
        request.close()
        return code.startswith("2")

    def list(self, remote_path=root) -> list:

        def parse(response) -> list:
            response_str = response.getvalue().decode('utf-8')
            tree = ET.fromstring(response_str)
            hrees = [unquote(hree.text) for hree in tree.findall(".//{DAV:}href")]
            return [Urn(hree) for hree in hrees]

        try:

            directory_urn = Urn(remote_path, directory=True)

            if directory_urn.path() != Client.root:
                if not self.exists(directory_urn.path()):
                    raise RemoteResourceNotFound(directory_urn.path())

            response = BytesIO()

            options = {
                'CUSTOMREQUEST': Client.requests['list'],
                'URL'          : '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=directory_urn.unquote()),
                'HTTPHEADER'   : Client.http_header['list'],
                'WRITEDATA'    : response
            }

            request = self.Request(options=options)

            request.perform()
            request.close()

            urns = parse(response)
            return [urn.filename() for urn in urns if urn.path() != directory_urn.path()]

        except pycurl.error as e:
            raise NotConnection(e.args[1])

    def free(self) -> int:

        def parse(response) -> int:

            response_str = response.getvalue().decode('utf-8')
            root = ET.fromstring(response_str)
            size = root.find('.//{DAV:}quota-available-bytes')
            return int(size.text)

        def data() -> str:
            root = ET.Element("D:propfind")
            root.set('xmlns:D', "DAV:")
            prop = ET.SubElement(root, "D:prop")
            ET.SubElement(prop, "D:quota-available-bytes")
            ET.SubElement(prop, "D:quota-used-bytes")
            tree = ET.ElementTree(root)

            buffer = BytesIO()

            tree.write(buffer)
            return buffer.getvalue().decode('utf-8')

        try:
            response = BytesIO()

            options = {
                'CUSTOMREQUEST':    Client.requests['free'],
                'HTTPHEADER':       Client.http_header['free'],
                'POSTFIELDS':       data(),
                'WRITEDATA':        response
            }

            request = self.Request(options)

            request.perform()
            request.close()

            return parse(response)

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def exists(self, remote_path) -> int:

        urn = Urn(remote_path)
        if urn._path == Client.root: return True
        filename = urn.filename()
        parent = urn.parent()
        return filename in self.list(parent)

    def mkdir(self, remote_path) -> None:

        try:
            directory_urn = Urn(remote_path, directory=True)

            if not self.exists(directory_urn.parent()):
                raise RemoteParentNotFound(directory_urn.path())

            options = {
                'CUSTOMREQUEST':    Client.requests['mkdir'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=directory_urn.unquote()),
                'HTTPHEADER':       Client.http_header['mkdir']
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def download_to(self, buffer, remote_path) -> None:

        try:
            urn = Urn(remote_path)

            if urn.is_directory():
                raise InvalidOption(name="remote_path", value=remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            options = {
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'WRITEDATA':        buffer,
                'WRITEFUNCTION':    buffer.write
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def download(self, local_path, remote_path) -> None:

        urn = Urn(remote_path)

        if urn.is_directory():
            self.download_directory(local_path=local_path, remote_path=remote_path)
        else:
            self.download_file(local_path=local_path, remote_path=remote_path)

    def download_directory(self, local_path, remote_path) -> None:

        urn = Urn(remote_path)

        if not urn.is_directory():
            raise InvalidOption(name="remote_path", value=remote_path)

        if not os.path.isdir(local_path):
            raise InvalidOption(name="local_path", value=local_path)

        if not self.exists(urn.path()):
            raise RemoteResourceNotFound(urn.path())

        if os.path.exists(local_path):
            os.remove(local_path)

        os.makedirs(local_path)

        for resource_name in self.list(remote_path):
            _remote_path = "{parent}{name}".format(parent=urn.path(), name=resource_name)
            _local_path = os.path.join(local_path, resource_name)
            self.download(local_path=_local_path, remote_path=_remote_path)

    def download_file(self, local_path, remote_path) -> None:

        try:
            urn = Urn(remote_path)

            if urn.is_directory():
                raise InvalidOption(name="remote_path", value=remote_path)

            if os.path.isdir(local_path):
                raise InvalidOption(name="local_path", value=local_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            with open(local_path, 'wb') as file:

                options = {
                    'URL':          '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                    'WRITEDATA':    file
                }

                request = self.Request(options)

                request.perform()
                request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def download_sync(self, local_path, remote_path, callback=None) -> None:

        self.download(local_path=local_path, remote_path=remote_path)

        if callback:
            callback()

    def download_async(self, local_path, remote_path, callback=None) -> None:
        target = (lambda: self.download_sync(local_path=local_path, remote_path=remote_path, callback=callback))
        threading.Thread(target=target).start()

    def upload_from(self, buffer, remote_path) -> None:

        try:
            urn = Urn(remote_path)

            if urn.is_directory():
                raise InvalidOption(name="remote_path", value=remote_path)

            if not self.exists(urn.parent()):
                raise RemoteParentNotFound(urn.path())

            options = {
                'UPLOAD':           1,
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'READDATA':         buffer,
                'READFUNCTION':     buffer.read,
            }

            request = self.Request(options)

            request.perform()
            code = request.getinfo(pycurl.HTTP_CODE)
            if code == "507":
                raise NotEnoughSpace() #TODO

            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def upload(self, local_path, remote_path) -> None:

        if os.path.isdir(local_path):
            self.upload_directory(local_path=local_path, remote_path=remote_path)
        else:
            self.upload_file(local_path=local_path, remote_path=remote_path)

    def upload_directory(self, local_path, remote_path) -> None:

        urn = Urn(remote_path)

        if not urn.is_directory():
            raise InvalidOption(name="remote_path", value=remote_path)

        if not os.path.isdir(local_path):
            raise InvalidOption(name="local_path", value=local_path)

        if not os.path.exists(local_path):
            raise LocalResourceNotFound(local_path)

        if self.exists(remote_path):
            self.clear(remote_path)

        self.mkdir(remote_path)

        for resource_name in os.listdir(local_path):
            _remote_path = "{parent}{name}".format(parent=urn.path(), name=resource_name)
            _local_path = os.path.join(local_path, resource_name)
            self.upload(local_path=_local_path, remote_path=_remote_path)

    def upload_file(self, local_path, remote_path) -> None:

        try:
            if not os.path.exists(local_path):
                raise LocalResourceNotFound(local_path)

            urn = Urn(remote_path)

            if urn.is_directory():
                raise InvalidOption(name="remote_path", value=remote_path)

            if os.path.isdir(local_path):
                raise InvalidOption(name="local_path", value=local_path)

            if not os.path.exists(local_path):
                raise LocalResourceNotFound(local_path)

            if not self.exists(urn.parent()):
                raise RemoteParentNotFound(urn.path())

            with open(local_path, 'rb') as file:

                options = {
                    'UPLOAD':           1,
                    'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                    'READDATA':         file,
                    'READFUNCTION':     file.read,
                    'INFILESIZE_LARGE': os.path.getsize(local_path)
                }

                request = self.Request(options)

                request.perform()
                code = request.getinfo(pycurl.HTTP_CODE)
                if code == "507":
                    raise NotEnoughSpace() #TODO

                request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def upload_sync(self, local_path, remote_path, callback=None) -> None:

        self.upload(local_path=local_path, remote_path=remote_path)

        if callback:
            callback()

    def upload_async(self, local_path, remote_path, callback=None) -> None:
        target = (lambda: self.upload_sync(local_path=local_path, remote_path=remote_path, callback=callback))
        threading.Thread(target=target).start()

    def copy(self, remote_path_from, remote_path_to) -> None:

        def header(remote_path_to) -> list:
            destination = Urn(remote_path_to).path()
            header_item = "Destination: {destination}".format(destination=destination)
            header = Client.http_header['copy'].copy()
            header.append(header_item)
            return header

        try:
            urn_from = Urn(remote_path_from)

            if not self.exists(urn_from.path()):
                raise RemoteResourceNotFound(urn_from.path())

            urn_to = Urn(remote_path_to)

            if not self.exists(urn_to.parent()):
                raise RemoteParentNotFound(urn_to.path())

            options = {
                'CUSTOMREQUEST':    Client.requests['copy'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn_to.unquote()),
                'HTTPHEADER':       header(remote_path_to)
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def move(self, remote_path_from, remote_path_to) -> None:

        def header(remote_path_to) -> list:
            destination = Urn(remote_path_to).path()
            header_item = "Destination: {destination}".format(destination=destination)
            header = Client.http_header['copy'].copy()
            header.append(header_item)
            return header

        try:
            urn_from = Urn(remote_path_from)

            if not self.exists(urn_from.path()):
                raise RemoteResourceNotFound(urn_from.path())

            urn_to = Urn(remote_path_to)

            if not self.exists(urn_to.parent()):
                raise RemoteParentNotFound(urn_to.path())

            options = {
                'CUSTOMREQUEST':    Client.requests['move'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn_to.unquote()),
                'HTTPHEADER':       header(remote_path_to)
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def clear(self, remote_path) -> None:

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            options = {
                'CUSTOMREQUEST':    Client.requests['clear'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'HTTPHEADER':       Client.http_header['clear']
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def publish(self, remote_path) -> str:

        def parse(response) -> str:
            response_str = response.getvalue().decode('utf-8')
            root = ET.fromstring(response_str)
            public_url = root.find('.//public_url')
            return public_url.text if public_url else ""

        def data() -> str:
            root = ET.Element("propertyupdate", xmlns="DAV:")
            set = ET.SubElement(root, "set")
            prop = ET.SubElement(set, "prop")
            public_url = ET.SubElement(prop, "public_url", xmlns="urn:yandex:disk:meta")
            public_url.text = "true"
            tree = ET.ElementTree(root)

            buffer = BytesIO()
            tree.write(buffer)

            return buffer.getvalue().decode('utf-8')

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            response = BytesIO()

            options = {
                'CUSTOMREQUEST':    Client.requests['publish'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'POSTFIELDS':       data(),
                'WRITEDATA':        response
            }

            request = self.Request(options)

            request.perform()
            request.close()

            return parse(response)

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def unpublish(self, remote_path) -> None:

        def data() -> str:
            root = ET.Element("propertyupdate", xmlns="DAV:")
            remove = ET.SubElement(root, "remove")
            prop = ET.SubElement(remove, "prop")
            ET.SubElement(prop, "public_url", xmlns="urn:yandex:disk:meta")
            tree = ET.ElementTree(root)

            buffer = BytesIO()
            tree.write(buffer)

            return buffer.getvalue().decode('utf-8')

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                 raise RemoteResourceNotFound(urn.path())

            response = BytesIO()

            options = {
                'CUSTOMREQUEST':    Client.requests['unpublish'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'POSTFIELDS':       data(),
                'WRITEDATA':        response
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def published(self, remote_path) -> str:

        def parse(response) -> str:
            response_str = response.getvalue().decode('utf-8')
            root = ET.fromstring(response_str)
            public_url = root.find('.//public_url')
            return public_url.text if public_url else ""

        def data() -> str:
            root = ET.Element("D:propfind")
            root.set('xmlns:D', "DAV:")
            prop = ET.SubElement(root, "prop")
            ET.SubElement(prop, "public_url", xmlns="urn:yandex:disk:meta")
            tree = ET.ElementTree(root)

            buffer = BytesIO()
            tree.write(buffer)

            return buffer.getvalue().decode('utf-8')

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            response = BytesIO()

            options = {
                'CUSTOMREQUEST':    Client.requests['published'],
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.unquote()),
                'POSTFIELDS':       data(),
                'WRITEDATA':        response
            }

            request = self.Request(options)

            request.perform()
            request.close()

            return parse(response)

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def info(self, remote_path) -> dict:

        def parse(response) -> dict:
            response_str = response.getvalue().decode('utf-8')
            root = ET.fromstring(response_str)

            info = {}

            responses = root.findall("{DAV:}response")
            for response in responses:

                href = response.findtext("{DAV:}href")

                urn = Urn(href)


                find_attributes = {
                    'created':  ".//{DAV:}creationdate",
                    'name':     ".//{DAV:}displayname",
                    'size':     ".//{DAV:}getcontentlength",
                    'modified': ".//{DAV:}getlastmodified",
                    'type':     ".//{DAV:}resourcetype"
                }

                record = {}
                for (name, value) in find_attributes:
                    node = response.find(value)
                    record[name] = node.text if node else ''

                info[urn.filename()] = record

            return info

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            response = BytesIO()

            parent_urn = Urn(urn.parent())
            options = {
                'CUSTOMREQUEST': Client.requests['info'],
                'URL'          : '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=parent_urn),
                'HTTPHEADER'   : Client.http_header['info'],
                'WRITEDATA'    : response
            }

            request = self.Request(options)

            request.perform()
            request.close()

            info = parse(response)
            name = urn.filename()

            return info[name] if name in info else dict()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def resource(self, remote_path):
        urn = Urn(remote_path)
        return Resource(self, urn)

    def _add_options(request, options: dict) -> None:

        for (key, value) in options.items():
            try:
                request.setopt(pycurl.__dict__[key], value)
            except TypeError or pycurl.error:
                raise InvalidOption(key, value)

    def get_property(self, remote_path, option: dict) -> str:

        def parse(response, option) -> str:
            response_str = response.getvalue().decode('utf-8')
            root = ET.fromstring(response_str)
            xpath = "{xpath_prefix}{xpath_exp}".format(xpath_prefix=".//", xpath_exp=option['name'])
            return root.findtext(xpath)

        def data(option) -> str:
            root = ET.Element("propfind", xmlns="DAV:")
            prop = ET.SubElement(root, "prop")
            ET.SubElement(prop, option.get('name', ""), xmlns=option.get('namespace', ""))
            tree = ET.ElementTree(root)

            buffer = BytesIO()

            tree.write(buffer)
            return buffer.getvalue().decode('utf-8')

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            response = BytesIO()

            options = {
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.path()),
                'CUSTOMREQUEST':    Client.requests['get_metadata'],
                'HTTPHEADER':       Client.http_header['get_metadata'],
                'POSTFIELDS':       data(option),
                'WRITEDATA':        response
            }

            request = self.Request(options)

            request.perform()
            request.close()

            return parse(response, option)

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

    def set_property(self, remote_path, option: dict) -> None:

        def data(option) -> str:
            root = ET.Element("propertyupdate", xmlns="DAV:")
            root.set('xmlns:u', option.get('namespace', ""))

            set = ET.SubElement(root, "set")
            prop = ET.SubElement(set, "prop")
            opt = ET.SubElement(prop, "{namespace}:{name}".format(namespace='u', name=option['name']))
            opt.text = option.get('value', "")

            tree = ET.ElementTree(root)

            buffer = BytesIO()
            tree.write(buffer)

            return buffer.getvalue().decode('utf-8')

        try:
            urn = Urn(remote_path)

            if not self.exists(urn.path()):
                raise RemoteResourceNotFound(urn.path())

            options = {
                'URL':              '{hostname}{root}{path}'.format(hostname=self.server_hostname, root=self.server_root, path=urn.path()),
                'CUSTOMREQUEST':    Client.requests['set_metadata'],
                'HTTPHEADER':       Client.http_header['set_metadata'],
                'POSTFIELDS':       data(option)
            }

            request = self.Request(options)

            request.perform()
            request.close()

        except pycurl.error as e:
            raise NotConnection(e.args[-1:])

class Resource:

    def __init__(self, client, urn):

        self.client = client
        self.urn = urn

    def __str__(self):

        return "resource {path}".format(path=self.urn.path())

    def rename(self, new_name) -> None:

        old_path = self.urn.path()
        parent_path = self.urn.parent()
        new_name = Urn(new_name).filename()
        new_path = "{directory}{filename}".format(directory=parent_path, filename=new_name)

        self.client.move(remote_path_from=old_path, remote_path_to=new_path)
        self.urn = Urn(new_path)

    def move(self, remote_path) -> None:

        new_urn = Urn(remote_path)
        self.client.move(remote_path_from=self.urn.path(), remote_path_to=new_urn.path())
        self.urn = new_urn

    def copy(self, remote_path):

        urn = Urn(remote_path)
        self.client.copy(remote_path_from=self.urn.path(), remote_path_to=remote_path)
        return Resource(self.client, urn)

    def info(self) -> dict:

        return self.client.info(self.urn.path())

    def read_to(self, buffer) -> None:

        self.client.download_to(buffer=buffer, remote_path=self.urn.path())

    def read(self, local_path) -> None:

        self.client.download(local_path=local_path, remote_path=self.urn.path())

    def read_async(self, local_path, callback=None) -> None:

        self.client.download_sync(local_path=local_path, remote_path=self.urn.path(), callback=callback)

    def write_from(self, buffer) -> None:

        self.client.upload_from(buffer=buffer, remote_path=self.urn.path())

    def write(self, local_path) -> None:

        self.client.upload(local_path=local_path, remote_path=self.urn.path())

    def write_async(self, local_path, callback=None) -> None:

        self.client.upload_sync(local_path=local_path, remote_path=self.urn.path(), callback=callback)

    @property
    def property(self, option: dict) -> str:

        return self.client.get_property(remote_path=self.urn.path(), option=option)

    @property.setter
    def property(self, option, value):

        option['value'] = value.__str__()
        self.client.set_property(remote_path=self.urn.path(), option=option)



if __name__ == "__main__":

    options = {
        'server_hostname':  "server-hostname,
        'server_login':     "server-login",
        'server_password':  "server-password",
        'proxy_hostname':   "proxy-hostname",
        'proxy_login':      "proxy-login",
        'proxy_password':   "proxy-password"
    }

    try:
        yd_client = Client(options)
        files = yd_client.list(directory)

    except WebDavException as e:
        print(e)
