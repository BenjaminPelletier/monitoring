from collections.abc import Iterable
from typing import TYPE_CHECKING, TypeVar, get_args, get_origin

from implicitdict import ImplicitDict

from monitoring.uss_qualifier.resources.resource import Resource

_concrete_plural_specifications: dict[type, type] = {}


class PluralResourceSpecification[TSingularResourceSpecification: ImplicitDict](
    ImplicitDict
):
    instances: list[TSingularResourceSpecification]

    if not TYPE_CHECKING:

        def __class_getitem__(cls, item):
            if isinstance(item, tuple) and len(item) == 1:
                item = item[0]
            if isinstance(item, TypeVar):
                return super().__class_getitem__(item)
            if item not in _concrete_plural_specifications:
                _concrete_plural_specifications[item] = type(
                    f"{cls.__name__}[{item.__name__}]",
                    (cls,),
                    {
                        "__annotations__": {"instances": list[item]},
                        "__module__": item.__module__,
                        "__firstlineno__": getattr(item, "__firstlineno__", 1),
                    },
                )
            return _concrete_plural_specifications[item]


class PluralResource[
    TSingularResource: Resource,
    TSingularResourceSpecification: ImplicitDict,
](Resource[PluralResourceSpecification[TSingularResourceSpecification]]):
    instances: list[TSingularResource]

    def __init__(
        self,
        specification: PluralResourceSpecification[TSingularResourceSpecification],
        resource_origin: str,
        **dependencies,
    ):
        super().__init__(specification, resource_origin, **dependencies)
        singular_resource_type = self.get_singular_resource_type()
        self.instances = [
            singular_resource_type(
                specification=s,
                resource_origin=f"instance {i + 1} in {resource_origin}",
                **dependencies,
            )
            for i, s in enumerate(specification.instances)
        ]

    @classmethod
    def get_singular_resource_type(cls) -> type[TSingularResource]:
        def walk(c: type, subst: dict) -> type[TSingularResource] | None:
            for base in getattr(c, "__orig_bases__", ()):
                origin = get_origin(base)
                if origin is None:
                    if isinstance(base, type) and issubclass(base, PluralResource):
                        result = walk(base, subst)
                        if result is not None:
                            return result
                    continue
                args = tuple(subst.get(a, a) for a in get_args(base))
                if origin is PluralResource:
                    if isinstance(args[0], TypeVar):
                        raise ValueError(
                            f"Could not resolve concrete singular resource type for {cls}"
                        )
                    return args[0]
                params = getattr(origin, "__parameters__", None)
                if params is not None:
                    result = walk(origin, dict(zip(params, args)))
                    if result is not None:
                        return result
            return None

        result = walk(cls, {})
        if result is None:
            raise ValueError(f"Could not resolve singular resource type for {cls}")
        return result

    def make_subset(self, select_indices: Iterable[int]) -> list[TSingularResource]:
        return [self.instances[i] for i in select_indices]
