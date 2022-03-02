class WebDavException(Exception):
    def __str__(self):
        return "WebDavException"
    def __repr__(self):
        return self.__str__()


class NotValid(WebDavException):
    pass


class OptionNotValid(NotValid):
    def __init__(self, name, value, ns=""):
        self.name = name
        self.value = value
        self.ns = ns

    def __str__(self):
        return "Option ({ns}{name}={value}) have invalid name or value".format(ns=self.ns, name=self.name, value=self.value)


class CertificateNotValid(NotValid):
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


class MethodNotSupported(WebDavException):
    def __init__(self, name, server):
        self.name = name
        self.server = server

    def __str__(self):
        return "Method {name} not supported for {server}".format(name=self.name, server=self.server)


class NotConnection(WebDavException):
    def __init__(self, hostname):
        self.hostname = hostname

    def __str__(self):
        return "Not connection with {hostname}".format(hostname=self.hostname)


class NotEnoughSpace(WebDavException):
    def __init__(self):
        pass

    def __str__(self):
        return "Not enough space on the server"
        
class InternalServerError(WebDavException):
    def __init__(self):
        pass

    def __str__(self):
        return "Internal Server Error: Permission Problem?"

class UnhandledError(WebDavException):
    def __init__(self):
        pass

    def __str__(self):
        return "Unhandled Error"