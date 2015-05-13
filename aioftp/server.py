import asyncio
import pathlib


from . import common
from . import errors


def add_prefix(message):

    return str.format("aioftp server: {}", message)


class Permission:

    def __init__(self, path=".", *, readable=True, writable=True):

        self.path = pathlib.Path(path)
        self.readable = readable
        self.writable = writable

    def is_parent(self, other):

        try:

            other.relative_to(self.path)
            return True

        except ValueError:

            return False

    def __repr__(self):

        return str.format(
            "Permission({!r}, readable={!r}, writable={!r}",
            self.path,
            self.readable,
            self.writable,
        )


class User:

    def __init__(self, login=None, password=None, *,
                 base_path=pathlib.Path("."), home_path=pathlib.Path("."),
                 permissions=None):

        self.login = login
        self.password = password
        self.base_path = base_path
        self.home_path = home_path
        self.permissions = permissions or [Permission()]

    def get_permissions(self, path):

        path = pathlib.Path(path)
        parents = filter(lambda p: p.is_parent(path), self.permissions)
        perm = min(parents, key=lambda p: len(path.relative_to(p.path).parts))
        return perm

    def __repr__(self):

        return str.format(
            "User({!r}, {!r}, base_path={!r}, home_path={!r}, "
            "permissions={!r})",
            self.login,
            self.password,
            self.base_path,
            self.home_path,
            self.permissions,
        )


class BaseServer:

    @asyncio.coroutine
    def start(self, host=None, port=None, **kw):

        self.connections = {}
        coro = asyncio.start_server(self.dispatcher, host, port, **kw)
        self.server = yield from coro
        host, port = self.server.sockets[0].getsockname()
        message = str.format("serving on {}:{}", host, port)
        common.logger.info(add_prefix(message))

    def close(self):

        self.server.close()

    @asyncio.coroutine
    def wait_closed(self):

        yield from self.server.wait_closed()

    def write_line(self, reader, writer, code, line, last=False,
                   encoding="utf-8"):

        separator = " " if last else "-"
        message = str.strip(code + separator + line)
        common.logger.info(add_prefix(message))
        writer.write(str.encode(message + "\r\n", encoding=encoding))

    @asyncio.coroutine
    def write_response(self, reader, writer, code, lines=""):

        lines = common.wrap_with_container(lines)
        for line in lines:

            self.write_line(reader, writer, code, line, line is lines[-1])

        yield from writer.drain()

    @asyncio.coroutine
    def parse_command(self, reader, writer):

        line = yield from asyncio.wait_for(reader.readline(), self.timeout)
        if not line:

            raise errors.ConnectionClosedError()

        s = str.rstrip(bytes.decode(line, encoding="utf-8"))
        common.logger.info(add_prefix(s))
        cmd, _, rest = str.partition(s, " ")
        return str.lower(cmd), rest

    @asyncio.coroutine
    def dispatcher(self, reader, writer):

        host, port = writer.transport.get_extra_info("peername", ("", ""))
        message = str.format("new connection from {}:{}", host, port)
        common.logger.info(add_prefix(message))

        key = reader, writer
        connection = {"host": host, "port": port}
        self.connections[key] = connection

        try:

            ok, code, info = self.greeting(connection, "")
            yield from self.write_response(reader, writer, code, info)

            while ok:

                cmd, rest = yield from self.parse_command(reader, writer)
                if cmd == "pass":

                    # is there a better solution?
                    cmd = "pass_"

                if hasattr(self, cmd):

                    ok, code, info = getattr(self, cmd)(connection, rest)
                    yield from self.write_response(reader, writer, code, info)

                else:

                    yield from self.write_response(
                        reader,
                        writer,
                        "502",
                        "Not implemented",
                    )

        finally:

            writer.close()
            self.connections.pop(key)



class Server(BaseServer):

    def __init__(self, users=None, timeout=None):

        self.users = users or [User()]
        self.timeout = timeout

    def greeting(self, connection, rest):

        return True, "220", "welcome"

    def user(self, connection, rest):

        current_user = None
        for user in self.users:

            if user.login is None and current_user is None:

                current_user = user

            elif user.login == rest:

                current_user = user
                break

        if current_user is None:

            code, info = "530", "no such username"
            ok = False

        elif current_user.login is None:

            connection["logged"] = True
            connection["current_directory"] = current_user.home_path
            connection["user"] = current_user
            code, info = "230", "anonymous login"
            ok = True

        else:

            connection["user"] = current_user
            code, info = "331", "require password"
            ok = True

        return ok, code, info

    def pass_(self, connection, rest):

        if "user" in connection:

            if connection["user"].password == rest:

                connection["logged"] = True
                connection["current_directory"] = current_user.home_path
                connection["user"] = current_user
                code, info = "230", "normal login"

            else:

                code, info = "530", "wrong password"

        else:

            code, info = "503", "bad sequence of commands"

        return True, code, info

    def quit(self, connection, rest):

        return False, "221", "bye"

    def pwd(self, connection, rest):

        if connection.get("logged", False):

            code, info = "257", connection["current_directory"]

        else:

            code, info = "503", "bad sequence of commands (not logged)"

        return True, code, info
