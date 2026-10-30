"""The safe YAML adapter for role manifests (P02-3).

Only `yaml.safe_load` is used. Any document it cannot read as plain data,
including a tagged payload such as `!!python/object`, is refused as
PolicyDenied before anything is constructed.
"""

import yaml

from hyper_agents.domain.errors import PolicyDenied


class SafeYamlParser:
    def parse(self, text: str) -> object:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            raise PolicyDenied("manifest is not plain YAML") from None
