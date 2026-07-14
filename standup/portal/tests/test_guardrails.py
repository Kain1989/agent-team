"""Content guardrails: input validation + output secret-scanning (v0.2)."""
import importlib
import json


def _gr(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_ROOT", str(tmp_path))
    monkeypatch.delenv("STANDUP_MAX_PROMPT_CHARS", raising=False)
    (tmp_path / "control").mkdir(exist_ok=True)
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.guardrails as guardrails
    importlib.reload(guardrails)
    return guardrails, paths


def test_check_input_length_and_deny_pattern(tmp_path, monkeypatch):
    gr, paths = _gr(tmp_path, monkeypatch)
    assert gr.check_input("do a normal task")["ok"] is True
    monkeypatch.setenv("STANDUP_MAX_PROMPT_CHARS", "10")
    importlib.reload(gr)
    assert gr.check_input("x" * 50)["ok"] is False
    # configured input deny pattern
    monkeypatch.delenv("STANDUP_MAX_PROMPT_CHARS", raising=False)
    importlib.reload(gr)
    (paths.control_dir() / "guardrails.json").write_text(json.dumps({"deny_input_patterns": ["rm -rf /"]}))
    assert gr.check_input("please rm -rf / everything")["ok"] is False
    assert gr.check_input("please be careful")["ok"] is True


def test_check_output_flags_secrets(tmp_path, monkeypatch):
    gr, _ = _gr(tmp_path, monkeypatch)
    clean = "+def truncate(text, n):\n+    return text[:n]\n"
    assert gr.check_output(clean)["ok"] is True
    # an AWS access key id in the diff -> hard block
    leak = clean + "+AWS_KEY = 'AKIA" + "ABCDEFGHIJKLMNOP'\n"
    out = gr.check_output(leak)
    assert out["ok"] is False and "aws_access_key_id" in out["findings"]
    # a private key block
    pk = "+-----BEGIN OPENSSH PRIVATE KEY-----\n+xxxx\n"
    assert gr.check_output(pk)["ok"] is False


def test_check_output_custom_deny_pattern(tmp_path, monkeypatch):
    gr, paths = _gr(tmp_path, monkeypatch)
    (paths.control_dir() / "guardrails.json").write_text(json.dumps({"deny_output_patterns": ["INTERNAL-ONLY"]}))
    importlib.reload(gr)
    out = gr.check_output("+# INTERNAL-ONLY do not ship\n")
    assert out["ok"] is False and any("INTERNAL-ONLY" in f for f in out["findings"])
