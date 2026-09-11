"""Explorer-key resolution: the one config value the dossier cannot degrade past.

The explorer is its own failure unit (design D8), so a missing key is not an
error -- it is six fields carrying a `failed` marker and a `partial` section.
That deliberate softness is why the reading is pinned here: absent, empty and
whitespace-only must all resolve the same way, because none of them produces a
loud failure at runtime to reveal a mistake.
"""
import pytest

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


class TestAlchemyMonthlyCuCeiling:
    def test_the_default_stays_inside_the_jobs_share_of_the_provider_cap(self) -> None:
        # The only number bounding metered spend, pinned to its derivation
        # rather than to itself: raising it means re-deriving the headroom the
        # NETNET monitor keeps on the same key.
        usd = config.DEFAULT_ALCHEMY_MONTHLY_CU_CEILING / 1_000_000 * config.ALCHEMY_USD_PER_MILLION_CU
        assert 0 < usd <= config.ALCHEMY_JOB_SHARE_OF_CAP_USD
        assert config.ALCHEMY_JOB_SHARE_OF_CAP_USD < 1.0  # the operator's $1 cap

    def test_unset_is_the_default(self, monkeypatch) -> None:
        from src.service.onchain import config

        monkeypatch.delenv('ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING', raising=False)
        assert config.get_alchemy_monthly_cu_ceiling() == config.DEFAULT_ALCHEMY_MONTHLY_CU_CEILING

    def test_a_positive_integer_is_read(self, monkeypatch) -> None:
        from src.service.onchain import config

        monkeypatch.setenv('ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING', ' 4200 ')
        assert config.get_alchemy_monthly_cu_ceiling() == 4200

    @pytest.mark.parametrize('value', ['abc', '0', '-5', '1.5', '\u00b2'])
    def test_anything_else_is_rejected_by_name(self, monkeypatch, value) -> None:
        # `0` would refuse every call while looking configured; `abc` would
        # fail the run as a bare ValueError with no setting named.
        from src.service.onchain import config

        monkeypatch.setenv('ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING', value)
        with pytest.raises(ValueError, match='ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING'):
            config.get_alchemy_monthly_cu_ceiling()
