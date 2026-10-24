# Protocol and data sources

Research date: 2026-10-24. These sources establish protocol/data starting points;
none establishes successful operation on this user's actual car.

## MHS2 research catalog

The separate [research library](../audi_workbench/data/mhs2_library.json) contains
**54 entries, 38 source records and 13 categories**. Of these entries, 35
explicitly name MHS2; the rest preserve generic, inferred-family or incompatible
evidence. Status counts are 8 documented observations/rules, 33 reported claims,
6 conflicts/ambiguities and 7 out-of-scope records. There are **zero target-verified
features and zero writable library entries**. Counts describe review inputs,
not independent confirmations or a complete factory dataset.

Claim-level references include original Digital Eliteboard MHS2 discussions,
A4 Klub owner reports and scans, AudiWorld and Audi-Sport owner reports,
CarTechnology audio cases, mr-fix creator descriptions, Ross-Tech documentation,
Audi manufacturer bulletins and original project documentation. See each
source's `access`, identity evidence, notes and independence information.

The catalog preserves these distinctions:

- A public `8W0 035 193 G / MU0191` scan and a separate `P0470 / MU0191` report
  support identity research; they are not one complete adaptation map.
- USB byte-19 descriptions leave the complete encoding unresolved. Setting
  bit 7 and requiring bits 6–7 can agree when bit 6 was already set.
- Named Apple_DIO/Google_GAL/rHMI settings have hardware and activation
  dependencies; successful USB audio or an accepted setting is not ASI proof.
- A reported dashboard group `0B5A` has no verified individual field codec.
  Dataset locations such as `0x003000` remain dataset locations.
- Camera reports include unchanged defaults; sound and display reports include
  failures. Configuration-dependent outcomes are retained.
- MHI2 toolboxes, MH2P/MIB3 RS Monitor and legacy 3G/RMC channels are explicitly
  separated from MHS2 candidates.

Some AudiWorld/CarTechnology/Ross-Tech material was available only as indexed
excerpts; blocked maps were not reconstructed. YouTube descriptions were read,
not the video footage. The historical a4b9.ro translated PDF now redirects to
unrelated content and is retained only as a failed research lead. It is not
primary support or a download recommendation.

The named adaptation CSV comparison is documented in [LIBRARY.md](LIBRARY.md).
The complete follow-up audit instructions are in [CODEX_VERIFY.md](CODEX_VERIFY.md).

## Protocol and transport references

| Decision | Source | Scope and limitation |
|---|---|---|
| 5F 773/7DD, 19 710/77A | https://github.com/ConnorHowell/vag-uds-ids | Author-extracted ODIS CAN ID table; confirm installed ECU by live identity |
| VAG identification and coding DID0600 | https://github.com/bri3d/VW_Flash/blob/293f46d782bae2ced930497889b71eb782f122cb/lib/constants.py | Named diagnostic records pinned to the reviewed source revision; availability varies by ECU |
| Optional identifier catalog and text/raw decoding | https://github.com/bri3d/VW_Flash/blob/293f46d782bae2ced930497889b71eb782f122cb/lib/flash_uds.py | Source codec selection maps parse type 0 to ASCII and other selected records to raw hex; does not prove MHS2 support |
| ELM CAF1, AL, H1, FC setup | https://www.elmelectronics.com/wp-content/uploads/2016/07/ELM327DS.pdf | Official ELM327DSJ; clones may lack commands; AL is not long-request segmentation |
| BLE/macOS connection behavior | https://bleak.readthedocs.io/en/latest/backends/macos.html | Per-host UUIDs, permissions, CoreBluetooth behavior |
| BLEClient, GATT writes/notifications | https://bleak.readthedocs.io/en/latest/api/client.html | Use discovered BLEDevice and actual characteristic capabilities |
| Vgate GATT candidate18F0/2AF0/2AF1 | https://github.com/cheeseprince/obd-gauge-cluster/blob/main/docs/ADAPTERS.md | First-party project hardware observations; not manufacturer guarantee for every 2S |
| Existing 2S use on VAG | https://github.com/JMuffin/OBD2bridge | Author tested 2S on A3/Q2; not B9 coding validation |
| UDS channel dictionaries | https://wiki.ross-tech.com/wiki/index.php/Control_Module_Maps | UDS interpretation needs matching descriptions; raw reads do not enumerate semantics |
| Restore uses raw adaptation values | https://www.ross-tech.com/vcds/tour/controller-channels-map.php | An ordinary human-readable report is not a universal restore image |
| Security access | https://www.ross-tech.com/vcds/tour/securityaccess.php | Some operations require access; no generic seed/key algorithm inferred |
| ODX describes ECU diagnostics/variant coding | https://www.asam.net/standards/detail/mcd-2-d/ | Useful future import family; actual definitions must be obtained separately |
| 0600 writer example | https://github.com/commaai/openpilot/blob/master/tools/scripts/car/vw_mqb_config.py | Targets MQB steering; its masks/security must not be copied to MHS2 |
| Extracting diagnostic definitions | https://github.com/kartoffelpflanze/ODIS-project-explorer | Research reference; no proprietary ODIS datasets bundled |
| Audi smartphone interface activation keys | https://static.nhtsa.gov/odi/tsbs/2016/SB-10094048-0699.pdf | Audi bulletin for affected model years; coding alone does not establish activation |

The eight optional catalog additions are `F18C`, `F17C`, `F186`, `F1AA`,
`F1F4`, `F1AB`, `F17E` and `F1DF`. Their names and text/raw handling were
checked in the pinned VW_Flash source. They remain read-only and have
`read_by_default: false`; existing default captures do not expand.

## User-specific evidence

The earlier `audi_mhs2_ble_probe.py` was inspected. It was itself marked untested
and probed DID22AD. Its GATT candidates and TX773/RX7DD informed continuity.
The new transport adds explicit FC addressing, strict reassembly, connection
poisoning and capability enforcement. The earlier script is not treated as
proof of Apple_DIO layout or successful CarPlay activation.

## Known uncertainty

- No physical 2S or MHS2 access was available during this build.
- No raw byte/bit map for Apple_DIO was verified for the user's exact ECU.
- Outgoing long messages and access-protected writes remain separate work;
  default or extended-session unprotected writes still require exact reviewed
  definitions. No outgoing long-message support is inferred from the incoming
  ISO-TP parser.
- A raw configuration snapshot does not contain firmware, EEPROM, certificates,
  licenses or all undocumented adaptation records.
