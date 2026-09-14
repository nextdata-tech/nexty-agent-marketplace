# Nexty Desktop marketplace

This public repository contains the plugins that are intended for local Nexty
Desktop workflows in Claude Desktop and Cowork. It intentionally contains no
deployed DataMesh skills.

Claude Desktop can add this marketplace from its supported Git host once the
repository is published:

```text
nextdata-tech/nexty-agent-marketplace
```

The contents are synchronized from the private `nexty-agent-skills` source
repository by `.github/workflows/sync.yml`. The workflow copies only the
Desktop skill directories listed in `.claude-plugin/marketplace.json`, then
commits changes to this public repository.

The sync workflow needs a repository secret named
`NEXTY_AGENT_SKILLS_TOKEN`. It must be able to read
`nextdata-tech/nexty-agent-skills`.
