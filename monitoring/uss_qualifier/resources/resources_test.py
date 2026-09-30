import unittest

import pytest

from monitoring.uss_qualifier.resources.definitions import (
    ResourceDeclaration,
    ResourceID,
)
from monitoring.uss_qualifier.resources.dev.test_modifier import (
    NumberGeneratorModifierSpecification,
    NumberGeneratorModifiersResource,
    NumberGeneratorSpecification,
    NumberGeneratorsResource,
)
from monitoring.uss_qualifier.resources.plural_resource import (
    PluralResourceSpecification,
)
from monitoring.uss_qualifier.resources.resource import (
    SupportedKeysNotSpecifiedError,
    create_resources,
)
from monitoring.uss_qualifier.validation import validate_resource_declarations


class TestModifierResource(unittest.TestCase):
    def _build_number_generator_declaration(
        self, base_id
    ) -> dict[ResourceID, ResourceDeclaration]:
        return {
            "number_generator": ResourceDeclaration(
                resource_type="resources.dev.NumberGeneratorResource",
                specification=NumberGeneratorSpecification(base_id=base_id),
            )
        }

    def _build_modifier_declaration(
        self, base_id, shift_interval
    ) -> dict[ResourceID, ResourceDeclaration]:
        return {
            "number_generator": self._build_number_generator_declaration(base_id)[
                "number_generator"
            ],
            "modifier": ResourceDeclaration(
                resource_type="resources.dev.NumberGeneratorModifierResource",
                specification=NumberGeneratorModifierSpecification(
                    shift_interval=shift_interval
                ),
                dependencies={
                    "base_resource": "number_generator",
                },
            ),
        }

    def test_base_resource(self):
        """Test basic usage of the resource"""
        declaration = self._build_number_generator_declaration(42)

        resources = create_resources(declaration, "unittest", True)
        assert "number_generator" in resources

        resource = resources["number_generator"]

        assert resource.build_ids() == [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]

    def test_base_resource_base_id(self):
        """Test that base id works as expected"""

        declaration = self._build_number_generator_declaration(52)

        resources = create_resources(declaration, "unittest", True)
        assert "number_generator" in resources

        resource = resources["number_generator"]

        assert resource.build_ids() == [52, 53, 54, 55, 56, 57, 58, 59, 60, 61]

    def test_modifier_resource(self):
        """Test basic usage of the resource modifier resource"""
        declaration = self._build_modifier_declaration(42, 10)

        resources = create_resources(declaration, "unittest", True)
        assert "modifier" in resources

        resource = resources["modifier"]

        with pytest.raises(SupportedKeysNotSpecifiedError):
            resource.provide_resource_for(key=0)

        with pytest.raises(SupportedKeysNotSpecifiedError):
            resource.provide_resource_for(index="foo")

        assert resource.provide_resource_for(index=0).build_ids() == [
            42,
            43,
            44,
            45,
            46,
            47,
            48,
            49,
            50,
            51,
        ]
        assert resource.provide_resource_for(index=1).build_ids() == [
            52,
            53,
            54,
            55,
            56,
            57,
            58,
            59,
            60,
            61,
        ]

    def test_modifier_resource_shift(self):
        """Test shift usage of the resource modifier"""
        declaration = self._build_modifier_declaration(42, 20)

        resources = create_resources(declaration, "unittest", True)
        assert "modifier" in resources

        resource = resources["modifier"]

        assert resource.provide_resource_for(index=0).build_ids() == [
            42,
            43,
            44,
            45,
            46,
            47,
            48,
            49,
            50,
            51,
        ]
        assert resource.provide_resource_for(index=1).build_ids() == [
            62,
            63,
            64,
            65,
            66,
            67,
            68,
            69,
            70,
            71,
        ]


class TestPluralResource(unittest.TestCase):
    def test_plural_resource(self):
        declarations: dict[ResourceID, ResourceDeclaration] = {
            "generators": ResourceDeclaration(
                resource_type="resources.dev.NumberGeneratorsResource",
                specification=PluralResourceSpecification[NumberGeneratorSpecification](
                    instances=[
                        NumberGeneratorSpecification(base_id=10),
                        NumberGeneratorSpecification(base_id=20),
                        NumberGeneratorSpecification(base_id=30),
                    ]
                ),
            )
        }
        resources = create_resources(declarations, "unittest", True)
        assert "generators" in resources
        plural = resources["generators"]
        assert isinstance(plural, NumberGeneratorsResource)
        assert len(plural.instances) == 3
        assert plural.instances[0].build_ids()[0] == 10
        assert plural.instances[1].build_ids()[0] == 20
        assert plural.instances[2].build_ids()[0] == 30
        subset = plural.make_subset([2, 0])
        assert [r.build_ids()[0] for r in subset] == [30, 10]

    def test_plural_resource_with_dependencies_and_validation(self):
        raw_config = {
            "declarations": {
                "base": {
                    "resource_type": "resources.dev.NumberGeneratorResource",
                    "specification": {"base_id": 100},
                },
                "modifiers": {
                    "resource_type": "resources.dev.NumberGeneratorModifiersResource",
                    "specification": {
                        "instances": [
                            {"shift_interval": 5},
                            {"shift_interval": 15},
                        ]
                    },
                    "dependencies": {"base_resource": "base"},
                },
            }
        }
        assert validate_resource_declarations(raw_config, "$.declarations") == []

        declarations = {
            k: ResourceDeclaration(**v) for k, v in raw_config["declarations"].items()
        }
        resources = create_resources(declarations, "unittest", True)
        plural = resources["modifiers"]
        assert isinstance(plural, NumberGeneratorModifiersResource)
        assert len(plural.instances) == 2
        assert plural.instances[0].provide_resource_for(index=1).build_ids()[0] == 105
        assert plural.instances[1].provide_resource_for(index=1).build_ids()[0] == 115

        # Missing dependency should fail validation
        bad_dep_config = {
            "declarations": {
                "modifiers": {
                    "resource_type": "resources.dev.NumberGeneratorModifiersResource",
                    "specification": {"instances": [{"shift_interval": 5}]},
                }
            }
        }
        assert (
            len(validate_resource_declarations(bad_dep_config, "$.declarations")) == 1
        )
