import json
import unittest
from dataclasses import FrozenInstanceError
from typing import MutableMapping, cast

from fixtures import ROOT
from pennilogic_contracts.categories import CATEGORY_DEFAULTS, ICON_CODE_POINTS, COLOUR_RGB, category_label
from pennilogic_contracts.models.category_system_key import CategorySystemKey
from pennilogic_contracts.models.category_icon import CategoryIcon
from pennilogic_contracts.models.category_colour import CategoryColour


class CategoryRegistryTest(unittest.TestCase):
    def test_every_accepted_key_default_parent_nature_registry_and_locale_fallback(self) -> None:
        seed = json.loads((ROOT / "spec" / "category-seed.v1.json").read_bytes())
        self.assertEqual(len(CATEGORY_DEFAULTS), 59)
        self.assertEqual({row.value for row in CategorySystemKey}, {row["key"] for row in seed["categories"]})
        for row in seed["categories"]:
            key = CategorySystemKey(row["key"])
            value = CATEGORY_DEFAULTS[key]
            self.assertEqual(value.parent_key.value if value.parent_key else None, row["parent_key"])
            self.assertEqual(value.nature.value, row["nature"])
            self.assertEqual(value.icon.value, row["icon"])
            self.assertEqual(value.colour.value, row["colour"])
            self.assertEqual(value.introduced_in, row["introduced_in"])
            self.assertEqual(value.retired_in, row["retired_in"])
            self.assertEqual(value.sort_order, row["sort_order"])
            self.assertEqual(category_label(key, "en-IN"), row["labels"]["en-IN"])
            self.assertEqual(category_label(key, "hi-IN"), row["labels"]["en-IN"])
            with self.assertRaises(FrozenInstanceError):
                setattr(value, "sort_order", 99)
            with self.assertRaises(TypeError):
                cast(MutableMapping[str, str], value.labels)["en-IN"] = "changed"
        self.assertEqual({key.value: value for key, value in ICON_CODE_POINTS.items()},
                         {row["id"]: row["code_point"] for row in seed["icons"]})
        self.assertEqual({key.value: list(value) for key, value in COLOUR_RGB.items()},
                         {row["id"]: row["rgb"] for row in seed["colours"]})
        for enum in (CategorySystemKey, CategoryIcon, CategoryColour):
            with self.assertRaises(ValueError):
                enum("SYNTHETIC_UNKNOWN")
