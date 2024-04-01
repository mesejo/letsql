from typing import Any, Callable
from collections import Counter, defaultdict


class RefCountedNameTable:
    def __init__(
        self,
        *,
        generate_name: Callable[[], str],
        key: Callable[[Any], Any],
    ) -> None:
        # Somehow mypy needs a type hint here
        self.refs: Counter = Counter()
        # Somehow mypy needs a type hint here
        self.names: defaultdict = defaultdict(generate_name)
        self.key = key or (lambda x: x)

    def name(self, value):
        name = self.names[self.key(value)]
        self.refs[name] += 1
        return name
