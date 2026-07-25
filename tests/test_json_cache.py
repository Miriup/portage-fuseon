"""
Tests for the shared two-level JSON cache.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import json
import os
import time

import pytest

from portage_pip_fuse import json_cache
from portage_pip_fuse.json_cache import JSONCache


@pytest.fixture
def cache(tmp_path):
    return JSONCache(tmp_path / 'cache', ttl=3600)


class TestKeys:

    def test_name_is_case_folded(self, cache):
        assert cache.make_key('Requests') == 'requests'

    def test_version_is_namespaced(self, cache):
        assert cache.make_key('requests', '2.0') == 'requests_2.0'

    def test_package_and_version_do_not_collide(self, cache):
        cache.set('requests', {'kind': 'package'})
        cache.set('requests', {'kind': 'release'}, version='2.0')
        assert cache.get('requests') == {'kind': 'package'}
        assert cache.get('requests', version='2.0') == {'kind': 'release'}

    def test_lookup_is_case_insensitive(self, cache):
        cache.set('Requests', {'a': 1})
        assert cache.get('requests') == {'a': 1}
        assert cache.get('REQUESTS') == {'a': 1}


class TestTiers:

    def test_miss_returns_none(self, cache):
        assert cache.get('absent') is None

    def test_round_trips(self, cache):
        cache.set('pkg', {'a': [1, 2, {'b': None}]})
        assert cache.get('pkg') == {'a': [1, 2, {'b': None}]}

    def test_persists_across_instances(self, tmp_path):
        first = JSONCache(tmp_path / 'c', ttl=3600)
        first.set('pkg', {'a': 1})
        second = JSONCache(tmp_path / 'c', ttl=3600)
        assert second.get('pkg') == {'a': 1}

    def test_disk_hit_is_promoted_to_memory(self, tmp_path):
        first = JSONCache(tmp_path / 'c', ttl=3600)
        first.set('pkg', {'a': 1})

        second = JSONCache(tmp_path / 'c', ttl=3600)
        assert second.get('pkg') == {'a': 1}
        # Now served from memory even with the file gone.
        second.path_for('pkg').unlink()
        assert second.get('pkg') == {'a': 1}

    def test_clear_memory_keeps_disk(self, cache):
        cache.set('pkg', {'a': 1})
        cache.clear_memory()
        assert cache.get('pkg') == {'a': 1}


class TestExpiry:

    def test_zero_ttl_expires_immediately(self, tmp_path):
        cache = JSONCache(tmp_path / 'c', ttl=0)
        cache.set('pkg', {'a': 1})
        assert cache.get('pkg') is None

    def test_stale_disk_entry_is_removed(self, tmp_path):
        cache = JSONCache(tmp_path / 'c', ttl=60)
        cache.set('pkg', {'a': 1})
        cache.clear_memory()

        path = cache.path_for('pkg')
        stale = time.time() - 3600
        os.utime(path, (stale, stale))

        assert cache.get('pkg') is None
        assert not path.exists(), 'expired entry should be pruned on read'

    def test_fresh_entry_survives(self, tmp_path):
        cache = JSONCache(tmp_path / 'c', ttl=3600)
        cache.set('pkg', {'a': 1})
        cache.clear_memory()
        assert cache.get('pkg') == {'a': 1}


class TestRobustness:

    def test_corrupt_file_is_discarded_not_raised(self, cache):
        cache.set('pkg', {'a': 1})
        cache.clear_memory()
        cache.path_for('pkg').write_text('{ this is not json')

        assert cache.get('pkg') is None
        assert not cache.path_for('pkg').exists()

    def test_writes_leave_no_temp_file(self, cache):
        cache.set('pkg', {'a': 1})
        leftovers = list(cache.cache_dir.rglob('*.tmp'))
        assert leftovers == []

    def test_failed_disk_write_does_not_raise(self, cache, monkeypatch):
        """A failing disk write must degrade, not crash the FUSE process.

        The failure is injected rather than created with chmod, because chmod is
        no-op for a root test runner and would make this pass vacuously.
        """
        real_open = type(cache.cache_dir).open

        def exploding_open(self, *args, **kwargs):
            if 'w' in (args[0] if args else kwargs.get('mode', 'r')):
                raise OSError('disk full')
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(type(cache.cache_dir), 'open', exploding_open)
        cache.set('pkg', {'a': 1})

        # The memory tier still answers even though nothing reached disk.
        assert cache.get('pkg') == {'a': 1}

        monkeypatch.undo()
        assert not cache.path_for('pkg').exists()
        assert list(cache.cache_dir.rglob('*.tmp')) == [], 'temp file left behind'

    def test_invalidate_clears_both_tiers(self, cache):
        cache.set('pkg', {'a': 1})
        cache.invalidate('pkg')
        assert cache.get('pkg') is None
        assert not cache.path_for('pkg').exists()


class TestSharding:

    def test_entries_are_sharded(self, cache):
        cache.set('requests', {'a': 1})
        path = cache.path_for('requests')
        assert path.parent.name == 're'
        assert path.parent.parent == cache.cache_dir

    def test_short_keys_are_padded(self, cache):
        cache.set('a', {'x': 1})
        assert cache.get('a') == {'x': 1}

    def test_scoped_names_stay_one_level_deep(self, cache):
        """npm-style scoped names contain '/', which must not create subdirs."""
        cache.set('@vue/cli-service', {'a': 1})
        path = cache.path_for(cache.make_key('@vue/cli-service'))
        assert path.parent.parent == cache.cache_dir
        assert '/' not in path.name
        assert cache.get('@vue/cli-service') == {'a': 1}


class TestListCached:

    def test_lists_packages(self, cache):
        for name in ['requests', 'flask', 'django']:
            cache.set(name, {'n': name})
        assert cache.list_cached() == ['django', 'flask', 'requests']

    def test_excludes_version_entries(self, cache):
        cache.set('requests', {'a': 1})
        cache.set('requests', {'a': 2}, version='2.0')
        assert cache.list_cached() == ['requests']

    def test_empty_when_absent(self, tmp_path):
        assert JSONCache(tmp_path / 'missing', ttl=60).list_cached() == []


class TestRubyGemsProviderDelegates:
    """The RubyGems provider must resolve through the shared cache."""

    def test_provider_uses_json_cache(self, tmp_path):
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.rubygems.plugin import RubyGemsMetadataProvider

        provider = RubyGemsMetadataProvider(cache_dir=str(tmp_path), cache_ttl=3600)
        assert isinstance(provider._cache, JSONCache)

        provider._set_cached('rails', {'name': 'rails'})
        assert provider._get_cached('rails') == {'name': 'rails'}
        assert 'rails' in provider.list_packages()

    def test_cache_key_scheme_is_preserved(self, tmp_path):
        """Keys must keep the shape the provider used before the refactor."""
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.rubygems.plugin import RubyGemsMetadataProvider

        provider = RubyGemsMetadataProvider(cache_dir=str(tmp_path), cache_ttl=3600)
        assert provider._get_cache_key('Rails') == 'rails'
        assert provider._get_cache_key('rails', '7.0.0') == 'rails_7.0.0'

    def test_written_file_is_valid_json(self, tmp_path):
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.rubygems.plugin import RubyGemsMetadataProvider

        provider = RubyGemsMetadataProvider(cache_dir=str(tmp_path), cache_ttl=3600)
        provider._set_cached('rails', {'name': 'rails'})
        path = provider._get_cache_path('rails')
        with path.open() as handle:
            assert json.load(handle) == {'name': 'rails'}


def test_doctests():
    results = doctest.testmod(json_cache, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
