"""filedrawer configure: the setup questions, the config they produce, and run.sh refusing to start without them."""
import json
import os
import subprocess

import yaml

from filedrawer import cli
from filedrawer import configure as CF
from filedrawer.config import load_config
from filedrawer.scaffold import init_study


def test_questions_are_data_and_defaults_are_valid(capsys):
    assert cli.main(["configure", "--questions"]) == 0
    q = json.loads(capsys.readouterr().out)
    ids = [x["id"] for x in q["questions"]]
    assert ids == ["where", "models", "local_model", "outside_review", "extensions", "pii", "literature"]
    assert CF.validate(dict(CF.DEFAULTS)) == []
    assert "advanced" not in json.dumps(q).lower()               # the outside reviews offered are coarse, refine, openreview


def test_yes_writes_recommended_setup(tmp_path, capsys):
    assert cli.main(["configure", str(tmp_path), "--yes"]) == 0
    cfg = yaml.safe_load((tmp_path / CF.SETUP_FILE).read_text())
    assert cfg["provider"] == "openrouter"
    assert cfg["models"] == {"strong": "anthropic/claude-sonnet-5.5", "fast": "anthropic/claude-haiku-5.5"}
    assert cfg["pii"] == {"model": False} and cfg["litreview"] == {"enabled": True} and cfg["setup"]["extensions"] == "yes"
    assert cli.main(["configure", str(tmp_path), "--show"]) == 0
    assert "Light Pass" in capsys.readouterr().out


def test_local_and_custom_choices(tmp_path):
    assert cli.main(["configure", str(tmp_path), "--yes", "--where", "local", "--local-model", "qwen/qwen3.8-27b",
                     "--pii", "rules+model", "--literature", "off", "--outside-review", "coarse"]) == 0
    cfg = yaml.safe_load((tmp_path / CF.SETUP_FILE).read_text())
    assert cfg["provider"] == "local" and cfg["local"]["models"]["fast"] == "qwen/qwen3.8-27b"
    assert cfg["pii"] == {"model": True} and cfg["litreview"] == {"enabled": False}
    assert cli.main(["configure", str(tmp_path), "--yes", "--models", "custom"]) == 2          # custom needs the slugs
    assert cli.main(["configure", str(tmp_path), "--yes", "--models", "custom",
                     "--strong-model", "x/strong", "--fast-model", "x/fast"]) == 0
    assert yaml.safe_load((tmp_path / CF.SETUP_FILE).read_text())["models"] == {"strong": "x/strong", "fast": "x/fast"}


def test_pipeline_config_reads_the_setup_in_the_study_folder(tmp_path, monkeypatch):
    assert cli.main(["configure", str(tmp_path), "--yes", "--models", "sonnet"]) == 0
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg["models"]["fast"] == "anthropic/claude-sonnet-5.5" and cfg["setup"]["models"] == "sonnet"


def test_run_sh_refuses_without_setup(tmp_path):
    init_study(tmp_path, slug="s", title="T", authors="A", repo_url="https://github.com/a/s")
    env = dict(os.environ, OPENROUTER_API_KEY="x")
    r = subprocess.run(["bash", str(tmp_path / "run.sh")], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "filedrawer configure" in r.stderr
