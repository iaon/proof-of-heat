from proof_of_heat import version


def test_get_display_version_uses_explicit_override(monkeypatch):
    monkeypatch.setenv("PROOF_OF_HEAT_DISPLAY_VERSION", "1.2.3")
    monkeypatch.setenv("PROOF_OF_HEAT_VERSION", "9.9.9")
    monkeypatch.setenv("PROOF_OF_HEAT_COMMIT", "abcdef0")

    assert version.get_display_version() == "1.2.3"


def test_get_display_version_appends_commit_for_non_release(monkeypatch):
    monkeypatch.delenv("PROOF_OF_HEAT_DISPLAY_VERSION", raising=False)
    monkeypatch.setenv("PROOF_OF_HEAT_VERSION", "1.2.3")
    monkeypatch.delenv("PROOF_OF_HEAT_COMMIT", raising=False)
    monkeypatch.setattr(version, "_run_git", lambda *args: "b2d19e3" if args == ("rev-parse", "--short", "HEAD") else None)

    assert version.get_display_version() == "1.2.3-b2d19e3"


def test_get_display_version_omits_commit_for_release_tag(monkeypatch):
    monkeypatch.delenv("PROOF_OF_HEAT_DISPLAY_VERSION", raising=False)
    monkeypatch.setenv("PROOF_OF_HEAT_VERSION", "1.2.3")
    monkeypatch.delenv("PROOF_OF_HEAT_COMMIT", raising=False)

    def fake_run_git(*args):
        if args == ("tag", "--points-at", "HEAD"):
            return "v1.2.3"
        if args == ("rev-parse", "--short", "HEAD"):
            return "b2d19e3"
        return None

    monkeypatch.setattr(version, "_run_git", fake_run_git)

    assert version.get_display_version() == "1.2.3"
