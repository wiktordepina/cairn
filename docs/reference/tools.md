# Tools — API reference

The public surface of the tool system: the `@tool` decorator for authoring
tools, the registry and runner that dispatch them, the approval and content
transformation seams, and the error hierarchy tools raise.

For narrative context — risk tiers, the sandbox, SSRF defence — see
[Tools](../tools.md).

## Authoring

::: cairn.tools.tool

## Registry and runner

::: cairn.tools.DefaultToolRegistry

::: cairn.tools.DefaultToolRunner

## Approval gates

::: cairn.tools.AutoApproveReadOnly

::: cairn.tools.SessionAllowlist

::: cairn.tools.TierGate

## Content transformers

::: cairn.tools.InvisibleUnicodeStripper

::: cairn.tools.SecretRedactor

::: cairn.tools.SpotlightTransformer

::: cairn.tools.redact_secrets

::: cairn.tools.strip_invisible_unicode

## Errors

::: cairn.tools.ToolError

::: cairn.tools.ToolRetry

::: cairn.tools.ToolTimeout

::: cairn.tools.PathEscape

::: cairn.tools.SSRFBlocked
