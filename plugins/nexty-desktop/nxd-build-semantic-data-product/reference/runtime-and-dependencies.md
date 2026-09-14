# Runtime and dependencies

## Contents
- Runtime delivery
- Requirements
- Matched wheel versions
- Transform and output wiring
- Tool discovery
- Cross-DP lineage

---

## Runtime delivery

The installed `nxd.data_product` wheel provides `.semantic_tools()`, the semantic
compiler, dialect, and MCP tool factory. Authors use the public `nxd.spec` roles
in `models.py`; the platform compiles those roles into the semantic payload the
runtime serves. Do not copy the compiler or tool implementation into a data
product.

The runtime implementation remains in `nxd.experimental.semantic`, but it is not
the authoring surface. `models.py` imports public DSL builders such as `field`,
`primary_key`, `dimension`, `join`, `semantic_view`, `metric_field`, and `metric`.

---

## Requirements

```
nxd.data_product[spec]
nxd.drivers[rpc]
snowflake-connector-python[pandas]
pandas
pyyaml>=6.0.2
```

- `nxd.data_product[spec]` supplies the public model DSL and semantic runtime.
- `nxd.drivers[rpc]` supplies the RPC server pieces used by the auto-generated
  tools.
- `snowflake-connector-python[pandas]` and `pandas` support the Snowflake
  transform and query path.
- `pyyaml>=6.0.2` supports the split-pod fallback that reconstructs semantic
  payloads from the bundled model definition when the MCP pod has no
  kernel-delivered payload.

---

## Matched wheel versions

`nxd_core`, `nxd_drivers`, and `nxd_data_product` must be the same, recent
version. Build and publish all three together when refreshing the pip registry.
A mismatched set can install successfully but fail while deserializing the
injected runtime context, for example with `missing field secret_password`.

The deployed kernel must also be recent enough to compile the public roles into
semantic payloads. A split-pod deployment can use the bundled-model fallback,
but it still needs the matching wheel set and `pyyaml`.

---

## Transform and output wiring

`.semantic_tools()` creates no warehouse view. The transform creates and seeds
the physical base tables with `CREATE OR REPLACE TABLE` plus `write_pandas`.
Create those names unquoted so they resolve exactly as the compiler expects.
Write a marker row so storage produce-verification succeeds.

Promise physical bases only. Register every query-time metric view with
`.model(...)`; it is not a table and must not be promised.

```python
# spec.py
from nxd.spec import code, data_product, data_product_output, storage
from transform import transform
from models import order_metrics, orders, provision_marker

INFRA_PROFILE = "<infra-profile-name>"

_storage = (
    data_product_output()
    .promise(provision_marker)
    .promise(orders)
    .model(order_metrics)
    .port("snowflake", storage(f"/infra-profile/{INFRA_PROFILE}#/services/<snowflake>"))
)

spec = (
    data_product(name="...", infra_profile=INFRA_PROFILE)
    .transform(
        code(transform).compute(f"/infra-profile/{INFRA_PROFILE}#/services/<compute>")
    )
    .output(_storage)
    .semantic_tools(service="<mcp-service-name>")
)
```

The transform parameter name must match the storage port name (`snowflake`) and
be typed as `Snowflake`, not `Any`. `.startup_timeout(...)` is optional and only
needed when cold starts exceed the normal execution-start window.

---

## Tool discovery

`.semantic_tools(service=...)` is the only RPC declaration needed. It creates the
RPC output and the `list_models`, `semantic_model`, `describe_model`, and
`run_semantic_query` MCP tools. Do not add `data_product_rpc_output()` or invoke
`.semantic_tools()` twice; either conflicts with the generated RPC output.

At startup, the runtime loads kernel-delivered semantic payloads. In the
split-pod topology, where the MCP server has no kernel, it rebuilds equivalent
payloads from the bundled model definition. Both paths serve the same governed
tools at `/mcp` by default.

---

## Cross-DP lineage

Use the public `.referencing(...)` relationship API on a base-model field for a
cross-DP foreign key, and declare the upstream dependency with `.input(...)` in
`spec.py` before `.transform()`. The input creates the real lineage edge; the
field relationship makes the foreign key discoverable. Cross-DP joins resolve at
query time, so the transform seeds only this DP's own physical tables.
