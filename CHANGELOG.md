# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.4.x] — 2026-05/06

### Added
- **Object-level RBAC** across 7 object types (customer / section / subnet / IP /
  device / rack / location) with hierarchical cascade, per-type "All" wildcard,
  and 5 built-in roles (System Administrator, Read-only Viewer, Network
  Operator, Auditor, Department Administrator). Visibility is enforced on list
  endpoints, global search, the topology graph, and every selector.
- **Permission management UI** — principal (user/group) picker, grant table, and
  add-grant flow with "All"/specific multi-select and read/write/admin levels.
- **MCP server** — expanded toolset with both stdio and Streamable HTTP
  transports; mounted under `/api/mcp` so it is reachable through the nginx
  reverse proxy. Write tools self-gate on admin.
- **Customers** (managing units) attached to sections/subnets/devices/IPs, and an
  IEEE **OUI vendor** table with a monthly refresh timer.
- **AI chat** improvements — persistent history, per-message timestamps, model &
  elapsed-time display, and a model-parameters tooltip (family / parameter size /
  quantization / context length via Ollama `/api/show`).
- **Global search** now covers VPN, customers, racks, locations, NAT, DNS
  records, firewalls, and IP requests — all RBAC-filtered.
- Floating sticky horizontal scrollbar on wide tables; premium light/dark theme;
  Cabling / Power / VPN split into three independent pages.

### Changed
- prod database migrated from `SQL_ASCII` to `UTF8`.
- Terminology fixes for Taiwanese usage (e.g. 首碼 instead of 前綴).

### Fixed
- Numerous QA-driven UI fixes (column widths, dashboard widget styling, text
  selection contrast in light mode, topology node detail popovers, tooltip
  clipping).

## [0.3] — Phase 1–3 baseline

- phpIPAM parity (Sections/Subnets/IPs/VLANs/VRFs/NAT/Devices/Racks/Locations/
  IP-Requests), TOTP + API tokens, forced TLS.
- Multi-vendor DNS, deep LibreNMS integration, anomaly detection, SHA-256 audit
  chain, pgvector semantic search.
- Tenancy/Cabling/Power/VPN/Virtualization, Proxmox sync, Cytoscape topology,
  OIDC/SAML SSO, OPNsense firewall sync, Wazuh agent inventory.
