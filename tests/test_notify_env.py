import os

from halka_arz_advisor.notify.env import load_dotenv_if_present


def test_loads_key_value_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MY_TEST_VAR=hello world\n# a comment\n\nOTHER=1\n")

    load_dotenv_if_present(env_file)

    assert os.environ["MY_TEST_VAR"] == "hello world"
    assert os.environ["OTHER"] == "1"


def test_does_not_override_existing_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEST_VAR", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("MY_TEST_VAR=from-file\n")

    load_dotenv_if_present(env_file)

    assert os.environ["MY_TEST_VAR"] == "already-set"


def test_missing_file_is_a_no_op(tmp_path):
    load_dotenv_if_present(tmp_path / "does-not-exist.env")  # must not raise


def test_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("QUOTED_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('QUOTED_VAR="quoted value"\n')

    load_dotenv_if_present(env_file)

    assert os.environ["QUOTED_VAR"] == "quoted value"
