from proof_of_heat import version


def test_get_display_version_reads_version_file(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(version, "VERSION_FILE", version_file)

    assert version.get_display_version() == "1.2.3"


def test_get_display_version_returns_unknown_when_version_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "VERSION_FILE", tmp_path / "VERSION")

    assert version.get_display_version() == "unknown"
