

class Renderer:
    context = {}

    async def render(self, msg: str): ...

    async def result(self): ...

    async def __aenter__(self): ...

    async def __aexit__(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb): ...
