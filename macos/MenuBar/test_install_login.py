"""一時homeと架空bundleだけでログインplistの境界を検査する。"""
import importlib.util
from pathlib import Path
import plistlib

import pytest

spec = importlib.util.spec_from_file_location("install_login", Path(__file__).with_name("install_login.py"))
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = tmp_path / "Resident.app"
    binary = app / "Contents/MacOS/Resident"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": login.LABEL, "CFBundleExecutable": "Resident"}))
    return home, app


def test_preview_is_private_and_does_not_write(setup):
    home, app = setup
    result = login.install(app=app, home=home)
    assert result["action"] == "install"
    assert not result["changed"] and not result["launchctl_executed"]
    assert str(home) not in str(result) and str(app) not in str(result)
    assert not (home / "Library").exists()


def test_install_is_atomic_private_and_idempotent(setup):
    home, app = setup
    result = login.install(app=app, home=home, apply=True, show_paths=True)
    target = Path(result["target"])
    assert target.stat().st_mode & 0o777 == 0o600
    value = plistlib.loads(target.read_bytes())
    assert value["ProgramArguments"] == [str(app / "Contents/MacOS/Resident")]
    assert value["KeepAlive"] is False and value["RunAtLoad"] is True
    before = target.stat().st_mtime_ns
    assert login.install(app=app, home=home, apply=True)["action"] == "unchanged"
    assert target.stat().st_mtime_ns == before


def test_remove_only_owned_plist_with_backup(setup):
    home, app = setup
    installed = login.install(app=app, home=home, apply=True, show_paths=True)
    target = Path(installed["target"])
    raw = target.read_bytes()
    assert not login.install(app=None, home=home, remove=True)["changed"]
    result = login.install(app=None, home=home, remove=True, apply=True, show_paths=True)
    assert result["backup_created"] and result["rollback"] == "restore-backup"
    assert not target.exists()
    assert Path(result["backup"]).read_bytes() == raw
    assert Path(result["backup"]).stat().st_mode & 0o777 == 0o600
    assert login.install(app=None, home=home, remove=True, apply=True)["action"] == "unchanged"


@pytest.mark.parametrize("change", ["label", "marker", "program", "symlink", "writable"])
def test_foreign_plist_is_protected(setup, change):
    home, app = setup
    result = login.install(app=app, home=home, apply=True, show_paths=True)
    target = Path(result["target"])
    value = plistlib.loads(target.read_bytes())
    if change == "label":
        value["Label"] = "another.agent"
    elif change == "marker":
        value.pop("RuntimeProcessGuardInstaller")
    elif change == "program":
        value["ProgramArguments"] = ["/bin/echo"]
    elif change == "symlink":
        target.rename(target.with_suffix(".original"))
        target.symlink_to(target.with_suffix(".original"))
    else:
        target.chmod(0o666)
    if change in ("label", "marker", "program"):
        target.write_bytes(plistlib.dumps(value))
    with pytest.raises(login.UnsafeTarget):
        login.install(app=app, home=home, apply=True)
    with pytest.raises(login.UnsafeTarget):
        login.install(app=None, home=home, remove=True, apply=True)


@pytest.mark.parametrize("change", ["bundle-link", "parent-link", "identifier", "traversal", "nonexecuting", "relative"])
def test_invalid_bundle_fails_closed(setup, tmp_path, change):
    home, app = setup
    info = app / "Contents/Info.plist"
    if change == "bundle-link":
        link = tmp_path / "link.app"
        link.symlink_to(app)
        app = link
    elif change == "parent-link":
        link = tmp_path / "linked"
        link.symlink_to(app.parent)
        app = link / app.name
    elif change in ("identifier", "traversal"):
        value = plistlib.loads(info.read_bytes())
        value["CFBundleIdentifier" if change == "identifier" else "CFBundleExecutable"] = "wrong" if change == "identifier" else "../wrong"
        info.write_bytes(plistlib.dumps(value))
    elif change == "nonexecuting":
        (app / "Contents/MacOS/Resident").chmod(0o600)
    else:
        app = Path("relative.app")
    with pytest.raises(login.UnsafeTarget):
        login.install(app=app, home=home, apply=True)
    assert not (home / "Library").exists()


def test_launchagents_parent_link_is_rejected(setup, tmp_path):
    home, app = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "Library").symlink_to(outside)
    with pytest.raises(login.UnsafeTarget):
        login.install(app=app, home=home, apply=True)
    assert not list(outside.iterdir())
