from hermes_feishu_card import __version__


def test_package_has_version():
    assert __version__ == "0.1.0"


def test_console_entrypoint_target_exists():
    from hermes_feishu_card.cli import main

    assert main([]) == 0
