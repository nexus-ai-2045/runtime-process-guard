from runtime_process_guard.privacy import command_identity, safe_executable_name


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
