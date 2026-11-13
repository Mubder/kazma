"""A fake docker CLI with two engines, for the move from Docker Desktop.

Run as a program (``python -I -S _fake_docker_engines.py --context C ...``)
by :class:`kazma_core.engine_move.Docker`, so the move's real argv, its real
pipes between two docker processes and real tar streams are exercised. The
state lives in ``$FAKE_DOCKER_STATE``:

* ``<context>.json`` -- an engine: ``up``, ``containers`` (``docker inspect``
  rows, keyed by name), ``images``, ``networks``, ``volumes`` (labels);
* ``<context>.volumes/<name>.tar`` -- a volume's content as a tar archive,
  so owners, modes, symlinks and hard links survive (Windows files cannot
  hold them);
* ``registry.json`` -- images ``docker pull`` finds;
* ``faults.json`` -- ``fail`` (``"<context> <command...>"`` prefixes that
  exit 1), ``corrupt`` (contexts whose ``tar -x`` changes one byte),
  ``keeps_running`` (containers ``stop`` leaves running), ``unhealthy``
  (containers that never pass their health check), ``no_tar`` (images);
* ``calls/`` -- one file per call, named by when it began: what the tests
  read the order from.

Standard library only (it runs with ``-S``).
"""

from __future__ import annotations

import copy as copy_module
import io
import json
import os
import secrets
import sys
import tarfile
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])


def _load(name: str, default):
    path = STATE / name
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save(name: str, data) -> None:
    (STATE / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def engine_file(ctx: str) -> str:
    return f"{ctx}.json"


def load_engine(ctx: str) -> dict:
    return _load(engine_file(ctx), {"up": False, "containers": {}, "images": {}, "networks": {}, "volumes": {}})


def volume_path(ctx: str, name: str) -> Path:
    return STATE / f"{ctx}.volumes" / f"{name}.tar"


def read_volume(ctx: str, name: str) -> list[tuple[tarfile.TarInfo, bytes]]:
    path = volume_path(ctx, name)
    if not path.is_file():
        return []
    out = []
    with tarfile.open(path, "r") as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member).read() if member.isfile() else b""
            out.append((member, data))
    return out


def write_volume(ctx: str, name: str, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    path = volume_path(ctx, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isfile() else None)


def log_call(entry: list[str]) -> None:
    """One file per call (two calls run at once in a pipe, and an append
    from two processes is not atomic on Windows), named by when it began."""
    import time

    folder = STATE / "calls"
    folder.mkdir(exist_ok=True)
    (folder / f"{time.time_ns():020d}-{os.getpid()}.json").write_text(json.dumps(entry), encoding="utf-8")


def fail(msg: str, code: int = 1) -> int:
    sys.stderr.write(msg + "\n")
    return code


def find_container(eng: dict, ref: str) -> dict | None:
    if ref in eng["containers"]:
        return eng["containers"][ref]
    for row in eng["containers"].values():
        if row["Id"].startswith(ref):
            return row
    return None


def find_image(eng: dict, ref: str) -> dict | None:
    if ref in eng["images"]:
        return eng["images"][ref]
    for image in eng["images"].values():
        if ref in image.get("RepoTags", []) or ref in image.get("RepoDigests", []) or image["Id"].startswith(ref):
            return image
    return None


def take(args: list[str], flag: str) -> list[str]:
    """Remove every ``flag value`` pair from *args*; the values, in order."""
    values = []
    while flag in args:
        i = args.index(flag)
        values.append(args[i + 1])
        del args[i:i + 2]
    return values


def parse_mount(spec: str) -> dict:
    parts = dict(item.split("=", 1) if "=" in item else (item, "true") for item in spec.split(","))
    kind = parts.get("type", "volume")
    mount = {"Type": kind, "Destination": parts.get("dst") or parts.get("target"),
             "RW": "readonly" not in parts}
    if kind == "volume":
        mount.update({"Name": parts["src"], "Driver": "local", "Source": f"/var/lib/docker/volumes/{parts['src']}/_data"})
    elif kind == "bind":
        mount["Source"] = parts["src"]
    return mount


def ms(text: str) -> int:
    return int(text.rstrip("ms")) * 1_000_000


def cmd_create(ctx: str, eng: dict, args: list[str]) -> int:
    args = list(args[1:])
    name = take(args, "--name")[0]
    if name in eng["containers"]:
        return fail(f"Conflict. The container name /{name} is already in use")
    labels = dict(v.split("=", 1) for v in take(args, "--label"))
    env = take(args, "--env")
    flags = {f: f in args for f in ("--tty", "--interactive", "--no-healthcheck", "--privileged", "--init",
                                    "--read-only")}
    for f in flags:
        while f in args:
            args.remove(f)
    single = {}
    for flag in ("--workdir", "--user", "--hostname", "--domainname", "--stop-signal", "--stop-timeout",
                 "--health-cmd", "--health-interval", "--health-timeout", "--health-start-period",
                 "--health-start-interval", "--health-retries", "--restart", "--memory", "--cpus", "--shm-size",
                 "--network", "--entrypoint"):
        got = take(args, flag)
        if got:
            single[flag] = got[-1]
    multi = {flag: take(args, flag) for flag in ("--publish", "--mount", "--tmpfs", "--cap-add", "--cap-drop",
                                                  "--security-opt", "--add-host", "--dns", "--dns-search",
                                                  "--dns-option", "--group-add", "--ulimit", "--sysctl",
                                                  "--network-alias")}
    if args[0].startswith("-"):  # every flag the move passes was taken above; the rest is the image and command
        return fail(f"unknown flag: {args[0]}")
    image_ref, cmd = args[0], args[1:]
    image = find_image(eng, image_ref)
    if image is None:
        return fail(f"Unable to find image '{image_ref}' locally")
    ports: dict[str, list] = {}
    for spec in multi["--publish"]:
        bits = spec.split(":")
        ip, host, port = (bits if len(bits) == 3 else ["", *bits])
        ports.setdefault(port, []).append({"HostIp": ip, "HostPort": host})
    restart = single.get("--restart", "no")
    name_part, _, count = restart.partition(":")
    mounts = [parse_mount(m) for m in multi["--mount"]]
    for m in mounts:
        if m["Type"] == "volume":
            eng["volumes"].setdefault(m["Name"], {"Name": m["Name"], "Driver": "local", "Labels": {}, "Options": {}})
    network = single.get("--network", "bridge")
    if network not in ("bridge", "none") and network not in eng["networks"]:
        return fail(f"network {network} not found")
    health = None
    if "--health-cmd" in single:
        health = {"Test": ["CMD-SHELL", single["--health-cmd"]]}
        for flag, key in (("--health-interval", "Interval"), ("--health-timeout", "Timeout"),
                          ("--health-start-period", "StartPeriod"), ("--health-start-interval", "StartInterval")):
            if flag in single:
                health[key] = ms(single[flag])
        if "--health-retries" in single:
            health["Retries"] = int(single["--health-retries"])
    if flags["--no-healthcheck"]:
        health = {"Test": ["NONE"]}
    cid = secrets.token_hex(32)
    row = {
        "Id": cid, "Name": "/" + name, "Image": image["Id"],
        "Config": {"Image": image_ref, "Env": env, "Labels": labels, "Cmd": cmd,
                   "Entrypoint": [single["--entrypoint"]] if "--entrypoint" in single else None,
                   "WorkingDir": single.get("--workdir", ""), "User": single.get("--user", ""),
                   "Hostname": single.get("--hostname", cid[:12]), "Tty": flags["--tty"],
                   "OpenStdin": flags["--interactive"], "Healthcheck": health,
                   "StopSignal": single.get("--stop-signal")},
        "HostConfig": {"RestartPolicy": {"Name": name_part, "MaximumRetryCount": int(count or 0)},
                       "PortBindings": ports, "NetworkMode": network, "CapAdd": multi["--cap-add"] or None,
                       "Privileged": flags["--privileged"], "Init": flags["--init"],
                       "Memory": int(single.get("--memory", 0))},
        "Mounts": mounts,
        "NetworkSettings": {"Networks": {network: {"Aliases": multi["--network-alias"] or None}}},
        "State": {"Running": False, "Status": "created"},
        "_files": {}, "_diff": [],
    }
    eng["containers"][name] = row
    print(cid)
    return 0


def clean(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name


def tar_bytes(members: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        for info, data in members:
            copy = copy_module.copy(info)
            copy.name = "./" + clean(info.name)
            if copy.islnk():
                copy.linkname = "./" + clean(info.linkname)
            tar.addfile(copy, io.BytesIO(data) if info.isfile() else None)
    return buf.getvalue()


def cmd_run(ctx: str, eng: dict, args: list[str], faults: dict) -> int:
    args = list(args[1:])
    for flag in ("--rm", "-i"):
        while flag in args:
            args.remove(flag)
    take(args, "--network")
    take(args, "--user")
    entry = take(args, "--entrypoint")
    vols = take(args, "-v")
    image_ref, rest = args[0], args[1:]
    image = find_image(eng, image_ref)
    if image is None:
        return fail(f"Unable to find image '{image_ref}' locally")
    if entry != ["tar"]:
        return fail("the fake runs tar only")
    if image["Id"] in faults.get("no_tar", []) or image_ref in faults.get("no_tar", []):
        return fail('exec: "tar": executable file not found in $PATH', 127)
    if rest == ["-C", "/dev", "-cf", "-", "null"]:
        sys.stdout.buffer.write(b"\0" * 1024)
        return 0
    volume = vols[0].split(":")[0]
    eng["volumes"].setdefault(volume, {"Name": volume, "Driver": "local", "Labels": {}, "Options": {}})
    if rest == ["-C", "/v", "-cf", "-", "."]:
        sys.stdout.buffer.write(tar_bytes(read_volume(ctx, volume)))
        return 0
    if rest == ["-C", "/v", "-xf", "-"]:
        if vols[0].endswith(":ro"):
            return fail("read-only file system")
        incoming = []
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tar:
            for member in tar:
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                if name in (".", ""):
                    continue
                data = tar.extractfile(member).read() if member.isfile() else b""
                member.name = name
                if member.islnk():
                    member.linkname = clean(member.linkname)
                incoming.append((member, data))
        if ctx in faults.get("corrupt", []):
            for i, (info, data) in enumerate(incoming):
                if info.isfile() and data:
                    incoming[i] = (info, bytes([data[0] ^ 1]) + data[1:])
                    break
        have = {i.name: (i, d) for i, d in read_volume(ctx, volume)}
        for info, data in incoming:
            have[info.name] = (info, data)
        write_volume(ctx, volume, list(have.values()))
        return 0
    return fail(f"the fake does not run tar {rest}")


def main(argv: list[str]) -> int:
    if argv[:1] != ["--context"]:
        return fail("the fake needs --context")
    ctx, args = argv[1], argv[2:]
    faults = _load("faults.json", {})
    log_call([ctx, *args])
    joined = " ".join([ctx, *args])
    for prefix, message in (faults.get("fail") or {}).items():
        if joined.startswith(prefix):
            return fail(message)
    eng = load_engine(ctx)
    if not eng.get("up"):
        return fail("Cannot connect to the Docker daemon")
    code = dispatch(ctx, eng, args, faults)
    _save(engine_file(ctx), eng)
    return code


def dispatch(ctx: str, eng: dict, args: list[str], faults: dict) -> int:  # noqa: C901 -- a CLI's verbs
    verb = args[0]
    if verb == "version":
        print("29.9.3")
        return 0
    if verb == "ps":
        print("\n".join(row["Id"] for row in eng["containers"].values()))
        return 0
    if verb == "inspect":
        rows = [find_container(eng, ref) for ref in args[1:]]
        if any(r is None for r in rows):
            return fail("Error: No such object")
        print(json.dumps([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]))
        return 0
    if verb == "image" and args[1] == "inspect":
        image = find_image(eng, args[2])
        if image is None:
            return fail(f"Error: No such image: {args[2]}")
        print(json.dumps([image]))
        return 0
    if verb == "volume":
        sub, rest = args[1], args[2:]
        if sub == "inspect":
            found = [eng["volumes"].get(n) for n in rest]
            if any(v is None for v in found):
                return fail("Error: No such volume")
            print(json.dumps(found))
            return 0
        if sub == "ls":
            print("\n".join(eng["volumes"]))
            return 0
        if sub == "create":
            rest = list(rest)
            labels = dict(v.split("=", 1) for v in take(rest, "--label"))
            eng["volumes"][rest[0]] = {"Name": rest[0], "Driver": "local", "Labels": labels, "Options": {}}
            print(rest[0])
            return 0
        if sub == "rm":
            if any(rest[0] in [m.get("Name") for m in row["Mounts"]] for row in eng["containers"].values()):
                return fail("volume is in use")
            eng["volumes"].pop(rest[0], None)
            volume_path(ctx, rest[0]).unlink(missing_ok=True)
            return 0
    if verb == "network":
        sub, rest = args[1], list(args[2:])
        if sub == "inspect":
            if rest[0] not in eng["networks"]:
                return fail("Error: No such network")
            print(json.dumps([eng["networks"][rest[0]]]))
            return 0
        if sub == "create":
            driver = take(rest, "--driver")
            labels = dict(v.split("=", 1) for v in take(rest, "--label"))
            internal, attachable = "--internal" in rest, "--attachable" in rest
            rest = [a for a in rest if not a.startswith("--")]
            eng["networks"][rest[0]] = {"Name": rest[0], "Driver": (driver or ["bridge"])[0], "Labels": labels,
                                        "Internal": internal, "Attachable": attachable}
            return 0
        if sub == "rm":
            eng["networks"].pop(rest[0], None)
            return 0
        if sub == "connect":
            aliases = take(rest, "--alias")
            net, name = rest
            find_container(eng, name)["NetworkSettings"]["Networks"][net] = {"Aliases": aliases or None}
            return 0
    if verb == "save":
        image = find_image(eng, args[1])
        if image is None:
            return fail("no such image")
        sys.stdout.buffer.write(json.dumps({"image": image, "ref": args[1]}).encode("utf-8"))
        return 0
    if verb == "load":
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        image, ref = data["image"], data["ref"]
        eng["images"][image["Id"]] = image
        print(f"Loaded image: {ref}" if ref in image.get("RepoTags", []) else f"Loaded image ID: {image['Id']}")
        return 0
    if verb == "pull":
        registry = _load("registry.json", {})
        if args[1] not in registry:
            return fail(f"pull access denied for {args[1]}")
        image = registry[args[1]]
        eng["images"][image["Id"]] = image
        return 0
    if verb == "tag":
        image = find_image(eng, args[1])
        image.setdefault("RepoTags", []).append(args[2])
        return 0
    if verb == "create":
        return cmd_create(ctx, eng, args)
    if verb == "run":
        return cmd_run(ctx, eng, args, faults)
    if verb in ("start", "stop", "update", "rm", "diff"):
        name = args[-1]
        row = find_container(eng, name)
        if row is None:
            return fail(f"Error: No such container: {name}")
        if verb == "start":
            row["State"] = {"Running": True, "Status": "running"}
            check = row["Config"].get("Healthcheck") or {}
            if check.get("Test") and check["Test"][0] != "NONE":
                bad = name in faults.get("unhealthy", [])
                row["State"]["Health"] = {"Status": "unhealthy" if bad else "healthy"}
        elif verb == "stop":
            if name not in faults.get("keeps_running", []):
                row["State"] = {"Running": False, "Status": "exited"}
        elif verb == "update":
            policy = args[args.index("--restart") + 1]
            name_part, _, count = policy.partition(":")
            row["HostConfig"]["RestartPolicy"] = {"Name": name_part, "MaximumRetryCount": int(count or 0)}
        elif verb == "rm":
            del eng["containers"][row["Name"].lstrip("/")]
        elif verb == "diff":
            print("\n".join(row.get("_diff") or []))
        return 0
    if verb == "cp":
        rest = [a for a in args[1:] if a != "-a"]
        src, dst = rest
        if dst == "-":
            name, _, path = src.partition(":")
            row = find_container(eng, name)
            data = row["_files"][path].encode("utf-8")
            info = tarfile.TarInfo(path.rsplit("/", 1)[-1])
            info.size = len(data)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tar.addfile(info, io.BytesIO(data))
            sys.stdout.buffer.write(buf.getvalue())
            return 0
        name, _, parent = dst.partition(":")
        row = find_container(eng, name)
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tar:
            for member in tar:
                row["_files"][parent.rstrip("/") + "/" + member.name] = tar.extractfile(member).read().decode("utf-8")
        return 0
    return fail(f"the fake does not know: {args}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
