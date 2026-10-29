#!/usr/bin/env python3
"""ユーザーのログイン起動plistを限定設置する。launchctlは呼ばない。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import uuid

LABEL = "local.runtime-process-guard.resident-observatory"
MARKER = "runtime-process-guard.login-installer.v1"


class UnsafeTarget(ValueError):
    pass


def safe_path(path: Path, *, regular: bool = False, executable: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise UnsafeTarget("absolute-safe-path-required")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise UnsafeTarget("unsafe-path-owner-or-link")
        if part != path or not regular:
            if not stat.S_ISDIR(info.st_mode):
                raise UnsafeTarget("directory-required")
            # root所有のsticky一時領域以外では第三者の書込みを拒否する。
            if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                raise UnsafeTarget("unsafe-directory-permissions")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise UnsafeTarget("unsafe-file")
    if regular:
        if not path.is_file() or (executable and not os.access(path, os.X_OK)):
            raise UnsafeTarget("executable-or-file-missing")


def program_for(app: Path) -> str:
    if app.suffix != ".app":
        raise UnsafeTarget("app-bundle-required")
    safe_path(app)
    info_path = app / "Contents/Info.plist"
    safe_path(info_path, regular=True)
    try:
        if info_path.stat().st_size > 1024 * 1024:
            raise UnsafeTarget("bundle-info-too-large")
        info = plistlib.loads(info_path.read_bytes())
        executable = info["CFBundleExecutable"]
        if info["CFBundleIdentifier"] != LABEL or not isinstance(executable, str):
            raise UnsafeTarget("unexpected-bundle-identity")
        if not executable or Path(executable).name != executable or executable in (".", ".."):
            raise UnsafeTarget("invalid-executable-name")
    except (KeyError, TypeError, plistlib.InvalidFileException) as error:
        raise UnsafeTarget("invalid-bundle-info") from error
    program = app / "Contents/MacOS" / executable
    safe_path(program, regular=True, executable=True)
    return str(program)


def payload(program: str) -> dict:
    return {"Label": LABEL, "ProgramArguments": [program], "RunAtLoad": True,
            "KeepAlive": False, "RuntimeProcessGuardInstaller": MARKER}


def read_owned(target: Path) -> tuple[dict, bytes] | None:
    if not target.exists() and not target.is_symlink():
        return None
    safe_path(target, regular=True)
    if target.stat().st_uid != os.getuid():
        raise UnsafeTarget("plist-not-owned-by-current-user")
    if target.stat().st_size > 1024 * 1024:
        raise UnsafeTarget("existing-plist-too-large")
    raw = target.read_bytes()
    try:
        value = plistlib.loads(raw)
        args = value["ProgramArguments"]
        if (not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str)
                or not Path(args[0]).is_absolute() or value != payload(args[0])):
            raise UnsafeTarget("existing-plist-not-created-by-installer")
        program = Path(args[0])
        if len(program.parents) < 3 or program_for(program.parents[2]) != str(program):
            raise UnsafeTarget("existing-program-not-owned-bundle")
    except (KeyError, TypeError, plistlib.InvalidFileException) as error:
        raise UnsafeTarget("invalid-existing-plist") from error
    return value, raw


def atomic_write(target: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".resident-login-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(*, app: Path | None, home: Path, apply: bool = False,
            remove: bool = False, show_paths: bool = False) -> dict:
    safe_path(home)
    directory = home / "Library/LaunchAgents"
    safe_path(directory)
    target = directory / f"{LABEL}.plist"
    previous = read_owned(target)
    backup = None
    if remove:
        action = "remove" if previous else "unchanged"
    else:
        if app is None:
            raise UnsafeTarget("app-required")
        program = program_for(app)
        desired = payload(program)
        if previous and previous[0]["ProgramArguments"] != desired["ProgramArguments"]:
            raise UnsafeTarget("different-program-protected")
        action = "unchanged" if previous and previous[0] == desired else "install"
    if apply and action != "unchanged":
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_path(directory)
        # 書込み直前にも同じ対象を再読取りする。
        if read_owned(target) != previous:
            raise UnsafeTarget("target-changed")
        if previous:
            backup = directory / f"{LABEL}.backup-{uuid.uuid4().hex}.plist"
            atomic_write(backup, previous[1])
        if remove:
            target.unlink()
        else:
            atomic_write(target, plistlib.dumps(desired, sort_keys=True))
    result = {"schema": MARKER, "action": action, "applied": apply,
              "changed": apply and action != "unchanged", "label": LABEL,
              "launchctl_executed": False, "run_at_load": True, "keep_alive": False,
              "backup_created": backup is not None,
              "rollback": "restore-backup" if backup else "remove-owned-plist" if action == "install" else "none"}
    if show_paths:
        result.update(target=str(target), backup=str(backup) if backup else None)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--show-paths", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = install(app=args.app, home=Path.home(), apply=args.apply,
                         remove=args.remove, show_paths=args.show_paths)
    except (OSError, ValueError) as error:
        code = str(error) if isinstance(error, UnsafeTarget) else "filesystem-or-plist-error"
        print(json.dumps({"schema": MARKER, "error": code, "changed": False}))
        return 40
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
