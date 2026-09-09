"""Explorer-key resolution: the one config value the dossier cannot degrade past.

The explorer is its own failure unit (design D8), so a missing key is not an
error -- it is six fields carrying a `failed` marker and a `partial` section.
That deliberate softness is why the reading is pinned here: absent, empty and
whitespace-only must all resolve the same way, because none of them produces a
loud failure at runtime to reveal a mistake.
"""
from src.service.onchain import config


class TestBlockscoutApiKey:
    """Absent, empty, whitespace-only and padded must all resolve like a key or like none.

    The explorer is a degradable failure unit (design D8), so an unkeyed run
    must still start and degrade to `partial` rather than refuse to build.
    """

    def test_the_key_is_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv('BLOCKSCOUT_API_KEY', 'proapi_secret')
        assert config.get_blockscout_api_key() == 'proapi_secret'

    def test_an_unset_key_is_none_rather_than_raising(self, monkeypatch) -> None:
        monkeypatch.delenv('BLOCKSCOUT_API_KEY', raising=False)
        assert config.get_blockscout_api_key() is None

    def test_a_whitespace_only_key_reads_as_absent(self, monkeypatch) -> None:
        # A value pasted into a .env or an EnvironmentFile picks up a trailing
        # space or newline easily, and an unstripped key reproduces the exact
        # 403 outage the key exists to fix -- undiagnosably, since nothing may
        # print the key to show the whitespace.
        monkeypatch.setenv('BLOCKSCOUT_API_KEY', '   ')
        assert config.get_blockscout_api_key() is None

    def test_surrounding_whitespace_is_stripped_from_a_real_key(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv('BLOCKSCOUT_API_KEY', '  proapi_secret\n')
        assert config.get_blockscout_api_key() == 'proapi_secret'

    def test_an_empty_key_reads_as_absent(self, monkeypatch) -> None:
        # A var set to '' is a key someone meant to fill in. Passing '' on to
        # the client would send an empty x-api-key header, which the explorer
        # rejects less legibly than sending none at all.
        monkeypatch.setenv('BLOCKSCOUT_API_KEY', '')
        assert config.get_blockscout_api_key() is None
