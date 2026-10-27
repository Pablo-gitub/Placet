"""Which model configuration Antigravity's last user turn leaves: the most recent step wins, whichever field it uses."""
import pytest

from bridge.channels import AntigravityChannel, ChannelError


def step(**user_input):
    return {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"items": [{"text": "x"}], **user_input}}


def config(*steps):
    return AntigravityChannel._config({"steps": list(steps)})


def test_a_newer_last_user_config_beats_an_older_user_config():
    assert config(step(userConfig={"model": "A"}), step(lastUserConfig={"model": "B"})) == {"model": "B"}


def test_a_newer_user_config_beats_an_older_last_user_config():
    assert config(step(lastUserConfig={"model": "A"}), step(userConfig={"model": "B"})) == {"model": "B"}


def test_within_one_step_the_user_config_comes_first():
    assert config(step(userConfig={"model": "A"}, lastUserConfig={"model": "B"})) == {"model": "A"}


def test_steps_without_a_configuration_are_skipped_back_to_the_latest_that_has_one():
    other = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE"}
    assert config(step(userConfig={"model": "A"}), other, step(), other) == {"model": "A"}


def test_no_configuration_anywhere_is_still_an_error():
    with pytest.raises(ChannelError, match="model configuration"):
        config(step(), {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE"})
