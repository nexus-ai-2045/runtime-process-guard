from runtime_process_guard.privacy import command_identity, safe_executable_name
from runtime_process_guard import privacy
import pytest
from pathlib import PureWindowsPath


@pytest.fixture
def display_identity(tmp_path, monkeypatch):
    username = "fixture-" + tmp_path.name[-5:]
    home = privacy.Path("/Users") / username
    monkeypatch.setattr(privacy.Path, "home", lambda: home)
    return username, home


def test_identity_does_not_contain_home_or_secret() -> None:
    command = [
        r"X:\fixture-home\person\bin\node.exe",
        r"X:\fixture-home\person\work\server.mjs",
        "--api-key=top-secret-value",
        "--stdio",
    ]

    identity = command_identity(command, home=r"X:\fixture-home\person")

    assert len(identity) == 64
    assert "person" not in identity
    assert "top-secret-value" not in identity


def test_secret_value_does_not_change_identity() -> None:
    left = command_identity(["node", "server.mjs", "--token=one"])
    right = command_identity(["node", "server.mjs", "--token=two"])
    assert left == right


def test_safe_executable_name_removes_path() -> None:
    assert safe_executable_name(r"X:\fixture-home\person\bin\node.exe") == "node.exe"


def test_display_text_redacts_home_paths_user_and_secret_assignments(display_identity) -> None:
    username, _ = display_identity
    names = [f"other-{username}", f"linux-{username}", f"windows-{username}"]
    mac = privacy.Path("/Users") / names[0] / "project"
    linux = privacy.Path("/home") / names[1] / "project"
    windows = str(PureWindowsPath("C:/") / "Users" / names[2] / "project")
    raw = (f"{username} {mac} {linux} {windows} api_key=example-sensitive-value "
           "token = 'example secret value' password=example-password")
    result = privacy.redact_text(raw)
    for private in (username, *names,
                    "example-sensitive-value", "example secret value", "example-password"):
        assert private not in result
    assert result.count("%USERPROFILE%") == 3
    assert result.count("<redacted>") == 3
    assert privacy.redact_text(result) == result


@pytest.mark.parametrize("raw", [
    '{"api_key": "example-sensitive-value"}',
    "{'OPENAI_API_KEY': 'example-sensitive-value'}",
    "Authorization: Bearer example-sensitive-value",
    "Authorization: Basic example-sensitive-value",
    'token="example-sensitive-value with spaces',
    'token="example-sensitive-value \\"quoted\\" text"',
])
def test_display_text_redacts_structured_and_unterminated_secrets(raw, display_identity):
    result = privacy.redact_text(raw)
    assert "example-sensitive-value" not in result
    assert "with spaces" not in result
    assert "quoted" not in result
    assert "<redacted>" in result
    assert privacy.redact_text(result) == result


def test_display_text_redacts_home_usernames_with_spaces(display_identity):
    username, _ = display_identity
    other = f"Other {username} Person"
    homes = [str(privacy.Path("/Users") / other / "project"),
             str(privacy.Path("/home") / other / "project"),
             str(PureWindowsPath("C:/") / "Users" / other / "project")]
    result = privacy.redact_text(" ".join(homes))
    assert "Other" not in result and "Person" not in result and username not in result
    assert result.count("%USERPROFILE%") == 3


def test_display_text_redacts_username_next_to_japanese(display_identity):
    username, _ = display_identity
    assert privacy.redact_text(f"確認{username}さんの設定") == "確認<user>さんの設定"


def test_display_text_preserves_long_nonsecret_text_and_redacts_later_assignments(display_identity):
    prefix = "x" * 100_000 + " "
    raw = prefix + 'status=ready api_key="example-sensitive-value" mode: local token=example-token'
    assert privacy.redact_text(raw) == prefix + "status=ready api_key=<redacted> mode: local token=<redacted>"
