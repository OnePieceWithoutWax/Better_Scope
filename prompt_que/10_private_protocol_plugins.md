# 10 -- Private protocol plugin package (AMD SVI3 stub)

Read `prompt_que/README.md` first. Requires prompt 01 (registry and API).

## Goal

Show and document how proprietary decoders (AMD SVI3 first) live **outside**
this public repo and load into Better_Scope, with no fork needed.

## Public repo work (this repo)
- `docs/DECODERS.md` gains a "Private / proprietary decoders" section covering
  the three discovery routes from prompt 01:
  1. Single file in `~/.better_scope/plugins/` or a folder listed in
     `BETTER_SCOPE_PLUGIN_PATH` (simplest; good for one-off NDA decoders on
     one machine).
  2. A private pip package exposing entry points in group
     `better_scope.decoders` (and `better_scope.regmap_importers`).
  3. A fork. It's rarely needed, so document it as a last resort.
- **uv gotcha to document:** a package installed with `uv pip install -e` is
  removed by the next plain `uv sync`, because it isn't in `uv.lock`. Options:
  `uv sync --inexact`, `uv run --with <path-or-git-url> better-scope`, or the
  plugin-folder route. Recommend one and explain it. Don't add the private
  package to this repo's `pyproject.toml`.
- Add a tiny example plugin in `docs/examples/example_plugin/` (a public,
  trivial protocol) that CI/tests load through the entry-point route.

## Private package template (create outside this repo; ask the user where)
Suggested: `../better-scope-private-decoders/`:
```
pyproject.toml     # [project.entry-points."better_scope.decoders"] svi3 = "bs_private.svi3:SVI3Decoder"
src/bs_private/svi3.py
tests/
README.md          # licensing / NDA handling note
```
- `SVI3Decoder` **stub**: roles `svc` (clock), `svd` (data), and `svt`
  (telemetry). Frame layout, options, and commands are left as clearly marked
  placeholders to fill from the AMD SVI3 spec the user holds. **Don't
  guess the protocol from public sources.** Only the public fact is used:
  3-wire SVC/SVD/SVT, I2C-like, CPU as master.
- The package depends on `better-scope` for the API. Pin to the version that
  ships prompt 01's API, and keep the API stable (mention semver in DECODERS.md).
- Initialise it as its own git repo. Ask before creating any remote.

## Tests
- Public repo: the example plugin is discovered through entry points and through
  the plugin folder, and a plugin with a newer id overrides the built-in with a
  warning.
- Private repo: the stub registers and appears in the registry list without
  errors.
