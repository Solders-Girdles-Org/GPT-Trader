from gpt_trader.config import path_registry


def test_runtime_artifact_policy_constants_are_grouped_by_ownership() -> None:
    assert path_registry.USER_SECRETS_DIR == path_registry.USER_CONFIG_DIR / "secrets"
    assert path_registry.USER_CONFIG_DIR.name == ".gpt_trader"
