#!/usr/bin/env python3
"""Force update feature flags to enable optimization features.

This script updates the runtime_feature_flags table to enable:
- gateway_enabled: Routes /chat through RuntimeGateway
- router_llm_enabled: Enables RouterLLM pre-classification
- tool_dispatcher_enabled: Enables ToolDispatcher for result caching

Usage:
    python scripts/force_update_flags.py [--dry-run]

The --dry-run flag shows what would be changed without making changes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add server/src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main(dry_run: bool = False) -> None:
    from sqlalchemy import select, update
    from src.models.base import async_session_factory
    from src.models.runtime_flag import RuntimeFeatureFlag

    flags_to_enable = [
        "gateway_enabled",
        "router_llm_enabled",
        "tool_dispatcher_enabled",
    ]

    async with async_session_factory() as session:
        # First, show current state
        result = await session.execute(
            select(RuntimeFeatureFlag).where(
                RuntimeFeatureFlag.key.in_(flags_to_enable)
            )
        )
        current_flags = {f.key: f for f in result.scalars().all()}

        print("Current flag states:")
        print("-" * 60)
        for key in flags_to_enable:
            flag = current_flags.get(key)
            if flag:
                print(f"  {key}: enabled={flag.enabled}, rollout={flag.rollout_percent}%")
            else:
                print(f"  {key}: NOT FOUND (will be created)")
        print()

        if dry_run:
            print("DRY RUN - no changes made")
            print()
            print("To apply changes, run without --dry-run:")
            print("  python scripts/force_update_flags.py")
            return

        # Update existing flags
        for key in flags_to_enable:
            if key in current_flags:
                await session.execute(
                    update(RuntimeFeatureFlag)
                    .where(RuntimeFeatureFlag.key == key)
                    .values(enabled=True, rollout_percent=100)
                )
                print(f"Updated {key}: enabled=True, rollout_percent=100")
            else:
                # Create new flag
                new_flag = RuntimeFeatureFlag(
                    key=key,
                    enabled=True,
                    rollout_percent=100,
                    data={"description": f"Auto-created by force_update_flags.py"},
                )
                session.add(new_flag)
                print(f"Created {key}: enabled=True, rollout_percent=100")

        await session.commit()
        print()
        print("✓ All flags updated successfully!")
        print()
        print("IMPORTANT: Restart the server for changes to take effect.")
        print("The feature flag service refreshes every 10 seconds, but a restart")
        print("ensures all components pick up the new values immediately.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
