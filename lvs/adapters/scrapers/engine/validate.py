"""CLI config validator — python -m engine.validate sites/XX_BOARD/config.yaml"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import SiteConfig


def load_config(config_path: str) -> SiteConfig:
    """Load and validate a board config.yaml. Raises ValidationError on schema violations."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return SiteConfig(**data)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m engine.validate <path/to/config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    print(f"Validating: {config_path}")
    try:
        config = load_config(config_path)
        print(f"  OK — source_id={config.identity.source_id}, archetype={config.identity.archetype}")
        print(f"       board={config.identity.board_name}")
        print(f"       url={config.identity.base_url}")
        if not config.smoke_test and not config.compliance.requires_captcha:
            print(f"  WARNING: no smoke_test block — add one before this board goes to production")
            print(f"           Example: smoke_test: {{mode: last_name, query: Smith, expect: {{min_records: 1}}}}")
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        sys.exit(2)
    except ValidationError as e:
        print(f"  VALIDATION FAILED:\n{e}")
        sys.exit(3)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(4)


if __name__ == "__main__":
    main()
