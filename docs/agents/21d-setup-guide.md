<!-- AGENTS.md, piece 21d-setup-guide. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 21d. The setup guide (`/setup`, `kazma-ui/kazma_ui/setup_api.py`)

The owner, after a clean Windows install (2026-11-07): "why there is no Web
UI setup guide". The first visit had one card (a provider list, a key, a
model name to type), and on a fresh install it never showed:
`compute_setup_status` counted any LISTED local provider as set up, and every
install lists Ollama and LM Studio.
- **Ready means a model Kazma can use**: the active provider has a key or is
  a local server, or another provider has a key (Kazma falls back to it, and
  says so). A listed local server counts only as the provider in use.
- **The guide** (`templates/setup.html`, `static/js/setup_guide.js`, catalog
  slice `setup.*`, EN/AR): welcome (the first sentence of What Kazma is, the
  language), the model, optional extras, done. The installer's last step
  opens it (`kazma_cli.install._finish`); the chat page's card, Settings ->
  Providers & Connectors and the page search open it.
- **Choosing the model**: a cloud provider whose key is all it needs
  (`guided`; `custom`, Azure and Bedrock are Settings', `_NOT_GUIDED`), a
  local server (`/api/setup/local`: loopback `/models`, no proxy, 2 s, nothing
  saved), or a server on another computer of the network
  (`/api/setup/local/address`: only an address on the owner's network,
  `ssrf.resolves_to_own_network`; saved as `server-<host>-<port>`, no key).
  Nothing is saved before the model answered (providers stage 1, §60):
  `/api/setup/models` lists the models with the key as typed and
  `/api/setup/check` sends the chosen model one message through the client
  chat would build (`kazma_core.provider_check`), then `/api/setup/bootstrap`
  saves the provider (an empty key keeps the stored one; an address set in
  Settings stays) and switches model AND provider (§1). Errors are codes the
  page words (`setup.err.*`), never the provider's reply text.
- **The models found are a list on screen** (2026-11-19): a `<select>`
  (`#setup-model`, values `m:<model>` so no model name is taken for "Another
  model") with "Another model" for a typed name (`#setup-model-name`,
  `pickModel`); Settings -> X's model the same (`xReplyPickModel`). They were
  a `<datalist>`, which shows nothing until the box is clicked and then only
  the names matching what it holds: the owner's fresh install said "3 models
  found. Pick one from the list" over no list. No page holds a datalist
  (`tests/test_no_hidden_lists.py`, a planted one as the control).
- Everything but `/api/setup/status` is admin (`platform_rbac._ADMIN_PREFIXES`).
- **A switch ends a stale fallback notice**
  (`model_fallback.report_provider_chosen`, from both switches in
  `runtime/model_switch`): saving a provider before switching let Kazma fall
  back from the shipped placeholder, and that notice closed only when the
  placeholder got a key.
- **Settings -> Packages reads the install's record**
  (`install_components.install_overview`, `annotate_extras`): the mode and
  date, an extra of a part left out "Not chosen" (never Missing or Partial),
  and `kazma setup install --with <part>` as each extra's command. **A part
  the last run could not put in place is said** (2026-11-19: the owner's
  fresh Full install had no Docker, SearXNG, Neo4j or document worker, and
  every row was green -- the page listed Python extras, and Neo4j's driver
  was installed): the route's install data names them (`not_in_place`) in a
  warning (`#pkg-not-in-place`), and an extra whose packages are here while
  its part is not (`part_missing`) is a warning badge, never "Installed"
  (`pkgExtraBadge`). Only a recorded `ok:
  false` counts; a record that says nothing of a part is no failure.
Gates: `tests/test_setup_guide.py` (each with the old behaviour as its
control), `tests/e2e/test_setup_guide_browser.py` (a fresh install's chat
card to a working model on a stand-in Ollama, three models listed, one
picked, then "Another model" typed; Settings to the guide in Arabic),
`tests/js/test_x_reply_model_picker.js`, `tests/js/test_packages_not_in_place.js`,
`tests/e2e/test_packages_not_in_place_browser.py` (EN/AR, by the sidebar).

