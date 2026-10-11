# Configuration migration fixtures

`legacy-0.1-defaults.toml` is the unmodified packaged defaults at
`acb2ea3e31d7424acd123db3909e634a75b04057`. It is a test oracle, never a
production configuration source. The remaining fixtures represent old local
overrides; all credentials are inert test strings.

The intentional default change is provider neutralization: new installations
have no model name, endpoint or credential reference until Setup. Historical
installations retain their effective role configuration through a one-time
migration. Other defaults and validation constraints remain unchanged.
