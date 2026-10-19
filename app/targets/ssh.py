"""Bounded SSH commands and SFTP; credentials and trust remain on the control plane."""

import io
import json
import os
import shlex
import stat
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import paramiko

from app.executors.base import ExecutorError
from app.providers.credentials import MAX_SECRET
from app.targets.schemas import SshConfig


class SshTransport:
    def __init__(
        self,
        config: SshConfig,
        credential_ref: str,
        *,
        known_hosts: Path,
        secret_directory: Path,
        env_names: set[str],
    ) -> None:
        self.config, self.reference = config, credential_ref
        self.known_hosts, self.secret_directory, self.env_names = (
            known_hosts,
            secret_directory,
            env_names,
        )

    def key(self) -> paramiko.PKey:
        # Resolve only at connection time. Never retain resolved key bytes in target state.
        try:
            if self.reference.startswith("env://"):
                name = self.reference[6:]
                if name not in self.env_names:
                    raise ValueError
                value = os.environ[name]
            else:
                root = self.secret_directory.resolve(strict=True)
                path = Path(self.reference[7:]).resolve(strict=True)
                if not path.is_relative_to(root) or path == root:
                    raise ValueError
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_mode & 0o077
                    ):
                        raise ValueError
                    value = source.read(MAX_SECRET + 1).decode()
            if len(value.encode()) > MAX_SECRET:
                raise ValueError
            for key_type in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    return key_type.from_private_key(io.StringIO(value))
                except paramiko.SSHException:
                    continue
        except (OSError, ValueError, KeyError):
            pass
        raise ExecutorError(
            "SSH key reference unavailable; use an approved unencrypted private key"
        )

    @contextmanager
    def connect(self) -> Iterator[paramiko.SSHClient]:
        client = paramiko.SSHClient()
        try:
            client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(
                self.config.host,
                port=self.config.port,
                username=self.config.username,
                pkey=self.key(),
                allow_agent=False,
                look_for_keys=False,
                timeout=10,
                auth_timeout=10,
                banner_timeout=10,
            )
        except ExecutorError:
            client.close()
            raise
        except (OSError, paramiko.SSHException):
            client.close()
            raise ExecutorError(
                "SSH unavailable; verify credentials and the operator known-hosts file"
            ) from None
        except BaseException:
            client.close()
            raise
        try:
            yield client
        except FileNotFoundError:
            # Let SFTP callers distinguish absent files/workspaces from connectivity loss.
            raise
        except (OSError, paramiko.SSHException):
            raise ExecutorError("SSH operation unavailable; retry shortly") from None
        finally:
            client.close()

    def command(self, arguments: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
        with self.connect() as client:
            transport = client.get_transport()
            assert transport is not None
            with transport.open_session(timeout=10) as channel:
                channel.settimeout(10)
                channel.exec_command(shlex.join(arguments))
                stdout, stderr = bytearray(), bytearray()
                deadline = time.monotonic() + timeout
                while True:
                    if channel.recv_ready():
                        stdout.extend(channel.recv(65536))
                    if channel.recv_stderr_ready():
                        stderr.extend(channel.recv_stderr(65536))
                    if len(stdout) + len(stderr) > 4 * 1024**2 or time.monotonic() > deadline:
                        raise ExecutorError("SSH command exceeded its time or response limit")
                    if (
                        channel.exit_status_ready()
                        and not channel.recv_ready()
                        and not channel.recv_stderr_ready()
                    ):
                        break
                    time.sleep(0.01)
                return subprocess.CompletedProcess(
                    arguments,
                    channel.recv_exit_status(),
                    stdout.decode(errors="replace"),
                    stderr.decode(errors="replace"),
                )

    def docker(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self.command(["docker", "--host", "unix:///var/run/docker.sock", *arguments])

    def identity(self) -> tuple[int, int]:
        values = []
        for flag in ("-u", "-g"):
            result = self.command(["id", flag])
            if result.returncode or not result.stdout.strip().isdigit():
                raise ExecutorError("Cannot determine remote operator identity")
            values.append(int(result.stdout.strip()))
        if values[0] == 0:
            raise ExecutorError("SSH Docker execution requires a non-root remote user")
        return values[0], values[1]

    @contextmanager
    def sftp(self) -> Iterator[paramiko.SFTPClient]:
        with self.connect() as client:
            with client.open_sftp() as sftp:
                channel = sftp.get_channel()
                assert channel is not None
                channel.settimeout(60)
                yield sftp

    @staticmethod
    def directory(sftp: paramiko.SFTPClient, path: str, *, create: bool = False) -> None:
        """Reject links at every component, including pre-existing workspace ancestors."""
        current = PurePosixPath("/")
        for part in PurePosixPath(path).parts[1:]:
            current /= part
            try:
                info = sftp.lstat(str(current))
            except FileNotFoundError:
                if not create:
                    raise
                sftp.mkdir(str(current), mode=0o700)
                info = sftp.lstat(str(current))
            if info.st_mode is None or not stat.S_ISDIR(info.st_mode):
                raise ExecutorError("Remote workspace contains a link or non-directory")

    def upload(self, local: Path, remote: str) -> None:
        uid, _ = self.identity()
        try:
            with self.sftp() as sftp:
                self.directory(sftp, self.config.workspace, create=True)
                info = sftp.lstat(self.config.workspace)
                if info.st_uid != uid or info.st_mode is None or info.st_mode & 0o077:
                    raise ExecutorError(
                        "Remote workspace must be private and owned by the SSH user"
                    )
                self.directory(sftp, str(PurePosixPath(remote).parent), create=True)
                # Never merge into or replay a previous attempt directory.
                sftp.mkdir(remote, mode=0o700)
                deadline, total, count = time.monotonic() + 1800, 0, 0
                for path in sorted(local.rglob("*")):
                    dest = remote + "/" + path.relative_to(local).as_posix()
                    if path.is_symlink():
                        raise ExecutorError("Input staging cannot contain links")
                    if path.is_dir():
                        sftp.mkdir(dest, mode=0o700)
                    elif path.is_file():
                        total += path.stat().st_size
                        count += 1
                        if total > 9 * 1024**3 or count > 8192 or time.monotonic() > deadline:
                            raise ExecutorError("SSH input transfer exceeded its limit")
                        sftp.put(str(path), dest, confirm=True)
                        sftp.chmod(dest, 0o600)
                    else:
                        raise ExecutorError("Input staging contains a non-regular file")
        except OSError:
            raise ExecutorError(
                "SFTP input transfer failed; create a new attempt after recovery"
            ) from None

    def read(self, remote: str, limit: int, offset: int = 0, *, missing_ok: bool = False) -> bytes:
        try:
            with self.sftp() as sftp:
                self.directory(sftp, str(PurePosixPath(remote).parent))
                info = sftp.lstat(remote)
                if info.st_mode is None or not stat.S_ISREG(info.st_mode):
                    raise ExecutorError("Remote output must be a regular file")
                with sftp.open(remote, "rb") as source:
                    source.seek(offset)
                    return source.read(limit + 1)
        except FileNotFoundError:
            if missing_ok:
                return b""
            raise ExecutorError("Remote output is unavailable") from None
        except OSError:
            raise ExecutorError("SFTP output read failed") from None

    def download(self, remote: str, local: Path, size: int) -> None:
        """Stream only manifest-inventoried bytes; never recursively trust remote trees."""
        try:
            with self.sftp() as sftp:
                self.directory(sftp, str(PurePosixPath(remote).parent))
                info = sftp.lstat(remote)
                if info.st_mode is None or not stat.S_ISREG(info.st_mode) or info.st_size != size:
                    raise ExecutorError("Remote output type or size differs from the manifest")
                local.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                deadline, remaining = time.monotonic() + 1800, size
                with sftp.open(remote, "rb") as source, local.open("wb") as target:
                    while remaining:
                        chunk = source.read(min(1024**2, remaining))
                        if not chunk or time.monotonic() > deadline:
                            raise ExecutorError("SFTP output truncated or transfer timed out")
                        target.write(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise ExecutorError("Remote output grew during transfer")
        except OSError:
            raise ExecutorError("SFTP output transfer failed") from None

    def remove(self, remote: str) -> None:
        # Only executor-generated UUID children of the configured private root.
        path = PurePosixPath(remote)
        if not path.is_relative_to(self.config.workspace) or path == PurePosixPath(
            self.config.workspace
        ):
            raise ExecutorError("Invalid remote cleanup path")
        try:
            with self.sftp() as sftp:
                try:
                    self.directory(sftp, remote)
                except FileNotFoundError:
                    return

                def erase(directory: str, depth: int = 0) -> None:
                    if depth > 16:
                        raise ExecutorError("Remote cleanup depth exceeded")
                    for item in sftp.listdir_attr(directory):
                        if item.filename in (".", "..") or "/" in item.filename:
                            raise ExecutorError("Invalid remote cleanup entry")
                        child = directory + "/" + item.filename
                        if item.st_mode is not None and stat.S_ISDIR(item.st_mode):
                            erase(child, depth + 1)
                        else:
                            sftp.remove(child)  # unlink links; never follow them
                    sftp.rmdir(directory)

                erase(remote)
        except OSError:
            raise ExecutorError("Remote workspace cleanup failed; retry shortly") from None

    def request(
        self, endpoint: str, method: str, path: str, payload: dict[str, Any] | None, timeout: int
    ) -> bytes:
        """HTTP through an SSH direct-tcpip channel; no exposed port or remote agent."""
        parsed = urlsplit(endpoint)
        with self.connect() as client:
            transport = client.get_transport()
            assert transport is not None and parsed.hostname is not None
            channel = transport.open_channel(
                "direct-tcpip", (parsed.hostname, 8000), ("127.0.0.1", 0), timeout=10
            )
            channel.settimeout(timeout)
            connection = HTTPConnection(parsed.hostname, 8000, timeout=timeout)
            connection.sock = channel
            try:
                data = json.dumps(payload).encode() if payload is not None else None
                connection.request(
                    method, path, body=data, headers={"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                body = response.read(1024**2 + 1)
                if response.status != 200 or len(body) > 1024**2:
                    raise ExecutorError(
                        "Remote inference returned an invalid or oversized response"
                    )
                return body
            except (OSError, ValueError, HTTPException):
                raise ExecutorError("Remote inference unavailable") from None
            finally:
                connection.close()
