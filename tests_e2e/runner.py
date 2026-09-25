#!/usr/bin/env python
"""
Standalone E2E Test Runner for MainchApp Modernization.
Supports executing individual tiers (Tier 1 - Tier 4), specific features, or the full test suite.
Returns exit code 0 on complete pass, 1 on any failure.
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Setup paths and environment
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MainchApp.settings')

import django
django.setup()

from django.test.runner import DiscoverRunner


class MainchE2ERunner(DiscoverRunner):
    """Custom discover runner providing clean tier filtering and summary reporting."""
    pass


def main():
    parser = argparse.ArgumentParser(description="MainchApp 4-Tier E2E Test Suite Runner")
    parser.add_argument(
        '--tier',
        type=str,
        choices=['1', '2', '3', '4', 'all'],
        default='all',
        help="Test tier to execute: 1 (Features), 2 (Boundaries), 3 (Combinations), 4 (Scenarios), or all"
    )
    parser.add_argument(
        '--feature',
        type=str,
        default=None,
        help="Filter tests by specific feature ID (e.g., F01, F06, F19, F28)"
    )
    parser.add_argument(
        '--verbosity',
        type=int,
        default=2,
        help="Verbosity level (1=minimal, 2=standard, 3=detailed)"
    )
    parser.add_argument(
        '--failfast',
        action='store_true',
        help="Stop on first failure"
    )
    args = parser.parse_args()

    print("=" * 80)
    print(" MAINCHAPP E2E TEST SUITE RUNNER — 4-TIER SPECIFICATION ENGINE")
    print(f" Working Directory : {PROJECT_ROOT}")
    print(f" Target Tier       : {args.tier.upper()}")
    if args.feature:
        print(f" Target Feature    : {args.feature.upper()}")
    print("=" * 80)

    test_labels = []

    if args.feature:
        feat = args.feature.lower()
        # Search for test files matching feature pattern across tiers
        tier1_dir = PROJECT_ROOT / "tests_e2e" / "tier1_features"
        matching = list(tier1_dir.glob(f"test_{feat}_*.py"))
        if not matching:
            matching = list(tier1_dir.glob(f"*{feat}*.py"))
        for p in matching:
            mod_name = f"tests_e2e.tier1_features.{p.stem}"
            test_labels.append(mod_name)
        if not test_labels:
            print(f"Warning: No test files found for feature '{args.feature}'.")
            test_labels = [f"tests_e2e.tier1_features.test_{feat}"]
    elif args.tier == '1':
        test_labels = ['tests_e2e.tier1_features']
    elif args.tier == '2':
        test_labels = ['tests_e2e.tier2_boundaries']
    elif args.tier == '3':
        test_labels = ['tests_e2e.tier3_combinations']
    elif args.tier == '4':
        test_labels = ['tests_e2e.tier4_scenarios']
    else:  # all
        test_labels = ['tests_e2e']

    runner = MainchE2ERunner(
        verbosity=args.verbosity,
        interactive=False,
        failfast=args.failfast,
    )

    start_time = time.time()
    failures = runner.run_tests(test_labels)
    elapsed = time.time() - start_time

    print("\n" + "=" * 80)
    print(f" RUN FINISHED IN {elapsed:.2f}s")
    if failures:
        print(f" RESULT: FAILED ({failures} test failures/errors)")
        print("=" * 80)
        sys.exit(1)
    else:
        print(" RESULT: PASSED (All tests passed cleanly)")
        print("=" * 80)
        sys.exit(0)


if __name__ == '__main__':
    main()
