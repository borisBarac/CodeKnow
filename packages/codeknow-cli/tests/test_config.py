from __future__ import annotations

import json

from codeknow_cli.config import UserConfig, load_config, save_config


def test_missing_file_returns_defaults(tmp_path):
    path = tmp_path / "config.jsonl"
    cfg = load_config(path)
    assert cfg == UserConfig()
    assert cfg.mode == "docker"
    assert cfg.remote_url == ""
    assert cfg.host == "localhost"
    assert cfg.port == 8080


def test_malformed_json_returns_defaults(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text("{not valid json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == UserConfig()


def test_non_dict_json_returns_defaults(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text("[]", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == UserConfig()


def test_bogus_mode_falls_back_to_docker(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text(json.dumps({"mode": "bogus"}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.mode == "docker"


def test_remote_mode_with_defaults(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text(json.dumps({"mode": "remote"}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.mode == "remote"
    assert cfg.remote_url == ""
    assert cfg.host == "localhost"
    assert cfg.port == 8080


def test_daemon_mode_coerces_port_and_host(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text(
        json.dumps({"mode": "daemon", "host": "10.0.0.1", "port": "7000"}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.mode == "daemon"
    assert cfg.host == "10.0.0.1"
    assert cfg.port == 7000


def test_daemon_mode_invalid_port_falls_back(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text(
        json.dumps({"mode": "daemon", "port": "not-a-number"}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.port == 8080


def test_non_string_remote_url_falls_back(tmp_path):
    path = tmp_path / "config.jsonl"
    path.write_text(json.dumps({"remote_url": 123}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.remote_url == ""


def test_round_trip(tmp_path):
    path = tmp_path / "config.jsonl"
    original = UserConfig(mode="remote", remote_url="https://api.example.com")
    save_config(original, path)
    loaded = load_config(path)
    assert loaded == original


def test_save_config_writes_single_line(tmp_path):
    path = tmp_path / "config.jsonl"
    save_config(UserConfig(mode="daemon"), path)
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 1


def test_save_config_creates_parent_dir(tmp_path):
    path = tmp_path / "subdir" / "nested" / "config.jsonl"
    save_config(UserConfig(mode="remote"), path)
    assert path.exists()
    cfg = load_config(path)
    assert cfg.mode == "remote"
