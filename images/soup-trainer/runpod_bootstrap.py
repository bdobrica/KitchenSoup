"""Privileged SSH bootstrap only; Soup runs under the existing non-root identity."""

import os
import subprocess
from pathlib import Path


def main():
    keys = os.environ.get("PUBLIC_KEY", "")
    if not keys or len(keys.encode()) > 32768:
        raise SystemExit("Registered public SSH keys required")
    lines = keys.strip().splitlines()
    if any(not line.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")) for line in lines):
        raise SystemExit("Unsupported public SSH key format")
    root = Path("/home/runner/.ssh")
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "authorized_keys").write_text("\n".join(lines) + "\n")
    for path in (root, root / "authorized_keys"):
        os.chown(path, 10001, 10001)
    (root / "authorized_keys").chmod(0o600)
    ephemeral = os.environ.get("KITCHENSOUP_SSH_HOST_KEY")
    host_key = Path("/etc/ssh/ssh_host_rsa_key")
    if ephemeral:
        if len(ephemeral.encode()) > 8192 or "PRIVATE KEY" not in ephemeral:
            raise SystemExit("Invalid ephemeral host key")
        host_key.write_text(ephemeral)
        host_key.chmod(0o600)
    else:
        subprocess.run(["ssh-keygen", "-A"], check=True, timeout=10)
        host_key = Path("/etc/ssh/ssh_host_ed25519_key")
    # Host certificates/keys must be provisioned through the operator's image trust
    # process. The client never enrolls an unknown key from the network.
    Path("/etc/ssh/sshd_config").write_text(
        f"Port 22\nHostKey {host_key}\n"
        "PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
        "PermitEmptyPasswords no\nPubkeyAuthentication yes\nUsePAM no\nAllowUsers runner\n"
        "AllowTcpForwarding no\nX11Forwarding no\nPermitUserEnvironment no\n"
        "Subsystem sftp internal-sftp\nMaxSessions 4\nLoginGraceTime 20\n"
    )
    # No provider-injected secrets or caller environment enters SSH sessions.
    os.execve(
        "/usr/sbin/sshd", ["/usr/sbin/sshd", "-D", "-e"], {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    )


if __name__ == "__main__":
    main()
