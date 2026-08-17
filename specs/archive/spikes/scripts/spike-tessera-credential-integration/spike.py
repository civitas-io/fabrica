"""Spike: does Fabrica's real MCPClient/MCPToolNamespace/ToolManager stack
compose with a real, unmodified `tsr mcp` process such that model-generated
code running inside a real SubprocessSandbox can use a Tessera-managed
secret without ever seeing it -- with zero new Fabrica code?

Requires a built tsr binary (see ../../../../../tessera repo, `cargo build
--release`) and a store set up via setup_store.sh in this directory first.
Run from a fabrica checkout with `src` on the path, e.g.:

    TSR_BINARY=/path/to/tsr TSR_STORE_DIR=/tmp/tsr-fabrica-demo \\
        TSR_PASSPHRASE=... python3 spike.py
"""

from __future__ import annotations

import asyncio
import os

from fabrica.managers import ToolManager
from fabrica.mcp import MCPClient, MCPServerConfig, MCPToolNamespace
from fabrica.presidium import GrantResult
from fabrica.retriever import KeywordBackend, Retriever
from fabrica.sandbox import SandboxPool, SubprocessSandbox
from fabrica.scope import Scope

TSR_BINARY = os.environ.get(
    "TSR_BINARY", os.path.expanduser("~/workspace/projects/tessera/target/release/tsr")
)
TSR_STORE_DIR = os.environ.get("TSR_STORE_DIR", "/tmp/tsr-fabrica-demo")
TSR_PASSPHRASE = os.environ.get("TSR_PASSPHRASE", "demo-passphrase-for-integration-test")


class _AllowClient:
    async def check_grant(self, *, agent_id: str, action: str, scope: Scope) -> GrantResult:
        return GrantResult(decision="allow")


async def main() -> None:
    config = MCPServerConfig(
        name="tessera",
        transport="stdio",
        command=TSR_BINARY,
        args=["mcp"],
        env={"TSR_STORE_DIR": TSR_STORE_DIR, "TSR_PASSPHRASE": TSR_PASSPHRASE},
    )
    client = MCPClient(config)
    await client.connect()
    namespace = await MCPToolNamespace.create(client)
    print("=== real schemas from a real tsr mcp process, via Fabrica's real MCPClient ===")
    for schema in namespace.list_schemas():
        print(f"  {schema.name}: {schema.description[:70]}...")

    retriever = Retriever(primary=KeywordBackend())
    sandbox_pool = SandboxPool(SubprocessSandbox(), warm_size=0, max_concurrent=5)
    tools = ToolManager(retriever, sandbox_pool, _AllowClient())
    await tools.register(namespace)

    # MODEL-GENERATED code, running inside a REAL subprocess sandbox. It
    # never sees TSR_PASSPHRASE, never sees the store path, never sees any
    # secret value -- only the tool NAME it calls and the redacted RESULT
    # that crosses back.
    code = (
        "result = namespace.call('ls', {})\n"
        "print(f\"secrets visible to the sandbox: {result['value']}\")\n"
    )
    result = await tools.run_code(code, agent_id="demo-agent", scope=Scope(agent_id="demo-agent"))
    print()
    print("=== code-mode result -- this is ALL that crossed back into the sandbox ===")
    print(f"success={result.success}")
    print(result.stdout)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
