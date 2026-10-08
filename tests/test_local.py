"""Run locally and push to GitHub: the local model provider, the --local flags, publish, the local badge."""
import json
import shutil
import subprocess
import types

import pytest


def test_local_config_replaces_the_model_names():
    from filedrawer.config import load_config
    cfg = load_config(overrides={"provider": "local"})
    assert cfg["models"] == cfg["local"]["models"] and cfg["models"]["strong"].startswith("qwen")
    cfg = load_config(overrides={"provider": "local", "local": {"models": {"strong": "qwen3:32b", "fast": "qwen3:32b"}}})
    assert set(cfg["models"].values()) == {"qwen3:32b"}


def test_local_flags():
    from filedrawer.cli import _overrides
    a = types.SimpleNamespace(provider=None, local=True, local_model="qwen3:32b", local_url="http://localhost:1234/v1")
    assert _overrides(a) == {"provider": "local", "local": {"models": {"strong": "qwen3:32b", "fast": "qwen3:32b"},
                                                            "base_url": "http://localhost:1234/v1"}}
    assert _overrides(types.SimpleNamespace(provider="mock", local=False, local_model=None, local_url=None)) == {"provider": "mock"}


def test_local_provider_talks_openai_and_names_missing_models(tmp_path):
    httpx = pytest.importorskip("httpx")
    from filedrawer.llm.client import LocalProvider
    seen = {}

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        body = json.loads(req.content)
        seen.update(body)
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": "{\"ok\": true}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    p = LocalProvider(base_url="http://localhost:11434/v1", log_path=tmp_path / "log.jsonl")
    p.client = httpx.Client(transport=httpx.MockTransport(handler))
    p.check_models(["qwen3:8b"])
    with pytest.raises(SystemExit) as e:
        p.check_models(["qwen3:32b"])
    assert "ollama pull qwen3:32b" in str(e.value)
    r = p.chat(agent="writer", messages=[{"role": "user", "content": "hi"}], model="qwen3:8b", max_tokens=50)
    assert r.content == '{"ok": true}' and r.usage == {"in": 12, "out": 3, "cost": 0.0}
    assert "provider" not in seen and "reasoning" not in seen          # no OpenRouter-only fields go to a local server
    assert seen["reasoning_effort"] == "low"                           # reasoning models must not spend the budget thinking
    assert p.name == "local"


def test_local_provider_explains_a_missing_server():
    pytest.importorskip("httpx")
    from filedrawer.llm.client import LocalProvider
    p = LocalProvider(base_url="http://127.0.0.1:9/v1", timeout_s=2)
    with pytest.raises(SystemExit) as e:
        p.check_models(["qwen3:8b"])
    assert "ollama serve" in str(e.value)


def test_local_badge():
    from filedrawer.badges import badges_for
    b = {x["name"]: x for x in badges_for({"provenance": {"mode": "fully_agentic", "provider": "local", "cost_usd": 0.0}})}
    assert b["cost"]["label"] == "models" and b["cost"]["value"] == "local"


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "my-study"
    for rel, text in {"study.json": json.dumps({"slug": "my-study", "title": "My study"}), "report.md": "# My study\n\nShort.\n",
                      "results/H1.csv": "analysis_id,estimate\nH1,0.5\n", "data/clean.csv": "row,treat,y\n1,1,0.3\n",
                      "inputs/export.csv": "id,email\n1,a@b.org\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _gitenv(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@b.c", "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@b.c"}.items():
        monkeypatch.setenv(k, v)


def test_publish_blocks_identifiers_and_commits_nothing(package, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    from filedrawer.publish import publish
    (package / "data" / "clean.csv").write_text("email,y\na@b.org,1\n")
    out = []
    code, url = publish(package, say=out.append)
    assert code == 1 and url is None and any("BLOCK data/clean.csv" in o for o in out)
    assert not (package / ".git").exists()


def test_publish_commits_and_pushes_without_raw_inputs(package, tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    _gitenv(monkeypatch)
    from filedrawer import publish as P
    from filedrawer import submit as S
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=package, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=package, check=True)
    monkeypatch.setattr(S, "github_url", lambda p: ("https://github.com/someone/my-study", None))
    out = []
    code, url = P.publish(package, say=out.append)
    assert code == 0 and url == "https://github.com/someone/my-study"
    tracked = subprocess.run(["git", "ls-files"], cwd=package, capture_output=True, text=True, check=True).stdout.split()
    assert "report.md" in tracked and "data/clean.csv" in tracked and "inputs/export.csv" not in tracked
    pushed = subprocess.run(["git", "log", "--oneline", "main"], cwd=bare, capture_output=True, text=True, check=True).stdout
    assert "Study package" in pushed and any("private" in o for o in out)


def test_optional_agent_survives_a_timeout_required_agent_stops():
    from filedrawer.llm.agent import Agent

    class Dead:
        name = "local"

        def chat(self, **kw):
            raise RuntimeError("local request failed after retries: timed out")

    res = Agent("exploratory", Dead(), "qwen3:8b", "sys", None, max_turns=1, max_tokens=10).run("task")
    assert res.stopped == "error" and "timed out" in res.error
    with pytest.raises(RuntimeError):
        Agent("writer", Dead(), "qwen3:8b", "sys", None, max_turns=1, max_tokens=10).run("task")


def test_inverted_exclusion_is_caught_with_counts(tmp_path):
    from filedrawer.agents.papreader import exclusion_errors
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "raw_tidy.csv").write_text("Finished,y\n" + "1,0.1\n" * 30 + "0,0.2\n" * 2)
    pap = {"implemented": {"sample_exclusions": ["Finished != 1"], "hypotheses": [{"id": "H1", "exclusions": ["Finished == 1"]}]}}
    errs = exclusion_errors(pap, tmp_path)
    assert len(errs) == 1 and "keeps 2 of 32 rows" in errs[0] and "rows to KEEP" in errs[0]
