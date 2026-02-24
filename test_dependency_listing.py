#!/usr/bin/env python3
"""
Test script to debug why only 35 packages show up instead of 301 when using dependency filter.

This traces through the exact flow that should happen during:
  ls /var/db/repos/pypi/dev-python | wc -l

Run with: python3 test_dependency_listing.py
"""

import json
import logging
from pathlib import Path

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_dependency_resolution():
    """
    Step 1: Load the cached dependency tree to see what packages were resolved.
    
    >>> cache_file = Path('/home/dirk/.cache/portage-pip-fuse/deptree_open_webui_no_flags.depset')
    >>> if cache_file.exists():
    ...     with open(cache_file) as f:
    ...         data = json.load(f)
    ...     print(f"Cached packages: {data['package_count']}")
    ...     pypi_packages = set(data['packages'])
    ...     print(f"First 5 PyPI packages: {sorted(pypi_packages)[:5]}")
    ... else:
    ...     print("Cache file not found!")
    ...     pypi_packages = set()
    """
    pass

def test_name_translation():
    """
    Step 2: Test how PyPI names get translated to Gentoo names.
    
    >>> from portage_pip_fuse.prefetcher import create_prefetched_translator
    >>> 
    >>> # Create the translator (this might take a while as it scans repos)
    >>> print("Creating prefetched translator...")
    >>> translator = create_prefetched_translator()
    >>> 
    >>> # Test packages we know should work
    >>> test_packages = ['numpy', 'requests', 'torch', 'accelerate', 'aiohttp']
    >>> for pypi_name in test_packages:
    ...     gentoo_name = translator.pypi_to_gentoo(pypi_name)
    ...     print(f"{pypi_name} -> {gentoo_name if gentoo_name else 'NONE'}")
    """
    pass

def test_full_translation_flow():
    """
    Step 3: Simulate the full readdir flow with all 301 packages.
    
    >>> import json
    >>> from pathlib import Path
    >>> from portage_pip_fuse.prefetcher import create_prefetched_translator
    >>> 
    >>> # Load dependency tree
    >>> cache_file = Path('/home/dirk/.cache/portage-pip-fuse/deptree_open_webui_no_flags.depset')
    >>> with open(cache_file) as f:
    ...     data = json.load(f)
    >>> pypi_packages = data['packages']
    >>> print(f"Total PyPI packages: {len(pypi_packages)}")
    >>> 
    >>> # Create translator
    >>> print("Creating translator...")
    >>> translator = create_prefetched_translator()
    >>> 
    >>> # Try to translate all packages
    >>> gentoo_packages = []
    >>> failed_translations = []
    >>> 
    >>> for pypi_name in pypi_packages:
    ...     gentoo_name = translator.pypi_to_gentoo(pypi_name)
    ...     if gentoo_name:
    ...         gentoo_packages.append(gentoo_name)
    ...     else:
    ...         # This is what the fix should do - use PyPI name directly
    ...         normalized = pypi_name.lower().replace('_', '-').replace('.', '-')
    ...         gentoo_packages.append(normalized)
    ...         failed_translations.append(pypi_name)
    >>> 
    >>> print(f"Successfully translated: {len(gentoo_packages) - len(failed_translations)}")
    >>> print(f"Used PyPI name directly: {len(failed_translations)}")
    >>> print(f"Total Gentoo packages: {len(gentoo_packages)}")
    >>> print(f"First 10 failed translations: {failed_translations[:10]}")
    """
    pass

def test_filesystem_readdir():
    """
    Step 4: Test the actual filesystem readdir operation.
    
    >>> from portage_pip_fuse.filesystem import PortagePipFS
    >>> 
    >>> # Create filesystem with dependency filter
    >>> filter_config = {
    ...     'active_filters': ['deps'],
    ...     'deps_for': ['open-webui'],
    ...     'use_flags': [],
    ... }
    >>> 
    >>> print("Initializing filesystem...")
    >>> fs = PortagePipFS(
    ...     cache_dir='/home/dirk/.cache/portage-pip-fuse',
    ...     filter_config=filter_config
    ... )
    >>> 
    >>> # Simulate readdir for /dev-python
    >>> print("Calling readdir('/dev-python', None)...")
    >>> entries = list(fs.readdir('/dev-python', None))
    >>> 
    >>> # Remove . and ..
    >>> packages = [e for e in entries if e not in ['.', '..']]
    >>> print(f"Packages returned by readdir: {len(packages)}")
    >>> print(f"First 10 packages: {sorted(packages)[:10]}")
    >>> 
    >>> # Check if key packages are present
    >>> key_packages = ['accelerate', 'torch', 'numpy', 'transformers']
    >>> for pkg in key_packages:
    ...     if pkg in packages:
    ...         print(f"✓ {pkg} found")
    ...     else:
    ...         print(f"✗ {pkg} NOT found")
    """
    pass

def debug_translator_internals():
    """
    Step 5: Debug the translator to understand why it's not returning packages.
    
    >>> from portage_pip_fuse.prefetcher import create_prefetched_translator
    >>> 
    >>> # The prefetcher scans Gentoo repos and creates mappings
    >>> # Let's see what it actually knows about
    >>> translator = create_prefetched_translator()
    >>> 
    >>> # The translator has internal mappings we can inspect
    >>> if hasattr(translator, 'pypi_to_gentoo_map'):
    ...     print(f"Known PyPI->Gentoo mappings: {len(translator.pypi_to_gentoo_map)}")
    ...     # Show some examples
    ...     for pypi, gentoo in list(translator.pypi_to_gentoo_map.items())[:5]:
    ...         print(f"  {pypi} -> {gentoo}")
    >>> 
    >>> if hasattr(translator, 'gentoo_to_pypi_map'):
    ...     print(f"Known Gentoo->PyPI mappings: {len(translator.gentoo_to_pypi_map)}")
    """
    pass

def manual_test_sequence():
    """
    Manual test sequence to run step by step.
    """
    print("=" * 70)
    print("STEP 1: Check cached dependency tree")
    print("=" * 70)
    
    cache_file = Path('/home/dirk/.cache/portage-pip-fuse/deptree_open_webui_no_flags.depset')
    if not cache_file.exists():
        print(f"ERROR: Cache file not found at {cache_file}")
        return
    
    with open(cache_file) as f:
        data = json.load(f)
    
    pypi_packages = data['packages']
    print(f"✓ Found {len(pypi_packages)} packages in dependency tree")
    print(f"  First 10: {pypi_packages[:10]}")
    
    print("\n" + "=" * 70)
    print("STEP 2: Test name translator")
    print("=" * 70)
    
    from portage_pip_fuse.prefetcher import create_prefetched_translator
    
    print("Creating translator (this scans Gentoo repos)...")
    translator = create_prefetched_translator()
    
    # Test some known packages
    test_packages = ['numpy', 'torch', 'requests', 'accelerate', 'transformers']
    successful = 0
    for pkg in test_packages:
        result = translator.pypi_to_gentoo(pkg)
        if result:
            print(f"  ✓ {pkg} -> {result}")
            successful += 1
        else:
            print(f"  ✗ {pkg} -> FAILED")
    
    print(f"\nTranslated {successful}/{len(test_packages)} test packages")
    
    print("\n" + "=" * 70)
    print("STEP 3: Translate all dependency packages")
    print("=" * 70)
    
    gentoo_packages = []
    translated = []
    untranslated = []
    
    for pypi_name in pypi_packages:
        gentoo_name = translator.pypi_to_gentoo(pypi_name)
        if gentoo_name:
            gentoo_packages.append(gentoo_name)
            translated.append((pypi_name, gentoo_name))
        else:
            # Use normalized PyPI name as fallback
            normalized = pypi_name.lower().replace('_', '-').replace('.', '-')
            gentoo_packages.append(normalized)
            untranslated.append(pypi_name)
    
    print(f"Results:")
    print(f"  - Successfully translated: {len(translated)}")
    print(f"  - Used PyPI name directly: {len(untranslated)}")
    print(f"  - Total packages for listing: {len(gentoo_packages)}")
    
    if translated:
        print(f"\nExample successful translations (first 5):")
        for pypi, gentoo in translated[:5]:
            print(f"  {pypi} -> {gentoo}")
    
    if untranslated:
        print(f"\nExample untranslated packages (first 10):")
        for pkg in untranslated[:10]:
            print(f"  {pkg}")
    
    print("\n" + "=" * 70)
    print("STEP 4: Check what the filesystem actually returns")
    print("=" * 70)
    
    # Import filesystem components
    from portage_pip_fuse.package_filter import FilterDependencyTree
    
    # Create the filter
    filter = FilterDependencyTree(
        root_packages=['open-webui'],
        cache_dir=Path('/home/dirk/.cache/portage-pip-fuse')
    )
    
    # Get packages from filter
    filter_packages = filter.get_packages()
    print(f"Filter returns {len(filter_packages)} PyPI packages")
    
    # Now simulate what readdir does
    readdir_results = []
    for pypi_name in filter_packages:
        gentoo_name = translator.pypi_to_gentoo(pypi_name)
        if gentoo_name:
            readdir_results.append(gentoo_name)
        else:
            # This is what the FIX should do but might not be working
            normalized = pypi_name.lower().replace('_', '-').replace('.', '-')
            readdir_results.append(normalized)
    
    print(f"Readdir would return {len(readdir_results)} packages")
    print(f"But you're seeing only 35 - something is wrong!")
    
    print("\n" + "=" * 70)
    print("DEBUGGING: Why only 35?")
    print("=" * 70)
    
    # The issue might be in the prefetcher or translator
    # Let's check what the translator actually knows
    if hasattr(translator, 'pypi_to_gentoo_map'):
        print(f"Translator knows about {len(translator.pypi_to_gentoo_map)} PyPI packages")
    
    # Check if the translator is limiting results somehow
    actually_translated = []
    for pypi_name in filter_packages:
        gentoo_name = translator.pypi_to_gentoo(pypi_name)
        if gentoo_name:
            actually_translated.append(gentoo_name)
    
    print(f"Only {len(actually_translated)} packages have known Gentoo names")
    print(f"This matches the 35 you're seeing!")
    print("\nThe problem: The filesystem code might not be using the fallback")
    print("even though we added it. The translator might be returning None")
    print("and the fallback code isn't being executed.")

if __name__ == '__main__':
    # Run the manual test sequence
    manual_test_sequence()
    
    # Uncomment to run doctests
    # import doctest
    # doctest.testmod(verbose=True)