import pytest

from hermes_feishu_card import profile_sources


class StringSubclass(str):
    pass


def resolve(**kwargs):
    return profile_sources.resolve_authenticated_profile_identity(**kwargs)


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        ({"explicit_env_profile": "work"}, ("work", "env")),
        (
            {"authenticated_session_profile": "sales"},
            ("sales", "locals"),
        ),
        (
            {
                "hermes_home_profile": "thinking",
                "hermes_home_membership_verified": True,
            },
            ("thinking", "hermes_home"),
        ),
        ({"no_named_profile": True}, ("default", "fallback_default")),
    ),
)
def test_authenticated_profile_resolver_maps_four_exact_sources(facts, expected):
    assert resolve(**facts) == expected


def test_authenticated_profile_resolver_uses_precedence_only_for_agreeing_facts():
    assert resolve(
        explicit_env_profile="work",
        authenticated_session_profile="work",
        hermes_home_profile="work",
        hermes_home_membership_verified=True,
    ) == ("work", "env")


@pytest.mark.parametrize(
    "facts",
    (
        {},
        {"explicit_env_profile": ""},
        {"explicit_env_profile": " bad "},
        {"explicit_env_profile": "bad/profile"},
        {"explicit_env_profile": StringSubclass("work")},
        {"authenticated_session_profile": ""},
        {"authenticated_session_profile": StringSubclass("work")},
        {"hermes_home_profile": "work"},
        {
            "hermes_home_profile": "work",
            "hermes_home_membership_verified": 1,
        },
        {"hermes_home_membership_verified": True},
        {"no_named_profile": 1},
        {
            "explicit_env_profile": "work",
            "authenticated_session_profile": "other",
        },
        {
            "authenticated_session_profile": "work",
            "hermes_home_profile": "other",
            "hermes_home_membership_verified": True,
        },
        {"explicit_env_profile": "work", "no_named_profile": True},
    ),
)
def test_authenticated_profile_resolver_rejects_invalid_unverified_and_conflicting_facts(
    facts,
):
    assert resolve(**facts) is None


@pytest.mark.parametrize(
    ("profile_id", "source", "home_verified", "expected"),
    (
        ("work", "env", False, ("work", "env")),
        ("work", "locals", False, ("work", "locals")),
        ("work", "hermes_home", True, ("work", "hermes_home")),
        ("default", "fallback_default", False, ("default", "fallback_default")),
        ("work", "hermes_home", False, None),
        ("work", "sanitized_env", False, None),
        (StringSubclass("work"), "env", False, None),
        ("work", StringSubclass("env"), False, None),
        ("default", "env", False, ("default", "env")),
        ("work", "fallback_default", False, None),
    ),
)
def test_trusted_profile_identity_validator_requires_exact_pair_and_home_evidence(
    profile_id, source, home_verified, expected
):
    assert profile_sources.validate_trusted_profile_identity(
        profile_id,
        source,
        hermes_home_membership_verified=home_verified,
    ) == expected


def test_profile_source_sets_are_authoritative_and_disjoint():
    assert profile_sources.TRUSTED_PROFILE_SOURCES == frozenset(
        {"env", "locals", "hermes_home", "fallback_default"}
    )
    assert profile_sources.SANITIZED_PROFILE_SOURCES == frozenset(
        {"sanitized_env", "sanitized_locals", "sanitized_hermes_home"}
    )
    assert profile_sources.TRUSTED_PROFILE_SOURCES.isdisjoint(
        profile_sources.SANITIZED_PROFILE_SOURCES
    )
    assert profile_sources.PROFILE_SOURCES == (
        profile_sources.TRUSTED_PROFILE_SOURCES
        | profile_sources.SANITIZED_PROFILE_SOURCES
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("/home/user/.hermes/profiles/sales", "sales"),
        (r"C:\\Users\\user\\AppData\\Local\\hermes\\profiles\\thinking", "thinking"),
        ("/tmp/profiles/not-hermes", None),
        ("/home/user/.hermes/profiles/sales/extra", None),
    ),
)
def test_shared_legacy_home_parser_preserves_existing_path_contract(path, expected):
    assert profile_sources.profile_from_hermes_home_path(path) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("/opt/data", "default"),
        ("/opt/data/profiles/content", "content"),
        ("/opt/data/profiles/finance", "finance"),
        ("/opt/data/profiles", None),
        ("/opt/data/profiles/a/b", None),
        ("/opt/data/profiles/.hidden", None),
        ("/opt/data/profiles/Bad_Name", None),
        ("/opt/data/profiles/-x", None),
        ("/other/root", None),
        ("/opt/data/profiles/content/extra", None),
    ),
)
def test_root_bound_parser_matches_only_root_or_exact_profiles_child(path, expected):
    assert profile_sources.profile_from_hermes_home_path(
        path, hermes_root="/opt/data"
    ) == expected


def test_root_bound_parser_without_root_keeps_legacy_contract():
    # The optional keyword must not change the historical behavior.
    assert profile_sources.profile_from_hermes_home_path(
        "/home/u/.hermes/profiles/sales"
    ) == "sales"
    assert profile_sources.profile_from_hermes_home_path(
        "/opt/data/profiles/content"
    ) is None


def test_root_bound_parser_has_no_legacy_fallthrough():
    # With a root given, a legacy .hermes path outside that root is refused
    # even though the legacy rule alone would have accepted it.
    assert profile_sources.profile_from_hermes_home_path(
        "/home/u/.hermes/profiles/sales", hermes_root="/opt/data"
    ) is None


@pytest.mark.parametrize(
    ("value", "source", "expected"),
    (
        ("work", "env", ("work", "env")),
        ("bad:profile/path", "env", ("default", "sanitized_env")),
        ("bad:profile", "locals", ("default", "sanitized_locals")),
        ("bad:profile", "hermes_home", ("default", "sanitized_hermes_home")),
    ),
)
def test_shared_legacy_sanitizer_preserves_existing_labels(value, source, expected):
    assert profile_sources.legacy_profile_identity(value, source) == expected


@pytest.mark.parametrize(("root", "home", "expected"), [
    (r"C:\data", r"C:\data\profiles\work", "work"),
    (r"C:\data", "c:/DATA/Profiles/work", "work"),
    (r"C:\data", r"D:\data\profiles\work", None),
    (r"\\server\share", r"\\server\share\profiles\work", "work"),
    (r"\\server\share", r"\\other\share\profiles\work", None),
    ("/opt/data", r"C:\opt\data\profiles\work", None),
    ("/opt/data", "/opt/data/profiles/ work ", None),
])
def test_root_bound_paths_preserve_platform_grammar(root, home, expected):
    assert profile_sources.profile_from_hermes_home_path(home, hermes_root=root) == expected
